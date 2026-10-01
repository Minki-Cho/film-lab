"""Versioned float32 colour processing, independent of RAW I/O and the GUI.

The RAW decoder contract is linear ProPhoto RGB with a D50 white point. Only
``baseline_render`` changes colour space. Styles operate on encoded D65 sRGB.
All image operations retain floating point precision until the output encoder.
"""

from __future__ import annotations

import hashlib
from typing import Mapping

import numpy as np
from PIL import Image

BASELINE_VERSION = "1.0.0"
CHUNK_PIXELS = 262_144

# Fixed colourimetric constants. Matrices multiply column RGB vectors.
PROPHOTO_TO_XYZ_D50 = np.array([
    [0.7976749, 0.1351917, 0.0313534],
    [0.2880402, 0.7118741, 0.0000857],
    [0.0000000, 0.0000000, 0.8252100],
], dtype=np.float32)
BRADFORD_D50_TO_D65 = np.array([
    [0.9555766, -0.0230393, 0.0631636],
    [-0.0282895, 1.0099416, 0.0210077],
    [0.0122982, -0.0204830, 1.3299098],
], dtype=np.float32)
XYZ_D65_TO_SRGB = np.array([
    [3.2404542, -1.5371385, -0.4985314],
    [-0.9692660, 1.8760108, 0.0415560],
    [0.0556434, -0.2040259, 1.0572252],
], dtype=np.float32)
PROPHOTO_TO_SRGB = XYZ_D65_TO_SRGB @ BRADFORD_D50_TO_D65 @ PROPHOTO_TO_XYZ_D50
SRGB_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

# LibRaw 0.21.4 calls output_color=4 "ProPhoto D65". It applies the
# prophoto_rgb matrix to its internal linear D65 sRGB camera conversion:
# https://raw.githubusercontent.com/LibRaw/LibRaw/0.21.4/src/tables/colorconst.cpp
# https://raw.githubusercontent.com/LibRaw/LibRaw/0.21.4/src/postprocessing/postprocessing_utils_dcrdefs.cpp
# Its D65-adapted ProPhoto primary basis is almost, but not exactly, equivalent
# to our standard D50 ProPhoto coordinates after Bradford adaptation. Normalize
# explicitly so the working-space contract is canonical ProPhoto D50.
# This fixed matrix was calculated in float64 as
# inv(PROPHOTO_TO_XYZ_D50) @ inv(BRADFORD_D50_TO_D65)
# @ LibRaw.xyz_rgb @ inv(LibRaw.prophoto_rgb), then rounded to float32.
# The two LibRaw matrices are fixed constants in the source URLs above.
LIBRAW_PROPHOTO_NORMALIZATION_VERSION = "1.0.0"
LIBRAW_PROPHOTO_TO_CANONICAL_D50 = np.array([
    [1.0000631954014969, -0.00004362032456897, -0.00001656829739023],
    [0.00001390262420754, 0.99999117688357031, -0.00000702307717976],
    [0.00000644240948927, 0.00001056271615851, 0.99998399158359497],
], dtype=np.float32)
LIBRAW_PROPHOTO_TO_CANONICAL_D50.flags.writeable = False
LIBRAW_PROPHOTO_NORMALIZATION_SHA256 = hashlib.sha256(
    LIBRAW_PROPHOTO_TO_CANONICAL_D50.astype("<f4", copy=False).tobytes()).hexdigest()


def _rgb_array(rgb: np.ndarray) -> np.ndarray:
    image = np.asarray(rgb, dtype=np.float32)
    if image.ndim < 2 or image.shape[-1] != 3 or not image.size:
        raise ValueError("RGB 데이터는 마지막 축이 3인 비어 있지 않은 배열이어야 합니다.")
    # Scan in chunks; a full-resolution boolean mask would consume extra RAM.
    flat = image.reshape(-1)
    for start in range(0, image.size, CHUNK_PIXELS * 3):
        if not np.isfinite(flat[start:start + CHUNK_PIXELS * 3]).all():
            raise ValueError("RGB 데이터에 NaN 또는 무한대가 있습니다.")
    return image


def srgb_encode(linear: np.ndarray) -> np.ndarray:
    """Encode linear sRGB once. Input is clipped only at the transfer boundary."""
    linear = np.clip(np.asarray(linear, dtype=np.float32), 0, 1)
    return np.where(linear <= 0.0031308, linear * 12.92,
                    1.055 * np.power(linear, 1 / 2.4) - 0.055).astype(np.float32)


def srgb_decode(encoded: np.ndarray) -> np.ndarray:
    """Decode encoded sRGB once for explicitly linear-light operations."""
    encoded = np.clip(np.asarray(encoded, dtype=np.float32), 0, 1)
    return np.where(encoded <= 0.04045, encoded / 12.92,
                    np.power((encoded + 0.055) / 1.055, 2.4)).astype(np.float32)


def gamut_compress(rgb: np.ndarray) -> np.ndarray:
    """Smoothly reduce chroma toward equal-luminance grey inside the sRGB cube.

    The knee begins at 80% of the available chroma in the current hue direction.
    It approaches the gamut boundary smoothly instead of clipping channels.
    """
    luminance = np.clip(rgb @ SRGB_LUMA, 0, 1)[..., None]
    delta = rgb - luminance
    excursion = np.max(np.where(delta >= 0, delta / np.maximum(1 - luminance, 1e-7),
                                -delta / np.maximum(luminance, 1e-7)), axis=-1, keepdims=True)
    soft = 0.8 + 0.2 * (-np.expm1(-np.maximum(excursion - 0.8, 0) / 0.2))
    factor = np.where(excursion <= 0.8, 1, soft / np.maximum(excursion, 1e-7))
    return np.clip(luminance + delta * factor, 0, 1).astype(np.float32)


def normalize_libraw_prophoto(rgb: np.ndarray) -> np.ndarray:
    """Normalize LibRaw's linear ProPhoto D65 coordinates to canonical D50.

    Pass the float32-normalized 16-bit decoder output. This transformation does
    not clip: tiny negative/above-one values must survive until the baseline's
    tone and smooth gamut handling. The version and matrix hash belong in the
    RAW-processing recipe and baseline cache key. This is not a second white
    balance, and it cannot undo clipping in LibRaw's integer RGB output.
    """
    image = _rgb_array(rgb)
    source = image.reshape(-1, 3)
    output = np.empty_like(source)
    for start in range(0, len(source), CHUNK_PIXELS):
        output[start:start + CHUNK_PIXELS] = (
            source[start:start + CHUNK_PIXELS] @ LIBRAW_PROPHOTO_TO_CANONICAL_D50.T)
    return output.reshape(image.shape)


def baseline_render(linear_prophoto: np.ndarray) -> np.ndarray:
    """Render linear D50 ProPhoto to encoded D65 sRGB, baseline version 1.0.0.

    Order: explicit colourimetric transform, luminance tone curve
    T(Y)=1.25*Y/(1+0.25*Y), smooth chroma compression, sRGB encoding.
    The curve preserves black/white, raises middle tones mildly, and is recorded
    in the baseline profile asset. It cannot recover decoder-clipped highlights.
    """
    image = _rgb_array(linear_prophoto)
    source = image.reshape(-1, 3)
    output = np.empty_like(source)
    for start in range(0, len(source), CHUNK_PIXELS):
        part = source[start:start + CHUNK_PIXELS] @ PROPHOTO_TO_SRGB.T
        luminance = np.maximum(part @ SRGB_LUMA, 0)
        tone = np.minimum(1.25 * luminance / (1 + 0.25 * luminance), 1)
        part *= (tone / np.maximum(luminance, 1e-8))[:, None]
        output[start:start + CHUNK_PIXELS] = srgb_encode(gamut_compress(part))
    return output.reshape(image.shape)


def apply_adjustments(encoded_rgb: np.ndarray, settings: Mapping) -> np.ndarray:
    """Apply post-style tone controls and linear-light saturation.

    Exposure and relative WB belong exclusively to RAW development. Contrast and
    tonal masks use encoded sRGB; saturation is explicitly decoded to linear
    sRGB and re-encoded exactly once. Neutral controls are a bit-exact identity.
    """
    image = _rgb_array(encoded_rgb)
    contrast = float(settings.get("contrast", 0)) / 100
    saturation = float(settings.get("saturation", 0)) / 100
    highlights = float(settings.get("highlights", 0)) / 100
    shadows = float(settings.get("shadows", 0)) / 100
    if settings.get("style_id") == "monochrome":
        saturation = 0
    if not all(np.isfinite(x) and -1 <= x <= 1 for x in (contrast, saturation, highlights, shadows)):
        raise ValueError("명암 및 채도 보정값은 -100~100이어야 합니다.")
    if contrast == saturation == highlights == shadows == 0:
        return image.copy()
    source = image.reshape(-1, 3)
    output = np.empty_like(source)
    for start in range(0, len(source), CHUNK_PIXELS):
        part = np.clip(source[start:start + CHUNK_PIXELS], 0, 1)
        # An endpoint-preserving sigmoid; a zero setting takes the identity path.
        if contrast:
            power = np.float32(2 ** contrast)
            numerator = np.power(part, power)
            part = numerator / np.maximum(numerator + np.power(1 - part, power), 1e-8)
        if highlights or shadows:
            y = part @ SRGB_LUMA
            delta = (highlights * 0.8 * y ** 2 + shadows * 0.8 * (1 - y) ** 2) * y * (1 - y)
            # Equal channel offset retains neutral greys and keeps endpoints fixed.
            part = np.clip(part + delta[:, None], 0, 1)
        if saturation:
            linear = srgb_decode(part)
            y = (linear @ SRGB_LUMA)[:, None]
            linear = y + (linear - y) * (1 + saturation)
            part = srgb_encode(gamut_compress(linear))
        output[start:start + CHUNK_PIXELS] = part
    return output.reshape(image.shape)


def resize_rgb(rgb: np.ndarray, long_edge: int | None) -> np.ndarray:
    """Lanczos resize in Pillow float mode; never quantize to 8 bits or upscale."""
    image = _rgb_array(rgb)
    if image.ndim != 3:
        raise ValueError("크기 변경에는 H×W×3 배열이 필요합니다.")
    if long_edge is None:
        return image
    if isinstance(long_edge, bool) or int(long_edge) != long_edge or long_edge < 1:
        raise ValueError("긴 변은 양의 정수여야 합니다.")
    height, width = image.shape[:2]
    ratio = min(1.0, int(long_edge) / max(height, width))
    if ratio == 1:
        return image
    size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    output = np.empty((size[1], size[0], 3), dtype=np.float32)
    for channel in range(3):
        plane = Image.fromarray(np.ascontiguousarray(image[..., channel]))
        output[..., channel] = np.asarray(plane.resize(size, Image.Resampling.LANCZOS), dtype=np.float32)
    return np.clip(output, 0, 1, out=output)


def _coordinate_noise(x: np.ndarray, y: np.ndarray, seed: int) -> np.ndarray:
    """Stateless hash noise: independent of array shape and tile boundaries."""
    with np.errstate(over="ignore"):
        value = (x.astype(np.uint32) * np.uint32(0x1F123BB5)) ^ (y.astype(np.uint32) * np.uint32(0x5F356495)) ^ np.uint32(seed)
        value ^= value >> np.uint32(16)
        value *= np.uint32(0x7FEB352D)
        value ^= value >> np.uint32(15)
        value *= np.uint32(0x846CA68B)
        value ^= value >> np.uint32(16)
    return ((value >> np.uint32(8)).astype(np.float32) / np.float32(8388607.5) - 1)


def apply_grain(rgb: np.ndarray, settings: Mapping, source_hash: str,
                original_size: tuple[int, int] | None = None,
                origin: tuple[float, float] = (0, 0)) -> np.ndarray:
    """Add reproducible monochrome grain in original-image coordinates.

    ``original_size`` is (width, height). For a complete reduced image the source
    scale is inferred. For source-resolution tiles pass their source ``origin``
    and settings['_grain_scale']=1. Reduced tiles may pass (sx, sy) instead.
    Grain cells are 1.25 or 3 original pixels. Pixel centres and a continuous
    bilinear noise field make crops agree exactly when the same scale is used.
    Reduction attenuates grain to approximate integration over the pixel area.
    """
    image = _rgb_array(rgb)
    if image.ndim != 3:
        raise ValueError("입자 처리에는 H×W×3 배열이 필요합니다.")
    grain = settings.get("grain", {})
    amount = float(grain.get("amount", 0))
    if not np.isfinite(amount) or not 0 <= amount <= 100:
        raise ValueError("입자감은 0~100이어야 합니다.")
    if amount == 0:
        return image.copy()
    size = grain.get("size", "small")
    if size not in ("small", "large"):
        raise ValueError("알 수 없는 입자 크기입니다.")
    seed = int(grain.get("seed", 12345))
    mixed_seed = int.from_bytes(hashlib.sha256(f"{source_hash}:{seed}".encode("utf-8")).digest()[:4], "little")
    height, width = image.shape[:2]
    original_size = original_size or (width, height)
    explicit_scale = settings.get("_grain_scale")
    if explicit_scale is not None:
        sx, sy = (explicit_scale, explicit_scale) if np.isscalar(explicit_scale) else explicit_scale
    elif origin != (0, 0):
        sx, sy = 1.0, 1.0
    else:
        sx, sy = original_size[0] / width, original_size[1] / height
    if not all(np.isfinite(v) and v > 0 for v in (sx, sy)):
        raise ValueError("입자 좌표 배율은 양수여야 합니다.")
    cell = 1.25 if size == "small" else 3.0
    amplitude = np.float32(amount / 100 * 0.055 / np.sqrt(max(1.0, sx * sy / (cell * cell))))
    output = np.empty_like(image)
    rows = max(1, CHUNK_PIXELS // width)
    x = (np.arange(width, dtype=np.float64) * sx + float(origin[0]) + sx / 2) / cell
    xi, xf = np.floor(x).astype(np.int64)[None, :], (x - np.floor(x)).astype(np.float32)[None, :]
    for top in range(0, height, rows):
        bottom = min(height, top + rows)
        y = (np.arange(top, bottom, dtype=np.float64) * sy + float(origin[1]) + sy / 2) / cell
        yi, yf = np.floor(y).astype(np.int64)[:, None], (y - np.floor(y)).astype(np.float32)[:, None]
        n00 = _coordinate_noise(xi, yi, mixed_seed)
        n10 = _coordinate_noise(xi + 1, yi, mixed_seed)
        n01 = _coordinate_noise(xi, yi + 1, mixed_seed)
        n11 = _coordinate_noise(xi + 1, yi + 1, mixed_seed)
        noise = ((n00 * (1 - xf) + n10 * xf) * (1 - yf) + (n01 * (1 - xf) + n11 * xf) * yf)
        part = image[top:bottom]
        luminance = np.clip(part @ SRGB_LUMA, 0, 1)
        envelope = 0.35 + 0.65 * np.sqrt(4 * luminance * (1 - luminance))
        if settings.get("style_id") == "monochrome":
            envelope *= 1.15 - 0.3 * luminance
        output[top:bottom] = np.clip(part + (noise * amplitude * envelope)[..., None], 0, 1)
    return output
