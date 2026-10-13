"""Versioned, colour-managed imports for developed photographs.

Pillow only supports 8-bit multichannel ICC transforms. TIFF uint16 is decoded
with tifffile; its precision is preserved and unsupported ICC transforms are
rejected rather than silently routed through an 8-bit Pillow image.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import io
from pathlib import Path
import struct

import numpy as np
from PIL import Image, ImageCms

IMPORTER_VERSION = "1.0.0"
SUPPORTED_FORMATS = {"JPEG", "PNG", "TIFF", "WEBP", "BMP"}
_EXTENSION_FORMAT = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".tif": "TIFF", ".tiff": "TIFF", ".webp": "WEBP", ".bmp": "BMP"}
_ICC_COLOUR_TAGS = {b"wtpt", b"chad", b"rXYZ", b"gXYZ", b"bXYZ", b"rTRC", b"gTRC", b"bTRC", b"chrm"}


def _ratio(value):
    if value is None:
        return None
    try:
        if isinstance(value, (tuple, list)):
            value = Fraction(int(value[0]), int(value[1]))
        ratio = Fraction(str(value)).limit_denominator(1_000_000)
        if ratio <= 0:
            return None
        return [ratio.numerator, ratio.denominator]
    except (ValueError, TypeError, IndexError, ZeroDivisionError):
        return None


def _safe_metadata(main, details):
    def value(number, name):
        return details.get(number, details.get(name, main.get(number, main.get(name))))
    def text(number, name):
        item = value(number, name)
        if isinstance(item, bytes):
            item = item.decode("ascii", "replace")
        return str(item).strip("\0 ") if item is not None else ""
    iso = value(34855, "ISOSpeedRatings")
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else None
    try:
        iso = int(iso) if iso is not None and int(iso) > 0 else None
    except (ValueError, TypeError):
        iso = None
    return {"camera_model": text(272, "Model"), "make": text(271, "Make"),
            "captured_at": text(36867, "DateTimeOriginal") or text(306, "DateTime") or None,
            "exposure_time": _ratio(value(33434, "ExposureTime")),
            "f_number": _ratio(value(33437, "FNumber")), "iso": iso,
            "focal_length": _ratio(value(37386, "FocalLength"))}


def _orientation(value):
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("이미지 Orientation 정보가 올바르지 않습니다.") from exc
    if value not in range(1, 9):
        raise ValueError("이미지 Orientation은 1~8이어야 합니다.")
    return value


def orient_pixels(rgb, orientation):
    """EXIF 1..8, including mirrored orientations, without quantizing pixels."""
    if orientation == 2:
        return rgb[:, ::-1]
    if orientation == 3:
        return rgb[::-1, ::-1]
    if orientation == 4:
        return rgb[::-1]
    if orientation == 5:
        return np.swapaxes(rgb, 0, 1)
    if orientation == 6:
        return np.rot90(rgb, -1)
    if orientation == 7:
        return np.swapaxes(rgb, 0, 1)[::-1, ::-1]
    if orientation == 8:
        return np.rot90(rgb, 1)
    return rgb


def _profile(icc):
    if not isinstance(icc, bytes) or len(icc) < 132:
        raise ValueError("이미지의 ICC 프로필이 손상되었거나 올바르지 않습니다.")
    try:
        return ImageCms.ImageCmsProfile(io.BytesIO(icc))
    except Exception as exc:
        raise ValueError("이미지의 ICC 프로필을 읽을 수 없습니다.") from exc


def _colour_tags(icc):
    """Compare colourimetric payloads, never profile names, for an sRGB bypass."""
    count = struct.unpack_from(">I", icc, 128)[0]
    if count > 128 or 132 + count * 12 > len(icc):
        return None
    tags = {}
    for index in range(count):
        signature, offset, length = struct.unpack_from(">4sII", icc, 132 + 12 * index)
        if offset + length > len(icc) or signature in tags:
            return None
        tags[signature] = icc[offset:offset + length]
    # Additional A2B/B2A or other transforms would change the profile meaning.
    if set(tags) - (_ICC_COLOUR_TAGS | {b"desc", b"cprt"}):
        return None
    return {tag: tags.get(tag) for tag in _ICC_COLOUR_TAGS}


def _is_standard_srgb(icc):
    canonical = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return (icc[16:24] == canonical[16:24] and icc[68:80] == canonical[68:80]
            and _colour_tags(icc) is not None and _colour_tags(icc) == _colour_tags(canonical))


def _colour_policy(icc, warnings, *, high_bit=False):
    if icc is None:
        warnings.append("ICC 프로필이 없어 입력 색상을 sRGB로 해석했습니다.")
        return None, "assumed_sRGB", None
    profile = _profile(icc)
    if _is_standard_srgb(icc):
        return profile, "embedded_standard_sRGB", hashlib.sha256(icc).hexdigest()
    if high_bit:
        raise ValueError("이 16비트 TIFF의 ICC 프로필 변환은 지원하지 않습니다. "
                         "정밀도 유지를 위해 8비트로 변환하지 않았습니다. "
                         "표준 sRGB 16비트 TIFF로 변환한 파일을 사용하세요.")
    return profile, "converted_embedded_ICC_to_sRGB", hashlib.sha256(icc).hexdigest()


def _bmp_profile(path):
    """Pillow does not expose V4/V5 colour declarations or embedded V5 ICC.

    BITMAPV5HEADER profile offsets are relative to the DIB header, not the file.
    Linked profiles are never opened. Calibrated RGB needs a separate colour
    transform and must not silently be interpreted as untagged sRGB.
    """
    with Path(path).open("rb") as stream:
        header = stream.read(138)
        if len(header) < 18 or header[:2] != b"BM":
            raise ValueError("BMP 파일 헤더가 올바르지 않습니다.")
        dib_size = struct.unpack_from("<I", header, 14)[0]
        if dib_size < 108:
            return None, False
        if dib_size not in (108, 124) or len(header) < 14 + dib_size:
            raise ValueError("지원하지 않거나 손상된 BMP 색관리 헤더입니다.")
        color_space = struct.unpack_from("<I", header, 14 + 56)[0]
        if color_space in (0x73524742, 0x57696E20):  # LCS_sRGB / Windows default sRGB
            return None, True
        if color_space == 0x4C494E4B:
            raise ValueError("외부 연결 ICC 프로필을 사용하는 BMP는 지원하지 않습니다. ICC를 포함한 이미지로 저장하세요.")
        if color_space != 0x4D424544 or dib_size != 124:
            raise ValueError("이 BMP의 보정 색공간 선언은 지원하지 않습니다. sRGB 또는 ICC를 포함한 이미지로 저장하세요.")
        offset, length = struct.unpack_from("<II", header, 14 + 112)
        if length < 132 or length > 16 * 1024**2 or offset < dib_size or 14 + offset + length > Path(path).stat().st_size:
            raise ValueError("BMP에 포함된 ICC 프로필 위치 또는 크기가 올바르지 않습니다.")
        stream.seek(14 + offset)
        return stream.read(length), False


def _details(fmt, rgb, orientation, metadata, warnings, bit_depth, color_policy, icc_hash, alpha):
    return {"source_kind": "raster", "image_format": fmt, "width": int(rgb.shape[1]),
            "height": int(rgb.shape[0]), "orientation": orientation,
            "rotation": {3: 180, 5: 90, 6: 90, 7: 270, 8: 270}.get(orientation, 0),
            "camera_model": metadata["camera_model"], "metadata": metadata,
            "supported": True, "warnings": warnings, "bit_depth": bit_depth,
            "color_policy": color_policy, "input_icc_sha256": icc_hash,
            "alpha_composited": alpha, "frame_count": 1}


def _read_tiff(path):
    import tifffile
    with tifffile.TiffFile(path) as file:
        if len(file.pages) != 1 or len(file.series) != 1:
            raise ValueError("다중 페이지 TIFF는 지원하지 않습니다. 한 장씩 저장한 이미지를 사용하세요.")
        page = file.pages[0]
        width, height = int(page.imagewidth), int(page.imagelength)
        if width <= 0 or height <= 0:
            raise ValueError("TIFF의 이미지 크기가 올바르지 않습니다.")
        if Image.MAX_IMAGE_PIXELS is not None and width * height > 2 * Image.MAX_IMAGE_PIXELS:
            raise ValueError("TIFF 이미지의 픽셀 수가 안전한 디코딩 한도를 초과합니다.")
        if page.subifds:
            raise ValueError("하위 이미지가 포함된 TIFF는 지원하지 않습니다. 단일 이미지 TIFF를 사용하세요.")
        if page.dtype.kind != "u" or page.dtype.itemsize not in (1, 2):
            raise ValueError("TIFF는 8비트 또는 16비트 unsigned 정수 이미지만 지원합니다. 부동소수점/32비트 TIFF는 지원하지 않습니다.")
        if int(page.bitspersample) not in (8, 16):
            raise ValueError("이 TIFF의 비트 깊이는 지원하지 않습니다. 8비트 또는 16비트를 사용하세요.")
        # Non RGB/grey 8-bit TIFF uses Pillow's colour-aware decoder below.
        if int(page.photometric) not in (0, 1, 2):
            if page.dtype.itemsize == 1:
                return None
            raise ValueError("16비트 TIFF는 RGB 또는 회색조만 지원합니다.")
        pixels = page.asarray()
        if page.planarconfig == 2 and pixels.ndim == 3:
            pixels = np.moveaxis(pixels, 0, -1)
        if pixels.ndim == 2:
            pixels = pixels[..., None]
        channels = 3 if int(page.photometric) == 2 else 1
        if pixels.ndim != 3 or pixels.shape[-1] not in (channels, channels + 1):
            raise ValueError("지원하지 않는 TIFF 채널 구성입니다.")
        metadata_tags = {tag.code: tag.value for tag in page.tags.values()
                         if tag.code in (271, 272, 306, 33434, 33437, 34855, 37386)}
        details = page.tags[34665].value if 34665 in page.tags else {}
        metadata = _safe_metadata(metadata_tags, details)
        orientation = _orientation(page.tags[274].value if 274 in page.tags else 1)
        warnings = []
        icc = page.tags[34675].value if 34675 in page.tags else None
        bit_depth = page.dtype.itemsize * 8
        profile, policy, icc_hash = _colour_policy(icc, warnings, high_bit=bit_depth == 16)
        has_alpha = pixels.shape[-1] == channels + 1
        if has_alpha and (len(page.extrasamples) != 1 or int(page.extrasamples[0]) not in (1, 2)):
            raise ValueError("추가 TIFF 채널을 투명도로 확인할 수 없습니다.")
        rgb = pixels[..., :channels].astype(np.float32) / np.float32(65535 if bit_depth == 16 else 255)
        if channels == 1:
            rgb = np.repeat(rgb, 3, axis=-1)
        alpha = pixels[..., channels:channels + 1].astype(np.float32) / np.float32(65535 if bit_depth == 16 else 255) if has_alpha else None
        if has_alpha and int(page.extrasamples[0]) == 1:
            rgb = np.divide(rgb, alpha, out=np.zeros_like(rgb), where=alpha > 0)
            np.clip(rgb, 0, 1, out=rgb)
        if channels == 1 and int(page.photometric) == 0:
            rgb = 1 - rgb
        if policy == "converted_embedded_ICC_to_sRGB":
            mode = "L" if channels == 1 and profile.profile.xcolor_space.strip() == "GRAY" else "RGB"
            array = np.floor(rgb * 255 + 0.5).astype(np.uint8)
            image = Image.fromarray(array[..., 0] if mode == "L" else array, mode)
            try:
                converted = ImageCms.profileToProfile(image, profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception as exc:
                raise ValueError("TIFF ICC 색공간과 이미지 채널이 호환되지 않습니다.") from exc
            rgb = np.asarray(converted, dtype=np.float32) / np.float32(255)
        if has_alpha:
            rgb = rgb * alpha + (1 - alpha)
            warnings.append("투명 영역을 흰 배경에 합성했습니다.")
        rgb = orient_pixels(rgb, orientation)
        return rgb, _details("TIFF", rgb, orientation, metadata, warnings, bit_depth, policy, icc_hash, has_alpha)


def load_raster(path):
    """Decode a single image into encoded sRGB float32 and a safe description."""
    path = Path(path)
    with path.open("rb") as stream:
        signature = stream.read(4)
    is_tiff = signature in (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+")
    if is_tiff:
        if _EXTENSION_FORMAT.get(path.suffix.lower()) != "TIFF":
            raise ValueError("파일 확장자와 실제 이미지 형식(TIFF)이 일치하지 않습니다.")
        # Some valid TIFF modes (e.g. uint16 grey+alpha) cannot even be opened
        # by Pillow. The TIFF signature routes them directly to tifffile.
        decoded = _read_tiff(path)
        if decoded is not None:
            return decoded
    with Image.open(path) as image:
        fmt = image.format
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"지원하지 않는 실제 이미지 형식입니다: {fmt}")
        if _EXTENSION_FORMAT.get(path.suffix.lower()) != fmt:
            raise ValueError(f"파일 확장자와 실제 이미지 형식({fmt})이 일치하지 않습니다.")
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("다중 프레임/애니메이션 이미지는 지원하지 않습니다. 정지 이미지 한 장을 사용하세요.")
    if fmt == "TIFF" and not is_tiff:
        decoded = _read_tiff(path)
        if decoded is not None:
            return decoded
    with Image.open(path) as image:
        # RGB16 PNG otherwise looks like ordinary RGB after Pillow has discarded
        # its lower eight bits, so inspect the actual IHDR before loading pixels.
        if fmt == "PNG":
            with path.open("rb") as stream:
                header = stream.read(29)
            if len(header) >= 25 and header[24] == 16:
                raise ValueError("16비트 PNG는 지원하지 않습니다. 정밀도 유지를 위해 8비트로 줄이지 않았습니다. 16비트 TIFF를 사용하세요.")
        if image.mode not in ("1", "L", "LA", "P", "PA", "RGB", "RGBA", "RGBX", "CMYK", "LAB", "YCbCr"):
            raise ValueError(f"지원하지 않는 이미지 모드/비트 깊이입니다: {image.mode}")
        orientation = _orientation(image.getexif().get(274, 1))
        image.load()
        exif = image.getexif()
        details = exif.get_ifd(34665) if 34665 in exif else {}
        metadata = _safe_metadata(exif, details)
        warnings = []
        icc = image.info.get("icc_profile")
        bmp_srgb = False
        if fmt == "BMP":
            embedded_bmp_icc, bmp_srgb = _bmp_profile(path)
            if embedded_bmp_icc is not None:
                icc = embedded_bmp_icc
        if not icc and image.mode in ("CMYK", "LAB"):
            raise ValueError(f"{image.mode} 입력에는 올바른 ICC 프로필이 필요합니다.")
        profile, policy, icc_hash = _colour_policy(icc, warnings)
        if bmp_srgb and icc is None:
            policy = "declared_BMP_sRGB"
            warnings.clear()
        if fmt == "PNG" and not icc and "srgb" not in image.info and ("gamma" in image.info or "chromaticity" in image.info):
            warnings.append("PNG gAMA/cHRM 정보만으로는 ICC 변환을 수행하지 않았습니다. 입력 색상을 sRGB로 해석했습니다.")
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        alpha = np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.float32)[..., None] / np.float32(255) if has_alpha else None
        if image.mode in ("CMYK", "LAB"):
            color_image = image
        elif profile is not None and profile.profile.xcolor_space.strip() == "GRAY" and image.mode in ("1", "L", "LA"):
            color_image = image.convert("L")
        else:
            color_image = image.convert("RGB")
        if policy == "converted_embedded_ICC_to_sRGB":
            try:
                color_image = ImageCms.profileToProfile(color_image, profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
            except Exception as exc:
                raise ValueError("ICC 색공간과 이미지 채널이 호환되지 않습니다.") from exc
        elif color_image.mode != "RGB":
            # Only RGB profiles can qualify for the standard-sRGB identity path.
            if color_image.mode in ("CMYK", "LAB"):
                raise ValueError("ICC 색공간과 이미지 채널이 호환되지 않습니다.")
            color_image = color_image.convert("RGB")
        rgb = np.asarray(color_image, dtype=np.float32) / np.float32(255)
        if has_alpha:
            rgb = rgb * alpha + (1 - alpha)
            warnings.append("투명 영역을 흰 배경에 합성했습니다.")
        # Pillow's TIFF plugin has already applied all EXIF orientations during
        # load (including its 8-bit palette/CMYK/LAB fallback path).
        rgb = orient_pixels(rgb, 1 if fmt == "TIFF" else orientation)
        return rgb, _details(fmt, rgb, orientation, metadata, warnings, 8, policy, icc_hash, has_alpha)


def adjust_developed(rgb, settings):
    """Relative exposure/WB only; no RAW demosaic, camera WB or baseline curve."""
    exposure, warmth, tint = settings["exposure_ev"], settings["warmth"], settings["tint"]
    multipliers = [2 ** (warmth / 100 * 0.45), 2 ** (-tint / 100 * 0.25), 2 ** (-warmth / 100 * 0.45)]
    if not exposure and not warmth and not tint:
        return rgb, multipliers
    from .color import srgb_decode, srgb_encode
    linear = srgb_decode(rgb)
    linear *= np.asarray(multipliers, dtype=np.float32) * np.float32(2 ** exposure)
    return srgb_encode(linear), multipliers
