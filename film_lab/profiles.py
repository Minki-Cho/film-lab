"""Strict, versioned sRGB .cube profiles and RGB-axis tetrahedral interpolation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .color import BASELINE_VERSION, CHUNK_PIXELS, _rgb_array, srgb_decode, srgb_encode

PROFILE_ROOT = Path(__file__).resolve().parent / "data" / "profiles"
PROFILE_NAMES = {
    "baseline": ("기본 현상", "BASE"),
    "documentary": ("다큐멘터리 스타일", "DOC"),
    "negative": ("네거티브 스타일", "NEG"),
    "cinema": ("시네마 스타일", "CIN"),
    "vivid": ("비비드 스타일", "VIV"),
    "monochrome": ("흑백 스타일", "MONO"),
}


class ProfileError(ValueError):
    """A profile is missing, corrupt, or outside the supported colour contract."""


@dataclass(frozen=True)
class CubeLUT:
    """Array axes are [red, green, blue, channel]; .cube red varies fastest."""

    table: np.ndarray
    grid_size: int
    sha256: str
    domain_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        return interpolate_tetrahedral(rgb, self.table)


def load_cube(path: str | Path, expected_sha256: str | None = None,
              expected_grid_size: int | None = None) -> CubeLUT:
    """Load a strict 3D .cube with a 0..1 input/output contract.

    Reject shaper/1D LUTs, unknown headers, duplicate headers, nonfinite numbers,
    wrong sample counts, out-of-range values and any hash mismatch.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and sha != expected_sha256:
            raise ProfileError(f"LUT SHA-256 불일치: {path.name}")
        source = data.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ProfileError(f"LUT를 읽을 수 없습니다: {path.name}: {exc}") from exc
    headers: dict = {}
    values = []
    reading_data = False
    for line_number, raw in enumerate(source.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        key = tokens[0]
        if key in ("TITLE", "LUT_3D_SIZE", "DOMAIN_MIN", "DOMAIN_MAX"):
            if reading_data or key in headers:
                raise ProfileError(f"LUT 헤더 순서 또는 중복 오류: {line_number}행")
            if key == "TITLE":
                if not re.fullmatch(r'TITLE\s+"[^"\r\n]*"', line):
                    raise ProfileError(f"LUT TITLE 형식 오류: {line_number}행")
                headers[key] = line
            elif key == "LUT_3D_SIZE":
                if len(tokens) != 2 or not tokens[1].isdigit() or not 2 <= int(tokens[1]) <= 65:
                    raise ProfileError("지원하는 LUT 격자는 2~65입니다.")
                headers[key] = int(tokens[1])
            else:
                try:
                    triple = tuple(float(x) for x in tokens[1:])
                except ValueError as exc:
                    raise ProfileError("LUT DOMAIN 숫자 형식 오류") from exc
                target = (0.0,) * 3 if key == "DOMAIN_MIN" else (1.0,) * 3
                if triple != target:
                    raise ProfileError("LUT 입력 영역은 RGB 0~1이어야 합니다.")
                headers[key] = triple
            continue
        if len(tokens) != 3 or "LUT_3D_SIZE" not in headers:
            raise ProfileError(f"알 수 없는 LUT 명령 또는 데이터: {line_number}행")
        try:
            triple = tuple(float(x) for x in tokens)
        except ValueError as exc:
            raise ProfileError(f"LUT 숫자 형식 오류: {line_number}행") from exc
        if not all(np.isfinite(x) and 0 <= x <= 1 for x in triple):
            raise ProfileError(f"LUT 출력 영역 오류: {line_number}행")
        reading_data = True
        values.append(triple)
        if len(values) > headers["LUT_3D_SIZE"] ** 3:
            raise ProfileError("LUT 샘플 수가 격자보다 많습니다.")
    grid = headers.get("LUT_3D_SIZE", 0)
    if not grid or len(values) != grid ** 3:
        raise ProfileError("LUT 샘플 수가 격자와 일치하지 않습니다.")
    if expected_grid_size is not None and grid != expected_grid_size:
        raise ProfileError("LUT 격자가 manifest와 일치하지 않습니다.")
    table = np.array(values, dtype=np.float32).reshape(grid, grid, grid, 3).transpose(2, 1, 0, 3).copy()
    table.flags.writeable = False
    return CubeLUT(table, grid, sha)


def interpolate_tetrahedral(rgb: np.ndarray, table: np.ndarray | CubeLUT) -> np.ndarray:
    """Interpolate the six tetrahedra in every RGB cube, in bounded chunks."""
    image = _rgb_array(rgb)
    if isinstance(table, CubeLUT):
        table = table.table
    table = np.asarray(table, dtype=np.float32)
    grid = table.shape[0] if table.ndim == 4 else 0
    if grid < 2 or table.shape != (grid, grid, grid, 3) or not np.isfinite(table).all():
        raise ProfileError("LUT 배열은 유한한 N×N×N×3이어야 합니다.")
    source = image.reshape(-1, 3)
    output = np.empty_like(source)
    for start in range(0, len(source), CHUNK_PIXELS):
        coordinates = np.clip(source[start:start + CHUNK_PIXELS], 0, 1) * (grid - 1)
        base = np.minimum(np.floor(coordinates).astype(np.int32), grid - 2)
        fraction = coordinates - base
        # Sort fractional positions; walks 000 -> largest axis -> next -> 111.
        # This is precisely the containing tetrahedron, including shared faces.
        order = np.argsort(-fraction, axis=1, kind="stable")
        sorted_fraction = np.take_along_axis(fraction, order, axis=1)
        first = np.zeros_like(base)
        second = np.ones_like(base)
        row = np.arange(len(base))
        first[row, order[:, 0]] = 1
        second[row, order[:, 2]] = 0
        p1, p2 = base + first, base + second
        c0 = table[base[:, 0], base[:, 1], base[:, 2]]
        c1 = table[p1[:, 0], p1[:, 1], p1[:, 2]]
        c2 = table[p2[:, 0], p2[:, 1], p2[:, 2]]
        c3 = table[base[:, 0] + 1, base[:, 1] + 1, base[:, 2] + 1]
        weights = sorted_fraction.astype(np.float32)
        output[start:start + CHUNK_PIXELS] = (c0 + weights[:, 0, None] * (c1 - c0)
                                                      + weights[:, 1, None] * (c2 - c1)
                                                      + weights[:, 2, None] * (c3 - c2))
    return output.reshape(image.shape)


# Friendly alias for direct engine callers and numerical tests.
tetrahedral_interpolate = interpolate_tetrahedral


def get_profile(profile_id: str) -> dict:
    """Return validated manifest metadata; never replace a broken style."""
    if profile_id not in PROFILE_NAMES:
        raise ProfileError(f"알 수 없는 스타일: {profile_id}")
    directory = PROFILE_ROOT / profile_id
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        required = {"id", "version", "input_space", "output_space", "grid_size", "sha256",
                    "baseline_version", "source", "license", "asset", "kind"}
        if not isinstance(manifest, dict) or not required.issubset(manifest):
            raise ProfileError("스타일 manifest의 필수 항목이 없습니다.")
        if manifest["id"] != profile_id or manifest["version"] != "1.0.0":
            raise ProfileError("스타일 ID 또는 버전이 지원되지 않습니다.")
        if manifest["baseline_version"] != BASELINE_VERSION:
            raise ProfileError("기본 현상 버전이 일치하지 않습니다.")
        if manifest["input_space"] != "sRGB_D65_encoded" or manifest["output_space"] != "sRGB_D65_encoded":
            raise ProfileError("스타일 색공간 계약이 일치하지 않습니다.")
        asset = manifest["asset"]
        if not isinstance(asset, str) or Path(asset).name != asset or asset not in ("style.cube", "curve.json"):
            raise ProfileError("스타일 파일 경로가 잘못되었습니다.")
        if not isinstance(manifest["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]):
            raise ProfileError("스타일 SHA-256 형식이 잘못되었습니다.")
        asset_path = directory / asset
        data = asset_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise ProfileError("스타일 파일의 SHA-256이 일치하지 않습니다.")
        expected_kind = "baseline" if profile_id == "baseline" else "monochrome" if profile_id == "monochrome" else "cube"
        if manifest["kind"] != expected_kind or manifest["grid_size"] != (33 if expected_kind == "cube" else 0):
            raise ProfileError("스타일 형식 또는 격자 크기가 일치하지 않습니다.")
        if expected_kind == "cube":
            _cached_cube(str(asset_path), manifest["sha256"])
        else:
            curve = json.loads(data.decode("utf-8"))
            if expected_kind == "monochrome":
                weights, knots = np.asarray(curve["weights"]), np.asarray(curve["knots"])
                if weights.shape != (3,) or not np.isfinite(weights).all() or np.any(weights < 0) or not np.isclose(weights.sum(), 1):
                    raise ProfileError("흑백 채널 기여도가 잘못되었습니다.")
                if knots.ndim != 2 or knots.shape[1] != 2 or knots.shape[0] < 2 or not np.isfinite(knots).all() or np.any((knots < 0) | (knots > 1)):
                    raise ProfileError("흑백 톤 곡선이 잘못되었습니다.")
                if np.any(np.diff(knots[:, 0]) <= 0) or np.any(np.diff(knots[:, 1]) < 0) or knots[0, 0] != 0 or knots[-1, 0] != 1:
                    raise ProfileError("흑백 톤 곡선은 0~1의 단조 증가 곡선이어야 합니다.")
        result = dict(manifest)
        result.update(name=PROFILE_NAMES[profile_id][0], short_name=PROFILE_NAMES[profile_id][1], available=True, error=None)
        return result
    except ProfileError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ProfileError(f"스타일을 불러올 수 없습니다 ({profile_id}): {exc}") from exc


def list_profiles() -> list[dict]:
    profiles = []
    for profile_id, (name, short_name) in PROFILE_NAMES.items():
        try:
            profiles.append(get_profile(profile_id))
        except ProfileError as exc:
            profiles.append({"id": profile_id, "name": name, "short_name": short_name,
                             "version": "1.0.0", "baseline_version": BASELINE_VERSION,
                             "sha256": None, "available": False, "error": str(exc),
                             "validation_status": "initial_style"})
    return profiles


@lru_cache(maxsize=4)
def _cached_cube(path: str, sha256: str) -> CubeLUT:
    return load_cube(path, expected_sha256=sha256, expected_grid_size=33)


def apply_style(rgb: np.ndarray, style_id: str, strength: float = 1.0) -> np.ndarray:
    """Apply profile then mix in encoded sRGB; zero is an exact identity."""
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ProfileError("스타일 강도는 0~1이어야 합니다.")
    image = _rgb_array(rgb)
    manifest = get_profile(style_id)
    if style_id == "baseline" or strength == 0:
        return image.copy()
    path = PROFILE_ROOT / style_id / manifest["asset"]
    if manifest["kind"] == "cube":
        styled = _cached_cube(str(path), manifest["sha256"]).apply(image)
    else:
        curve = json.loads(path.read_text(encoding="utf-8"))
        source = image.reshape(-1, 3)
        styled = np.empty_like(source)
        weights = np.array(curve["weights"], dtype=np.float32)
        knots = np.array(curve["knots"], dtype=np.float32)
        for start in range(0, len(source), CHUNK_PIXELS):
            luminance = srgb_decode(source[start:start + CHUNK_PIXELS]) @ weights
            encoded = srgb_encode(luminance)
            tone = np.interp(encoded, knots[:, 0], knots[:, 1]).astype(np.float32)
            styled[start:start + CHUNK_PIXELS] = tone[:, None]
        styled = styled.reshape(image.shape)
    if strength == 1:
        return styled
    # In-place mixing avoids a second full-frame temporary.
    styled *= np.float32(strength)
    flat = styled.reshape(-1, 3)
    source = image.reshape(-1, 3)
    for start in range(0, len(source), CHUNK_PIXELS):
        flat[start:start + CHUNK_PIXELS] += source[start:start + CHUNK_PIXELS] * np.float32(1 - strength)
    return styled
