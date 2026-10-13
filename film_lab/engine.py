"""Headless RAW and developed-photo pipeline shared by the desktop app and CLI.

Source files are opened for reading only. Work arrays and previews live in the
bounded cache; exports are verified before an atomic, non-overwriting commit.
"""
from __future__ import annotations

import copy
import csv
from collections import OrderedDict
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
import time
import uuid

import numpy as np
from PIL import Image, ImageCms, TiffImagePlugin

from .cache import DiskCache, cache_key
from .models import ENGINE_VERSION, validate_settings, validate_export_options, sha256_file, atomic_json
from .formats import RAW_EXTENSIONS, RASTER_EXTENSIONS, SUPPORTED_EXTENSIONS

CHUNK_ROWS = 128
SUPPORTED_MODEL = "ILCE-6400"
STYLE_CODES = {"baseline": "BASE", "documentary": "DOC",
               "negative": "NEG", "cinema": "CIN",
               "vivid": "VIV", "monochrome": "MONO"}
DEFAULT_WB = [2.0, 1.0, 1.5, 1.0]
_INSPECTION_CACHE = OrderedDict()


class CancelledError(RuntimeError):
    pass


def _check_cancel(cancel=None):
    if cancel and ((callable(cancel) and cancel()) or
                   (not callable(cancel) and Path(cancel).exists())):
        raise CancelledError("작업이 취소되었습니다.")


def _rawpy():
    try:
        import rawpy
        return rawpy
    except ImportError as exc:
        raise RuntimeError("RAW 엔진이 설치되지 않았습니다. requirements.txt의 rawpy를 설치하세요.") from exc


def _versions(include_raw=True):
    versions = {}
    names = ("rawpy", "numpy", "Pillow", "tifffile", "ExifRead") if include_raw else ("numpy", "Pillow", "tifffile")
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    if include_raw:
        versions["LibRaw"] = ".".join(str(v) for v in _rawpy().libraw_version)
    else:
        from PIL import features
        versions["LittleCMS"] = features.version("littlecms2")
    return versions


def _tag_value(tags, *names):
    for name in names:
        tag = tags.get(name)
        if tag is not None:
            vals = getattr(tag, "values", None)
            if isinstance(vals, list) and vals:
                return vals[0]
            return str(tag).strip("\x00 ")
    return None


def _ratio(value):
    if value is None:
        return None
    try:
        fraction = Fraction(str(value)).limit_denominator(1000000)
        return [fraction.numerator, fraction.denominator]
    except (ValueError, ZeroDivisionError):
        return None


def _metadata(path):
    try:
        import exifread
    except ImportError as exc:
        raise RuntimeError("카메라 모델 검사에 필요한 ExifRead가 설치되지 않았습니다.") from exc
    with path.open("rb") as stream:
        tags = exifread.process_file(stream, details=False, strict=False)
    iso = _tag_value(tags, "EXIF ISOSpeedRatings", "EXIF PhotographicSensitivity")
    try:
        iso = int(iso) if iso is not None else None
    except (TypeError, ValueError):
        iso = None
    return {"camera_model": str(_tag_value(tags, "Image Model") or ""),
            "make": str(_tag_value(tags, "Image Make") or ""),
            "captured_at": _tag_value(tags, "EXIF DateTimeOriginal", "Image DateTime"),
            "exposure_time": _ratio(_tag_value(tags, "EXIF ExposureTime")),
            "f_number": _ratio(_tag_value(tags, "EXIF FNumber")),
            "iso": iso, "focal_length": _ratio(_tag_value(tags, "EXIF FocalLength"))}


def inspect(source: str) -> dict:
    """Validate decoded content and return an immutable fingerprint and safe EXIF."""
    path = Path(source).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("Sony ILCE-6400 ARW 또는 JPEG, PNG, TIFF, WebP, BMP 이미지를 선택하세요.")
    before = path.stat()
    fingerprint = sha256_file(path)
    identity = (str(path), before.st_size, before.st_mtime_ns, fingerprint)
    after_hash = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after_hash.st_size, after_hash.st_mtime_ns):
        raise ValueError("검사 중 원본 파일이 변경되었습니다. 다시 연결하세요.")
    if identity in _INSPECTION_CACHE:
        _INSPECTION_CACHE.move_to_end(identity)
        return copy.deepcopy(_INSPECTION_CACHE[identity])
    if path.suffix.lower() in RASTER_EXTENSIONS:
        from .raster import load_raster
        try:
            pixels, description = load_raster(path)
            del pixels
        except Exception as exc:
            raise ValueError(f"이미지 내용을 읽을 수 없습니다: {path.name}: {exc}") from exc
        after = path.stat()
        result = {"id": str(path), "path": str(path), "size_bytes": before.st_size,
                  "mtime_ns": before.st_mtime_ns, "sha256": fingerprint, **description}
        _assert_source_unchanged(result)
        _INSPECTION_CACHE[identity] = copy.deepcopy(result)
        while len(_INSPECTION_CACHE) > 128:
            _INSPECTION_CACHE.popitem(last=False)
        return result
    metadata = _metadata(path)
    supported = metadata["camera_model"].strip().upper() == SUPPORTED_MODEL
    warnings = []
    if not supported:
        warnings.append(f"지원하지 않는 카메라 모델: {metadata['camera_model'] or '모델 정보 없음'} (지원: Sony ILCE-6400)")
    rawpy = _rawpy()
    try:
        with rawpy.imread(str(path)) as raw:
            width, height = int(raw.sizes.width), int(raw.sizes.height)
            flip = int(raw.sizes.flip)
            if width <= 0 or height <= 0 or raw.raw_image_visible.size == 0:
                raise ValueError("RAW의 유효 이미지 영역이 없습니다.")
            if raw.num_colors != 3:
                supported = False
                warnings.append("지원하지 않는 RAW 센서 채널 구성입니다.")
    except Exception as exc:
        raise ValueError(f"RAW 내용을 읽을 수 없습니다: {path.name}: {exc}") from exc
    if flip in (5, 6):
        width, height = height, width
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("검사 중 원본 파일이 변경되었습니다. 다시 연결하세요.")
    result = {"id": str(path), "path": str(path), "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns, "sha256": fingerprint,
            "source_kind": "raw", "image_format": "ARW",
            "camera_model": metadata["camera_model"], "width": width, "height": height,
            "rotation": {0: 0, 3: 180, 5: 270, 6: 90}.get(flip, 0),
            "libraw_flip": flip, "metadata": metadata, "supported": supported, "warnings": warnings}
    _INSPECTION_CACHE[identity] = copy.deepcopy(result)
    while len(_INSPECTION_CACHE) > 128:
        _INSPECTION_CACHE.popitem(last=False)
    return result


def _check_source(path, recipe):
    source = inspect(str(path))
    if not source["supported"]:
        raise ValueError("; ".join(source["warnings"]))
    saved = recipe.get("source", {}) if isinstance(recipe, dict) else {}
    for field in ("size_bytes", "mtime_ns", "sha256"):
        if saved.get(field) is not None and saved[field] != source[field]:
            raise ValueError(f"원본 {field}가 저장된 레시피와 다릅니다. 원본을 다시 연결하세요.")
    return source


def _assert_source_unchanged(source):
    path = Path(source["path"])
    stat = path.stat()
    if ((stat.st_size, stat.st_mtime_ns) != (source["size_bytes"], source["mtime_ns"])
            or sha256_file(path) != source["sha256"]):
        raise ValueError("현상 중 원본이 변경되어 작업을 중단했습니다. 원본을 다시 연결하세요.")


def _settings(recipe):
    if not isinstance(recipe, dict):
        raise ValueError("레시피는 JSON 객체여야 합니다.")
    if "schema_version" in recipe and recipe["schema_version"] != 1:
        raise ValueError("지원하지 않는 레시피 스키마 버전입니다.")
    if "engine_version" in recipe and recipe["engine_version"] != ENGINE_VERSION:
        raise ValueError("지원하지 않는 처리 엔진 버전입니다.")
    return validate_settings(copy.deepcopy(recipe.get("settings", recipe)))


def _profile(settings, recipe):
    from .profiles import get_profile
    profile = get_profile(settings["style_id"])
    # get_profile returns manifest data, possibly wrapped by a profile object.
    if hasattr(profile, "manifest"):
        profile = profile.manifest
    if not isinstance(profile, dict):
        raise TypeError("스타일 manifest 데이터가 올바르지 않습니다.")
    if str(profile.get("version")) != str(settings["style_version"]):
        raise ValueError("설정의 스타일 버전과 설치된 스타일 버전이 다릅니다.")
    stored = recipe.get("profile", {})
    if stored.get("id") == settings["style_id"] and stored.get("sha256") and stored["sha256"] != profile.get("sha256"):
        raise ValueError("레시피의 스타일 해시와 설치된 스타일 데이터가 다릅니다.")
    return {k: profile[k] for k in ("id", "version", "sha256", "baseline_version") if k in profile}


def _white_balance(raw, settings, warnings):
    wb = list(raw.camera_whitebalance)
    valid = len(wb) == 4 and all(math.isfinite(float(v)) and float(v) > 0 for v in wb[:3])
    fallback = not valid
    if fallback:
        wb = DEFAULT_WB.copy()
        warnings.append("촬영 WB가 없어 명시적 기본 WB [2.0, 1.0, 1.5, 1.0]을 적용했습니다.")
    if not math.isfinite(float(wb[3])) or wb[3] <= 0:
        wb[3] = wb[1]
    base = [float(v) for v in wb]
    relative = settings["wb_mode"] == "relative" or settings["warmth"] != 0 or settings["tint"] != 0
    if relative:
        warm = settings["warmth"] / 100 * 0.45
        tint = settings["tint"] / 100 * 0.25
        wb = [wb[0] * 2**warm, wb[1] * 2**(-tint), wb[2] * 2**(-warm), wb[3] * 2**(-tint)]
    use_camera = not relative and not fallback
    return base, [float(v) for v in wb], use_camera, fallback


def _close_map(array):
    if isinstance(array, np.memmap) and not array._mmap.closed:
        try:
            array.flush()
        finally:
            array._mmap.close()


def _baseline(source, settings, cache, cancel):
    if source.get("source_kind", "raw") == "raster":
        return _raster_baseline(source, settings, cache, cancel)
    return _raw_baseline(source, settings, cache, cancel)


def _raster_baseline(source, settings, cache, cancel):
    from .color import BASELINE_VERSION
    from .raster import IMPORTER_VERSION, load_raster, adjust_developed
    versions = _versions(include_raw=False)
    identity = {"id": "raster_srgb_import", "version": IMPORTER_VERSION,
                "sha256": hashlib.sha256(b"raster-srgb-v1:ICC-or-sRGB;orientation;white-alpha;linear-exposure-WB;no-RAW-curve").hexdigest(),
                "baseline_version": BASELINE_VERSION}
    key = cache_key({"source_kind": "raster", "source": source["sha256"], "engine": ENGINE_VERSION,
                     "input_format": source.get("image_format"),
                     "raster_importer": identity, "libraries": versions,
                     "adjustments": {k: settings[k] for k in ("exposure_ev", "wb_mode", "warmth", "tint")}})
    array_path, info_path = cache.get(key), cache.get(key, ".json")
    data = None
    if array_path and info_path:
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if not isinstance(info, dict) or not {"image_processing", "libraries", "baseline_version", "baseline_profile", "warnings"}.issubset(info):
                raise ValueError("Invalid raster cache metadata")
            data = np.load(array_path, mmap_mode="r", allow_pickle=False)
            if data.dtype != np.float32 or data.ndim != 3 or data.shape[2] != 3:
                raise ValueError("Invalid raster cache")
            return data, info, key
        except (ValueError, OSError, EOFError, json.JSONDecodeError):
            _close_map(data)
    _check_cancel(cancel)
    pixels, description = load_raster(source["path"])
    _check_cancel(cancel)
    temporary = cache.temporary()
    result = None
    try:
        result = np.lib.format.open_memmap(temporary, mode="w+", dtype=np.float32, shape=pixels.shape)
        for top in range(0, pixels.shape[0], CHUNK_ROWS):
            _check_cancel(cancel)
            result[top:top + CHUNK_ROWS], multipliers = adjust_developed(pixels[top:top + CHUNK_ROWS], settings)
        _close_map(result)
        result = None
        processing = {"importer_version": IMPORTER_VERSION, "input_format": description["image_format"],
                      "input_bit_depth": description["bit_depth"], "input_icc_sha256": description["input_icc_sha256"],
                      "color_policy": description["color_policy"], "working_space": "encoded sRGB",
                      "working_white_point": "D65", "adjustment_space": "linear sRGB",
                      "exposure_ev": settings["exposure_ev"], "relative_wb_multipliers": multipliers,
                      "wb_reference": "already_rendered_pixels", "baseline_curve_applied": False,
                      "output_orientation": 1, "alpha_composited": description["alpha_composited"],
                      "alpha_composite_space": "encoded sRGB"}
        info = {"image_processing": processing, "libraries": versions, "baseline_version": BASELINE_VERSION,
                "baseline_profile": identity, "warnings": description["warnings"]}
        array_path = cache.commit(temporary, key)
        atomic_json(cache.path(key, ".json"), info)
        return np.load(array_path, mmap_mode="r", allow_pickle=False), info, key
    finally:
        _close_map(result)
        temporary.unlink(missing_ok=True)


def _raw_baseline(source, settings, cache, cancel):
    from .color import (baseline_render, BASELINE_VERSION, normalize_libraw_prophoto,
                        LIBRAW_PROPHOTO_NORMALIZATION_VERSION, LIBRAW_PROPHOTO_NORMALIZATION_SHA256)
    from .profiles import get_profile
    baseline_profile = get_profile("baseline")
    baseline_identity = {k: baseline_profile[k] for k in ("id", "version", "sha256", "baseline_version")}
    versions = _versions()
    key = cache_key({"source_kind": "raw", "source": source["sha256"], "engine": ENGINE_VERSION,
                     "baseline": baseline_identity, "libraries": versions,
                     "decoder_normalization": {"version": LIBRAW_PROPHOTO_NORMALIZATION_VERSION,
                                               "sha256": LIBRAW_PROPHOTO_NORMALIZATION_SHA256},
                     "raw": {k: settings[k] for k in ("exposure_ev", "wb_mode", "warmth", "tint")},
                     "half_size": False, "gamma": [1, 1], "output": "linear-LibRaw-ProPhoto-D65-16"})
    array_path, info_path = cache.get(key), cache.get(key, ".json")
    if array_path and info_path:
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            if (not isinstance(info, dict) or not {"raw_processing", "libraries", "baseline_version",
                                                   "baseline_profile", "warnings"}.issubset(info)):
                raise ValueError("Invalid baseline cache metadata")
            data = np.load(array_path, mmap_mode="r", allow_pickle=False)
            if data.dtype != np.float32 or data.ndim != 3 or data.shape[2] != 3:
                raise ValueError("Invalid baseline cache")
            return data, info, key
        except (ValueError, OSError, EOFError, json.JSONDecodeError):
            if "data" in locals():
                _close_map(data)
            pass
    _check_cancel(cancel)
    warnings = source["warnings"].copy()
    rawpy = _rawpy()
    with rawpy.imread(source["path"]) as raw:
        base_wb, user_wb, use_camera, fallback = _white_balance(raw, settings, warnings)
        white_levels = raw.camera_white_level_per_channel
        if not white_levels or any(v is None or v <= 0 for v in white_levels):
            white_levels = [int(raw.white_level)] * 4
        raw_view = raw.raw_image_visible
        colors = raw.raw_colors_visible
        saturated, sensor_count = 0, raw_view.size
        for y in range(0, raw_view.shape[0], CHUNK_ROWS):
            thresholds = np.asarray(white_levels)[colors[y:y + CHUNK_ROWS]]
            saturated += int(np.count_nonzero(raw_view[y:y + CHUNK_ROWS] >= thresholds))
        del colors, raw_view
        params = dict(output_color=rawpy.ColorSpace.ProPhoto, output_bps=16, gamma=(1, 1),
                      no_auto_bright=True, bright=1.0, use_camera_wb=use_camera,
                      use_auto_wb=False, exp_shift=2**settings["exposure_ev"], half_size=False,
                      demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                      highlight_mode=rawpy.HighlightMode.Clip, exp_preserve_highlights=0.0,
                      adjust_maximum_thr=0.0)
        if not use_camera:
            params["user_wb"] = user_wb
        _check_cancel(cancel)
        rgb16 = raw.postprocess(**params)
        actual_wb = getattr(raw, "auto_whitebalance", None)
        wb_report = "LibRaw pre_mul after postprocess" if actual_wb is not None else "explicit camera/user multipliers"
        actual_wb = [float(v) for v in actual_wb] if actual_wb is not None else user_wb
        raw_info = {"camera_wb": base_wb, "applied_wb": actual_wb,
                    "requested_wb": user_wb, "applied_wb_provenance": wb_report,
                    "wb_fallback": fallback, "use_camera_wb": use_camera,
                    "black_level_per_channel": [int(v) for v in raw.black_level_per_channel],
                    "white_level_per_channel": [int(v) for v in white_levels],
                    "sensor_saturation_fraction": saturated / max(sensor_count, 1),
                    "decoder_space": "linear LibRaw ProPhoto RGB",
                    "decoder_white_point": "D65 (adapted ProPhoto primaries)",
                    "decoder_normalization_version": LIBRAW_PROPHOTO_NORMALIZATION_VERSION,
                    "decoder_normalization_sha256": LIBRAW_PROPHOTO_NORMALIZATION_SHA256,
                    "working_space": "linear ProPhoto RGB", "working_white_point": "D50",
                    "output_color_space": "sRGB D65", "output_orientation": 1,
                    "demosaic": "AHD", "gamma": [1, 1], "half_size": False,
                    "adjust_maximum_thr": 0.0, "exp_shift": 2**settings["exposure_ev"]}
    _check_cancel(cancel)
    clipped = sum(int(np.count_nonzero(rgb16[y:y+CHUNK_ROWS] == 65535))
                  for y in range(0, rgb16.shape[0], CHUNK_ROWS))
    raw_info["developed_saturation_fraction"] = clipped / rgb16.size
    if saturated:
        warnings.append(f"센서 포화 픽셀 {100 * saturated / sensor_count:.3f}%: 기록되지 않은 밝은 정보는 복원되지 않습니다.")
    if clipped:
        warnings.append(f"16비트 현상 출력의 포화 채널 {100 * clipped / rgb16.size:.3f}% (센서 포화와 별도 검사).")
    temp = cache.temporary()
    result = None
    try:
        result = np.lib.format.open_memmap(temp, mode="w+", dtype=np.float32, shape=rgb16.shape)
        for y in range(0, rgb16.shape[0], CHUNK_ROWS):
            _check_cancel(cancel)
            block = rgb16[y:y+CHUNK_ROWS].astype(np.float32) / np.float32(65535)
            result[y:y+CHUNK_ROWS] = baseline_render(normalize_libraw_prophoto(block))
        result.flush()
        _close_map(result)
        result = None
        del rgb16
        array_path = cache.commit(temp, key)
        info = {"raw_processing": raw_info, "libraries": versions, "baseline_version": BASELINE_VERSION,
                "baseline_profile": baseline_identity,
                "warnings": warnings}
        atomic_json(cache.path(key, ".json"), info)
        return np.load(array_path, mmap_mode="r", allow_pickle=False), info, key
    finally:
        if result is not None:
            _close_map(result)
        temp.unlink(missing_ok=True)


def _processed(source, settings, cache, cancel, baseline_data=None):
    from .color import apply_adjustments
    from .profiles import apply_style
    baseline, info, key = baseline_data if baseline_data is not None else _baseline(source, settings, cache, cancel)
    work_path = cache.temporary()
    processed = None
    try:
        processed = np.lib.format.open_memmap(work_path, mode="w+", dtype=np.float32, shape=baseline.shape)
        for y in range(0, baseline.shape[0], CHUNK_ROWS):
            _check_cancel(cancel)
            block = np.asarray(baseline[y:y+CHUNK_ROWS])
            block = apply_style(block, settings["style_id"], settings["strength"])
            block = apply_adjustments(block, settings)
            if not np.isfinite(block).all():
                raise ValueError("색 처리에서 유효하지 않은 값이 발생했습니다.")
            processed[y:y+CHUNK_ROWS] = np.clip(block, 0, 1)
        processed.flush()
        return baseline, processed, info, key, work_path
    except BaseException:
        _close_map(baseline)
        if processed is not None:
            _close_map(processed)
        work_path.unlink(missing_ok=True)
        raise


def _final_rgb(rgb, settings, source_hash, long_edge, cancel):
    from .color import resize_rgb, apply_grain
    _check_cancel(cancel)
    original_size = (rgb.shape[1], rgb.shape[0])
    # Float resizing is shared by previews and exports; no 8-bit intermediate.
    resized = resize_rgb(rgb, long_edge) if long_edge else rgb
    if settings["grain"]["amount"]:
        if resized is rgb:
            grain_settings = {**settings, "_grain_scale": 1}
            for y in range(0, rgb.shape[0], CHUNK_ROWS):
                _check_cancel(cancel)
                rgb[y:y+CHUNK_ROWS] = apply_grain(rgb[y:y+CHUNK_ROWS], grain_settings, source_hash,
                                                original_size=original_size, origin=(0, y))
        else:
            resized = apply_grain(resized, settings, source_hash, original_size=original_size)
    return resized


def _resolved_recipe(source, settings, profile, info, export_options=None):
    recipe = {"schema_version": 1, "engine_version": ENGINE_VERSION,
              "source": copy.deepcopy(source), "profile": profile,
              "settings": settings,
              "libraries": info["libraries"], "baseline_version": info["baseline_version"],
              "baseline_profile": info["baseline_profile"]}
    processing_key = "image_processing" if source.get("source_kind", "raw") == "raster" else "raw_processing"
    recipe[processing_key] = info[processing_key]
    if export_options is not None:
        recipe["export"] = export_options
    return recipe


def _icc():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _png(path, rgb):
    Image.fromarray(np.floor(np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)).save(
        path, format="PNG", icc_profile=_icc())


def render(source_id: str, recipe: dict, quality="preview", request_id="", cache_dir=None, cancel_file=None) -> dict:
    """Render an immutable source; `source_id` is its absolute filesystem path."""
    start = time.monotonic()
    if quality not in ("preview", "full"):
        raise ValueError("quality must be preview or full")
    settings = _settings(recipe)
    profile = _profile(settings, recipe)
    _check_cancel(cancel_file)
    source = _check_source(source_id, recipe)
    cache = DiskCache(cache_dir)
    if cache.directory == Path(source["path"]).parent:
        raise ValueError("원본 보호를 위해 캐시는 원본 사진 폴더와 다른 폴더에 저장해야 합니다.")
    baseline = processed = None
    work_path = None
    protected = []
    try:
        baseline, info, baseline_key = _baseline(source, settings, cache, cancel_file)
        long_edge = 2048 if quality == "preview" else None
        render_key = cache_key({"source": source["sha256"], "settings": settings, "profile": profile,
                                "engine": ENGINE_VERSION, "baseline_key": baseline_key,
                                "preview_long_edge": long_edge})
        compare_key = cache_key({"baseline_key": baseline_key, "preview_long_edge": long_edge})
        result_path = cache.get(render_key, ".png")
        compare_path = cache.get(compare_key, ".png")
        if not result_path:
            baseline, processed, info, baseline_key, work_path = _processed(
                source, settings, cache, cancel_file, (baseline, info, baseline_key))
            rgb = _final_rgb(processed, settings, source["sha256"], long_edge, cancel_file)
            temp = cache.temporary(".png")
            try:
                _png(temp, rgb)
                result_path = cache.commit(temp, render_key, ".png")
            finally:
                temp.unlink(missing_ok=True)
            if rgb is not processed:
                del rgb
        if not compare_path:
            from .color import resize_rgb
            reference = resize_rgb(baseline, long_edge) if long_edge else baseline
            temp = cache.temporary(".png")
            try:
                _png(temp, reference)
                compare_path = cache.commit(temp, compare_key, ".png")
            finally:
                temp.unlink(missing_ok=True)
            if reference is not baseline:
                del reference
        _check_cancel(cancel_file)
        _assert_source_unchanged(source)
        with Image.open(result_path) as image:
            width, height = image.size
        protected = [result_path, compare_path, cache.path(baseline_key), cache.path(baseline_key, ".json")]
        return {"path": str(result_path), "baseline_path": str(compare_path), "source": source,
                "recipe": _resolved_recipe(source, settings, profile, info),
                "elapsed_seconds": time.monotonic() - start, "warnings": info["warnings"],
                "request_id": request_id, "width": width, "height": height}
    finally:
        _close_map(baseline)
        _close_map(processed)
        if work_path:
            work_path.unlink(missing_ok=True)
        cache.prune(protected)


def _exif(metadata):
    exif = Image.Exif()
    exif[274] = 1
    exif[305] = "Sony Film Lab " + ENGINE_VERSION
    for tag, key in ((271, "make"), (272, "camera_model"), (306, "captured_at")):
        if metadata.get(key):
            exif[tag] = str(metadata[key])
    details = {40961: 1}
    if metadata.get("captured_at"):
        details[36867] = str(metadata["captured_at"])
    for tag, key in ((33434, "exposure_time"), (33437, "f_number"), (37386, "focal_length")):
        if metadata.get(key):
            details[tag] = TiffImagePlugin.IFDRational(*metadata[key])
    if metadata.get("iso") is not None:
        details[34855] = int(metadata["iso"])
    exif[34665] = details
    return exif


def _tiff_exif(path, metadata):
    """Fill the reserved IFD slot with an ordinary EXIF sub-IFD.

    tifffile writes RGB16 and ICC reliably but does not author EXIF sub-IFDs.
    Only the explicit safe capture fields below are encoded. Offsets are from
    the start of this classic TIFF, and the result is checked by tifffile.
    """
    fields = [(40961, 3, 1, struct.pack("<H", 1))]
    if metadata.get("captured_at"):
        value = str(metadata["captured_at"]).encode("ascii", "replace") + b"\0"
        fields.append((36867, 2, len(value), value))
    if metadata.get("iso") is not None:
        fields.append((34855, 4, 1, struct.pack("<I", int(metadata["iso"]))))
    for tag, key in ((33434, "exposure_time"), (33437, "f_number"), (37386, "focal_length")):
        if metadata.get(key):
            fields.append((tag, 5, 1, struct.pack("<II", *metadata[key])))
    fields.sort()
    with Path(path).open("r+b") as stream:
        header = stream.read(8)
        if header[:4] != b"II*\0":
            raise ValueError("Unexpected TIFF header")
        first_ifd = struct.unpack("<I", header[4:])[0]
        stream.seek(first_ifd)
        count = struct.unpack("<H", stream.read(2))[0]
        slot = None
        for i in range(count):
            position = first_ifd + 2 + i * 12
            stream.seek(position)
            if struct.unpack("<H", stream.read(2))[0] == 65000:
                slot = position
                break
        if slot is None:
            raise ValueError("Missing TIFF EXIF placeholder")
        stream.seek(0, 2)
        if stream.tell() % 2:
            stream.write(b"\0")
        offset = stream.tell()
        payload_offset = offset + 2 + len(fields) * 12 + 4
        payload = bytearray()
        entries = bytearray(struct.pack("<H", len(fields)))
        for tag, typ, n, value in fields:
            if len(value) <= 4:
                stored = value.ljust(4, b"\0")
            else:
                stored = struct.pack("<I", payload_offset + len(payload))
                payload.extend(value)
                if len(payload) % 2:
                    payload.append(0)
            entries.extend(struct.pack("<HHI", tag, typ, n) + stored)
        stream.write(entries + b"\0\0\0\0" + payload)
        stream.seek(slot)
        stream.write(struct.pack("<HHII", 34665, 4, 1, offset))
        # TIFF requires IFD entries in increasing tag order. Replacing the
        # private placeholder changes its order, but not any referenced offsets.
        stream.seek(first_ifd + 2)
        entries = [stream.read(12) for _ in range(count)]
        entries.sort(key=lambda entry: struct.unpack("<H", entry[:2])[0])
        stream.seek(first_ifd + 2)
        stream.write(b"".join(entries))
        stream.flush()
        os.fsync(stream.fileno())


def _write_image(path, rgb, options, metadata):
    icc = _icc()
    if options["format"] == "jpeg":
        image = Image.fromarray(np.floor(np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8))
        image.save(path, format="JPEG", quality=options["quality"], subsampling=0,
                   icc_profile=icc, exif=_exif(metadata))
    else:
        import tifffile
        tags = [(274, "H", 1, 1, False), (34675, "B", len(icc), icc, False),
                (65000, "I", 1, 0, False)]
        for tag, key in ((271, "make"), (272, "camera_model")):
            if metadata.get(key):
                tags.append((tag, "s", 0, str(metadata[key]), False))
        pixels = np.floor(np.clip(rgb, 0, 1) * 65535 + 0.5).astype(np.uint16)
        tifffile.imwrite(path, pixels, photometric="rgb", compression="deflate", predictor=False,
                         byteorder="<", bigtiff=False, metadata=None, extratags=tags,
                         datetime=metadata.get("captured_at") or False,
                         software="Sony Film Lab " + ENGINE_VERSION)
        del pixels
        _tiff_exif(path, metadata)
    with Path(path).open("r+b") as stream:
        os.fsync(stream.fileno())
    _verify_image(path, options["format"], (rgb.shape[1], rgb.shape[0]), icc)


def _verify_image(path, fmt, size, icc):
    if fmt == "jpeg":
        with Image.open(path) as image:
            image.load()
            if image.size != size or image.mode != "RGB" or image.info.get("icc_profile") != icc:
                raise ValueError("JPEG 크기·RGB·ICC 검증 실패")
            if image.getexif().get(274) != 1:
                raise ValueError("JPEG 회전 정보 검증 실패")
    else:
        import tifffile
        with tifffile.TiffFile(path) as image:
            page = image.pages[0]
            if page.shape != (size[1], size[0], 3) or page.dtype != np.dtype("uint16"):
                raise ValueError("TIFF 16비트 RGB 검증 실패")
            if page.tags[34675].value != icc or page.tags[274].value != 1:
                raise ValueError("TIFF ICC·회전 정보 검증 실패")
            if page.tags[34665].value.get("ColorSpace") != 1:
                raise ValueError("TIFF EXIF 검증 실패")
            decoded = page.asarray()
            if decoded.shape != page.shape:
                raise ValueError("TIFF 재읽기 검증 실패")


def _commit_no_replace(temporary, target):
    """Atomic appearance without ever replacing an existing user file."""
    try:
        os.link(temporary, target)
    except FileExistsError:
        raise
    except OSError:
        # Windows rename is atomic and refuses existing destinations. POSIX
        # rename overwrites, so no fallback is permitted on POSIX.
        if os.name != "nt":
            raise
        os.rename(temporary, target)
        return
    Path(temporary).unlink()


def _unique_commit(temporary, directory, stem, suffix, paired_suffix=None):
    for index in range(1000000):
        name = stem if index == 0 else f"{stem}_{index:03d}"
        target = directory / (name + suffix)
        if paired_suffix and (directory / (name + paired_suffix)).exists():
            continue
        try:
            _commit_no_replace(temporary, target)
            return target
        except FileExistsError:
            continue
    raise OSError("충돌 없는 결과 파일명을 만들 수 없습니다.")


def export_item(source, recipe, export_options, cancel=None) -> dict:
    start = time.monotonic()
    result = {"status": "failed", "path": None, "recipe_path": None, "error": None,
              "source": str(source), "elapsed_seconds": 0}
    baseline = processed = None
    work_path = image_temp = recipe_temp = None
    cache = None
    try:
        _check_cancel(cancel)
        settings = _settings(recipe)
        options = validate_export_options(copy.deepcopy(export_options))
        profile = _profile(settings, recipe)
        source_info = _check_source(source, recipe)
        directory = Path(options["output_dir"]).expanduser().resolve()
        if directory == Path(source_info["path"]).parent:
            raise ValueError("원본 보호를 위해 원본 사진 폴더와 다른 저장 폴더를 선택하세요.")
        directory.mkdir(parents=True, exist_ok=True)
        estimated = source_info["width"] * source_info["height"] * 6 + 64 * 1024**2
        if shutil.disk_usage(directory).free < estimated:
            raise OSError("저장 공간이 부족합니다. 출력 크기 이상의 여유 공간을 확보하세요.")
        cache = DiskCache()
        if cache.directory == Path(source_info["path"]).parent:
            raise ValueError("원본 보호를 위해 캐시는 원본 사진 폴더와 다른 폴더에 저장해야 합니다.")
        baseline, processed, info, key, work_path = _processed(source_info, settings, cache, cancel)
        # Export needs no comparison array after processing.
        _close_map(baseline)
        baseline = None
        rgb = _final_rgb(processed, settings, source_info["sha256"], options.get("long_edge"), cancel)
        _check_cancel(cancel)
        image_temp = directory / (".film-lab-" + uuid.uuid4().hex + ".tmp")
        _write_image(image_temp, rgb, options, source_info["metadata"])
        _check_cancel(cancel)
        # Confirm the read source has not changed while LibRaw was working.
        _assert_source_unchanged(source_info)
        stem = Path(source_info["path"]).stem + "_" + STYLE_CODES[settings["style_id"]] + "_v1"
        suffix = ".jpg" if options["format"] == "jpeg" else ".tiff"
        target = _unique_commit(image_temp, directory, stem, suffix, ".recipe.json")
        result["path"] = str(target)
        result["status"] = "partial"
        result["warnings"] = info["warnings"]
        resolved = _resolved_recipe(source_info, settings, profile, info, options)
        resolved["output"] = {"path": str(target), "sha256": sha256_file(target),
                              "width": rgb.shape[1], "height": rgb.shape[0],
                              "orientation": 1, "saved_at": datetime.now(timezone.utc).isoformat()}
        recipe_temp = directory / (".film-lab-" + uuid.uuid4().hex + ".json.tmp")
        atomic_json(recipe_temp, resolved)
        recipe_target = target.with_suffix(".recipe.json")
        _commit_no_replace(recipe_temp, recipe_target)
        result["recipe_path"] = str(recipe_target)
        result["status"] = "completed"
    except CancelledError as exc:
        result["status"] = "partial" if result["path"] else "cancelled"
        result["error"] = str(exc)
    except MemoryError:
        result["error"] = "메모리가 부족합니다. 다른 앱을 닫고 캐시를 줄인 뒤 다시 시도하세요."
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        _close_map(baseline)
        _close_map(processed)
        for temporary in (work_path, image_temp, recipe_temp):
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
        if cache:
            cache.prune()
        result["elapsed_seconds"] = time.monotonic() - start
    return result


def export(items: list, export_options: dict, progress=None, cancel=None) -> list:
    """Sequential batch, snapshotting settings and reporting every individual error."""
    frozen_items = copy.deepcopy(items)
    options = validate_export_options(copy.deepcopy(export_options))
    results = []
    for item in frozen_items:
        source = item.get("source") or item.get("path")
        if isinstance(source, dict):
            source = source["path"]
        result = export_item(source, item.get("recipe", {}), options, cancel)
        results.append(result)
        if progress:
            progress(copy.deepcopy(result))
    if results:
        temporary = None
        try:
            directory = Path(options["output_dir"]).expanduser().resolve()
            # Never create a batch CSV in any original source folder either.
            for item in frozen_items:
                source = item.get("source") or item.get("path")
                if isinstance(source, dict):
                    source = source["path"]
                if directory == Path(source).expanduser().resolve().parent:
                    raise ValueError("원본 폴더에는 배치 기록을 저장하지 않습니다.")
            directory.mkdir(parents=True, exist_ok=True)
            temporary = directory / (".film-lab-" + uuid.uuid4().hex + ".csv.tmp")
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                columns = ["source", "status", "path", "recipe_path", "error", "elapsed_seconds"]
                writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                for result in results:
                    row = {k: result.get(k) for k in columns}
                    # Spreadsheet formula injection protection for filenames/errors.
                    for key, value in row.items():
                        if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                            row[key] = "'" + value
                    writer.writerow(row)
                stream.flush()
                os.fsync(stream.fileno())
            csv_path = _unique_commit(temporary, directory, "batch_" + datetime.now().strftime("%Y%m%d_%H%M%S"), ".csv")
            for result in results:
                result["batch_csv"] = str(csv_path)
        except Exception as exc:
            for result in results:
                if result["status"] == "completed":
                    result["status"] = "partial"
                result["error"] = ((result.get("error") + "; ") if result.get("error") else "") + f"배치 CSV 저장 실패: {exc}"
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    return results
