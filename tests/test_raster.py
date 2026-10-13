"""Real synthetic-file regression checks for the shared raster image pipeline.

These tests exercise decode/render/export through the public engine functions;
the baseline memmap is read directly where 8-bit previews would hide precision.
No camera RAW sample or external ICC fixture is required.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageCms, ImageOps, TiffImagePlugin
import pytest
import tifffile

from film_lab import engine
from film_lab.cache import DiskCache
from film_lab.color import srgb_decode, srgb_encode
from film_lab.models import default_settings, sha256_file


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    sources, outputs, cache = (tmp_path / name for name in ("한글 원본", "결과", "cache"))
    sources.mkdir()
    monkeypatch.setenv("FILM_LAB_CACHE", str(cache))
    # Keep every test independent from earlier source inspections.
    monkeypatch.setattr(engine, "_INSPECTION_CACHE", engine.OrderedDict())
    return SimpleNamespace(sources=sources, outputs=outputs, cache=cache)


def _pattern(width=43, height=29):
    y, x = np.mgrid[:height, :width]
    return np.stack([(x * 13 + y * 7) % 256,
                     (x * 5 + y * 19) % 256,
                     (x * 23 + y * 3) % 256], axis=-1).astype(np.uint8)


def _save(workspace, name="사진.png", data=None, **kwargs):
    path = workspace.sources / name
    Image.fromarray(_pattern() if data is None else data).save(path, **kwargs)
    return path


def _baseline(path, workspace, settings=None, source=None):
    info = engine.inspect(path) if source is None else source
    array, metadata, key = engine._baseline(info, settings or default_settings(),
                                             DiskCache(workspace.cache), None)
    try:
        return np.array(array, copy=True), metadata, key
    finally:
        engine._close_map(array)


def _pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).copy()


def _custom_rgb_icc():
    """A valid RGB matrix profile with a deliberately different primary basis.

    Shift two colourant XYZ tags by opposite amounts, keeping their sum/white
    point fixed. LittleCMS is the independent reference transform in the test.
    """
    data = bytearray(engine._icc())
    count = struct.unpack_from(">I", data, 128)[0]
    tags = {}
    for index in range(count):
        signature, offset, length = struct.unpack_from(">4sII", data, 132 + 12 * index)
        tags[signature] = (offset, length)
    for signature, sign in ((b"rXYZ", 1), (b"gXYZ", -1)):
        offset, _ = tags[signature]
        for component, delta in ((0, .025), (1, .015)):
            position = offset + 8 + component * 4
            value = struct.unpack_from(">i", data, position)[0]
            struct.pack_into(">i", data, position, value + sign * round(delta * 65536))
    # The original profile ID is no longer valid for this synthetic profile.
    data[84:100] = b"\x00" * 16
    return bytes(data)


@pytest.mark.parametrize("extension,fmt", [
    ("jpg", "JPEG"), ("JpEg", "JPEG"), ("png", "PNG"), ("PNG", "PNG"),
    ("tif", "TIFF"), ("TiFf", "TIFF"), ("webp", "WEBP"), ("WeBp", "WEBP"),
    ("bmp", "BMP"), ("BMP", "BMP"),
])
def test_supported_formats_inspect_render_export_without_raw_decoder(workspace, monkeypatch, extension, fmt):
    options = {"quality": 97, "subsampling": 0} if fmt == "JPEG" else {"lossless": True} if fmt == "WEBP" else {}
    path = _save(workspace, f"장면.{extension}", format=fmt, **options)
    original_hash = sha256_file(path)
    original_listing = set(workspace.sources.iterdir())
    decoded = _pixels(path)
    # Calling LibRaw for a normal image is a regression, even if installed.
    monkeypatch.setattr(engine, "_rawpy", lambda: pytest.fail("Raster processing must not invoke LibRaw"))
    source = engine.inspect(path)
    assert source["source_kind"] == "raster"
    assert source["image_format"] == fmt
    assert source["supported"] is True
    assert (source["width"], source["height"]) == (43, 29)
    assert source["sha256"] == original_hash
    assert source["size_bytes"] == path.stat().st_size
    assert source["mtime_ns"] == path.stat().st_mtime_ns
    result = engine.render(path, default_settings(), request_id="raster-test", cache_dir=workspace.cache)
    assert result["request_id"] == "raster-test"
    np.testing.assert_array_equal(_pixels(result["path"]), decoded)
    np.testing.assert_array_equal(_pixels(result["baseline_path"]), decoded)
    saved = engine.export_item(path, default_settings(), {"output_dir": str(workspace.outputs), "format": "tiff"})
    assert saved["status"] == "completed", saved
    with tifffile.TiffFile(saved["path"]) as output:
        assert output.pages[0].dtype == np.dtype("uint16")
        np.testing.assert_array_equal(output.asarray(), decoded.astype(np.uint16) * 257)
        assert output.pages[0].tags[34675].value
    recipe = json.loads(Path(saved["recipe_path"]).read_text(encoding="utf-8"))
    assert recipe["source"]["source_kind"] == "raster"
    assert recipe["source"]["image_format"] == fmt
    assert sha256_file(path) == original_hash
    assert set(workspace.sources.iterdir()) == original_listing


def test_raster_default_baseline_keeps_srgb_tones_exact(workspace):
    ramp = np.repeat(np.arange(256, dtype=np.uint8)[None, :, None], 3, axis=2)
    path = _save(workspace, data=ramp)
    actual, _, _ = _baseline(path, workspace)
    np.testing.assert_array_equal(actual, ramp.astype(np.float32) / np.float32(255))
    # A second RAW tone curve or another sRGB transfer function would move this.
    np.testing.assert_array_equal(actual[0, 128], np.full(3, 128 / 255, dtype=np.float32))


def test_raster_exposure_and_relative_white_balance_are_linear_light(workspace):
    pixels = np.full((13, 17, 3), 64, dtype=np.uint8)
    path = _save(workspace, data=pixels)
    baseline, _, neutral_key = _baseline(path, workspace)
    settings = default_settings()
    settings["exposure_ev"] = 1
    exposed, _, exposure_key = _baseline(path, workspace, settings)
    expected = srgb_encode(srgb_decode(baseline) * 2)
    np.testing.assert_allclose(exposed, expected, atol=2e-6, rtol=0)
    assert exposure_key != neutral_key
    settings = default_settings()
    settings.update(wb_mode="relative", warmth=80, tint=0)
    warm, _, warmth_key = _baseline(path, workspace, settings)
    assert np.all(warm[..., 0] > warm[..., 2])
    assert not np.array_equal(warm, baseline)
    assert warmth_key not in (neutral_key, exposure_key)
    settings.update(warmth=0, tint=80)
    tint, _, _ = _baseline(path, workspace, settings)
    assert np.all(tint[..., 1] < tint[..., 0])
    assert np.all(tint[..., 1] < tint[..., 2])


def test_styles_apply_to_raster_and_zero_strength_matches_baseline(workspace):
    path = _save(workspace)
    settings = default_settings()
    settings["style_id"] = "documentary"
    settings["strength"] = 0
    zero = engine.render(path, settings, cache_dir=workspace.cache)
    np.testing.assert_array_equal(_pixels(zero["path"]), _pixels(zero["baseline_path"]))
    settings["strength"] = 1
    styled = engine.render(path, settings, cache_dir=workspace.cache)
    assert styled["path"] != zero["path"]
    assert styled["baseline_path"] == zero["baseline_path"]
    assert np.mean(np.abs(_pixels(styled["path"]).astype(float) - _pixels(zero["path"]))) > 1
    settings["style_id"] = "monochrome"
    mono = _pixels(engine.render(path, settings, cache_dir=workspace.cache)["path"])
    np.testing.assert_array_equal(mono[..., 0], mono[..., 1])
    np.testing.assert_array_equal(mono[..., 1], mono[..., 2])


@pytest.mark.parametrize("orientation", range(1, 9))
def test_exif_orientation_is_applied_once_to_dimensions_and_pixels(workspace, orientation):
    exif = Image.Exif()
    exif[274] = orientation
    path = _save(workspace, "방향.jpg", data=_pattern(17, 11), exif=exif, quality=100, subsampling=0)
    with Image.open(path) as original:
        expected = np.asarray(ImageOps.exif_transpose(original).convert("RGB")).copy()
    source = engine.inspect(path)
    assert (source["width"], source["height"]) == (expected.shape[1], expected.shape[0])
    actual, _, _ = _baseline(path, workspace)
    np.testing.assert_allclose(actual, expected.astype(np.float32) / 255, atol=1e-7)
    saved = engine.export_item(path, default_settings(), {"output_dir": str(workspace.outputs), "format": "tiff"})
    assert saved["status"] == "completed", saved
    with tifffile.TiffFile(saved["path"]) as output:
        assert output.pages[0].tags[274].value == 1
        np.testing.assert_array_equal(output.asarray(), expected.astype(np.uint16) * 257)


@pytest.mark.parametrize("mode", ["L", "RGBA", "LA", "P"])
def test_gray_and_transparency_composite_on_white(workspace, mode):
    if mode == "L":
        original = Image.fromarray(np.array([[0, 73, 255]], dtype=np.uint8))
    elif mode == "RGBA":
        original = Image.fromarray(np.array([[[20, 80, 160, 255], [20, 80, 160, 127], [20, 80, 160, 0]]], dtype=np.uint8))
    elif mode == "LA":
        original = Image.fromarray(np.array([[[20, 255], [80, 127], [160, 0]]], dtype=np.uint8), mode="LA")
    else:
        original = Image.fromarray(np.array([[0, 1, 2]], dtype=np.uint8), mode="P")
        original.putpalette([20, 80, 160, 80, 160, 20, 160, 20, 80] + [0] * (768 - 9))
        original.info["transparency"] = bytes([255, 127, 0])
    path = workspace.sources / (mode + ".png")
    original.save(path)
    rgba = original.convert("RGBA")
    expected = np.asarray(Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB"))
    actual, _, _ = _baseline(path, workspace)
    np.testing.assert_allclose(actual, expected.astype(np.float32) / 255, atol=.51 / 255, rtol=0)
    if mode != "L":
        np.testing.assert_array_equal(actual[0, 2], np.ones(3, dtype=np.float32))


def test_missing_profile_warns_and_valid_srgb_profile_is_identity(workspace):
    pixels = _pattern()
    untagged = _save(workspace, "untagged.png", data=pixels)
    tagged = _save(workspace, "srgb.png", data=pixels, icc_profile=engine._icc())
    actual, metadata, _ = _baseline(untagged, workspace)
    assert metadata["warnings"], "An assumed input colour space must be visible in the recipe"
    tagged_rgb, _, _ = _baseline(tagged, workspace)
    np.testing.assert_array_equal(actual, tagged_rgb)


def test_non_srgb_rgb_profile_is_converted_to_srgb(workspace):
    profile = _custom_rgb_icc()
    path = _save(workspace, "custom-profile.png", icc_profile=profile)
    with Image.open(path) as original:
        import io
        expected = ImageCms.profileToProfile(original,
            ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB")
        expected = np.asarray(expected).copy()
    assert np.max(np.abs(expected.astype(int) - _pattern().astype(int))) > 2
    actual, _, _ = _baseline(path, workspace)
    np.testing.assert_allclose(actual, expected.astype(np.float32) / 255, atol=1 / 255, rtol=0)


def test_bmp_v5_embedded_icc_is_used_instead_of_assuming_srgb(workspace):
    # Pillow does not expose the V5 embedded profile through image.info. Build
    # the actual header so this checks the importer's format-specific ICC read.
    import io
    profile = _custom_rgb_icc()
    pixels = np.array([[[150, 80, 20], [40, 160, 100]]], dtype=np.uint8)
    pixel_bytes = pixels[..., ::-1].tobytes() + bytes(2)  # BGR, row padded to 4 bytes.
    dib = bytearray(124)
    struct.pack_into("<IiiHHIIiiII", dib, 0, 124, 2, 1, 1, 24, 0, len(pixel_bytes), 0, 0, 0, 0)
    struct.pack_into("<I", dib, 56, 0x4D424544)  # PROFILE_EMBEDDED.
    struct.pack_into("<IIII", dib, 108, 4, 124 + len(pixel_bytes), len(profile), 0)
    header = struct.pack("<2sIHHI", b"BM", 14 + len(dib) + len(pixel_bytes) + len(profile), 0, 0, 14 + len(dib))
    path = workspace.sources / "embedded-profile.BMP"
    path.write_bytes(header + dib + pixel_bytes + profile)
    expected = np.asarray(ImageCms.profileToProfile(Image.fromarray(pixels),
        ImageCms.ImageCmsProfile(io.BytesIO(profile)), ImageCms.createProfile("sRGB"), outputMode="RGB"))
    assert not np.array_equal(expected, pixels)
    source = engine.inspect(path)
    assert source["color_policy"] == "converted_embedded_ICC_to_sRGB"
    actual, _, _ = _baseline(path, workspace, source=source)
    np.testing.assert_allclose(actual, expected.astype(np.float32) / 255, atol=1 / 255, rtol=0)


def test_invalid_icc_is_an_explicit_error(workspace):
    path = _save(workspace, "broken-profile.png", icc_profile=b"This is not an ICC profile")
    with pytest.raises(ValueError, match="ICC|프로필"):
        engine.inspect(path)


def test_lab_tiff_requires_profile_and_converts_when_tagged(workspace):
    pixels = np.array([[[50, 128, 128], [120, 170, 95], [210, 100, 180]]], dtype=np.uint8)
    lab = Image.fromarray(pixels, mode="LAB")
    untagged = workspace.sources / "untagged-lab.tif"
    lab.save(untagged)
    with pytest.raises(ValueError, match="ICC|프로필"):
        engine.inspect(untagged)
    tagged = workspace.sources / "tagged-lab.tif"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
    lab.save(tagged, icc_profile=profile.tobytes())
    expected = np.asarray(ImageCms.profileToProfile(lab, profile, ImageCms.createProfile("sRGB"), outputMode="RGB"))
    actual, _, _ = _baseline(tagged, workspace)
    np.testing.assert_allclose(actual, expected.astype(np.float32) / 255, atol=1 / 255, rtol=0)


def test_untagged_cmyk_image_is_rejected_without_guessing_profile(workspace):
    path = workspace.sources / "untagged-cmyk.jpg"
    Image.new("CMYK", (17, 13), (80, 20, 0, 30)).save(path)
    with pytest.raises(ValueError, match="ICC|프로필"):
        engine.inspect(path)


@pytest.mark.parametrize("with_icc", [False, True])
def test_16bit_tiff_preserves_sub_8bit_steps_in_baseline_and_export(workspace, with_icc):
    ramp = np.arange(10000, 11024, dtype=np.uint16)
    pixels = np.tile(np.stack([ramp, ramp + 7000, ramp + 23000], axis=-1), (7, 1, 1))
    path = workspace.sources / "정밀도16.TIFF"
    extra = [(34675, "B", 0, engine._icc(), False)] if with_icc else []
    tifffile.imwrite(path, pixels, photometric="rgb", metadata=None, extratags=extra)
    actual, _, _ = _baseline(path, workspace)
    np.testing.assert_allclose(actual, pixels.astype(np.float32) / 65535, atol=1e-7, rtol=0)
    assert len(np.unique(actual[..., 0])) == 1024
    saved = engine.export_item(path, default_settings(), {"output_dir": str(workspace.outputs), "format": "tiff"})
    assert saved["status"] == "completed", saved
    np.testing.assert_array_equal(tifffile.imread(saved["path"]), pixels)


@pytest.mark.parametrize("photometric", ["minisblack", "miniswhite"])
def test_16bit_gray_tiff_polarity(workspace, photometric):
    pixels = np.array([[0, 1, 32000, 65534, 65535]], dtype=np.uint16)
    path = workspace.sources / (photometric + ".tif")
    tifffile.imwrite(path, pixels, photometric=photometric, metadata=None)
    actual, _, _ = _baseline(path, workspace)
    expected = pixels.astype(np.float32) / 65535
    if photometric == "miniswhite":
        expected = 1 - expected
    np.testing.assert_allclose(actual, np.repeat(expected[..., None], 3, axis=-1), atol=1e-7)


def test_16bit_planar_rgb_and_alpha_tiff(workspace):
    pixels = np.array([[[10000, 20000, 30000], [13000, 23000, 33000]]], dtype=np.uint16)
    planar_path = workspace.sources / "planar.tif"
    tifffile.imwrite(planar_path, pixels.transpose(2, 0, 1), photometric="rgb", planarconfig="separate", metadata=None)
    planar, _, _ = _baseline(planar_path, workspace)
    np.testing.assert_allclose(planar, pixels.astype(np.float32) / 65535, atol=1e-7)
    rgba_path = workspace.sources / "rgba16.tif"
    alpha = np.array([[[65535], [0]]], dtype=np.uint16)
    tifffile.imwrite(rgba_path, np.concatenate([pixels, alpha], axis=-1), photometric="rgb", extrasamples="unassalpha", metadata=None)
    rgba, _, _ = _baseline(rgba_path, workspace)
    np.testing.assert_allclose(rgba[0, 0], pixels[0, 0].astype(np.float32) / 65535, atol=1e-7)
    np.testing.assert_array_equal(rgba[0, 1], np.ones(3, dtype=np.float32))


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
@pytest.mark.parametrize("photometric", ["minisblack", "miniswhite"])
def test_gray_tiff_with_associated_alpha_keeps_precision(workspace, dtype, photometric):
    # Premultiplied samples for 0%, 25%, 50%, 100% grey at half opacity.
    maximum = np.iinfo(dtype).max
    alpha = (maximum + 1) // 2
    values = np.array([0, alpha // 4, alpha // 2, alpha], dtype=dtype)
    pixels = np.stack([values, np.full_like(values, alpha)], axis=-1)[None]
    path = workspace.sources / f"grey-alpha-{photometric}.tiff"
    tifffile.imwrite(path, pixels, photometric=photometric, extrasamples="assocalpha", metadata=None)
    expected_gray = values.astype(np.float64) / alpha
    if photometric == "miniswhite":
        expected_gray = 1 - expected_gray
    expected = expected_gray * (alpha / maximum) + (1 - alpha / maximum)
    actual, _, _ = _baseline(path, workspace)
    np.testing.assert_allclose(actual, np.repeat(expected[None, :, None], 3, axis=-1), atol=1e-7, rtol=0)


def test_tiff_direct_decoder_retains_pixel_limit(workspace, monkeypatch):
    path = workspace.sources / "pixel-limit.tiff"
    tifffile.imwrite(path, _pattern(9, 7), photometric="rgb", metadata=None)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 8)
    with pytest.raises(ValueError):
        engine.inspect(path)


def test_unsupported_high_bit_colour_conversion_does_not_reduce_to_8bit(workspace):
    path = workspace.sources / "custom16.tif"
    tifffile.imwrite(path, np.full((7, 9, 3), 12345, dtype=np.uint16), photometric="rgb", metadata=None,
                     extratags=[(34675, "B", 0, _custom_rgb_icc(), False)])
    with pytest.raises(ValueError, match="16|ICC|프로필"):
        engine.inspect(path)


@pytest.mark.parametrize("dtype", [np.float32, np.int32])
def test_unsupported_tiff_numeric_formats_are_rejected(workspace, dtype):
    path = workspace.sources / "unsupported.tif"
    tifffile.imwrite(path, np.ones((7, 9, 3), dtype=dtype), photometric="rgb", metadata=None)
    with pytest.raises(ValueError):
        engine.inspect(path)


def test_16bit_png_is_rejected_before_precision_can_be_lost(workspace):
    path = _save(workspace, "highbit.png", data=np.arange(300, dtype=np.uint16).reshape(15, 20))
    with pytest.raises(ValueError, match="16|비트"):
        engine.inspect(path)


@pytest.mark.parametrize("kind", ["corrupt", "mismatched", "unsupported", "multipage", "animated"])
def test_invalid_or_ambiguous_files_fail_content_validation(workspace, kind):
    if kind == "corrupt":
        path = workspace.sources / "corrupt.jpg"
        path.write_bytes(b"\xff\xd8this is not a JPEG file")
    elif kind == "mismatched":
        path = _save(workspace, "actually-png.jpg", format="PNG")
    elif kind == "unsupported":
        path = _save(workspace, "unsupported.gif", format="GIF")
    elif kind == "multipage":
        path = workspace.sources / "multipage.tif"
        with tifffile.TiffWriter(path) as writer:
            writer.write(_pattern(), photometric="rgb", metadata=None)
            writer.write(_pattern(), photometric="rgb", metadata=None)
    else:
        path = workspace.sources / "animated.webp"
        Image.fromarray(_pattern()).save(path, save_all=True, append_images=[Image.fromarray(255 - _pattern())], duration=80, loop=0)
    with pytest.raises(ValueError):
        engine.inspect(path)


def test_safe_metadata_export_works_for_non_sony_camera_images(workspace):
    exif = Image.Exif()
    exif[271], exif[272], exif[274] = "Other maker", "Ordinary camera", 6
    rational = TiffImagePlugin.IFDRational
    exif[34665] = {36867: "2026:10:13 09:10:11", 33434: rational(1, 125), 33437: rational(28, 10),
                   34855: 320, 37386: rational(35, 1), 42033: "SECRET_BODY_SERIAL"}
    exif[34853] = {1: "N", 2: (1.0, 2.0, 3.0)}
    path = _save(workspace, "metadata.jpg", exif=exif)
    source = engine.inspect(path)
    assert source["supported"]
    assert source["camera_model"] == "Ordinary camera"
    assert "SECRET_BODY_SERIAL" not in json.dumps(source)
    saved = engine.export_item(path, default_settings(), {"output_dir": str(workspace.outputs), "format": "jpeg"})
    assert saved["status"] == "completed", saved
    with Image.open(saved["path"]) as image:
        output = image.getexif()
        assert output[274] == 1
        assert output[272] == "Ordinary camera"
        assert 34853 not in output
        details = output.get_ifd(34665)
        assert details[34855] == 320
        assert details[36867] == "2026:10:13 09:10:11"
        assert float(details[33434]) == pytest.approx(1 / 125)
        assert float(details[33437]) == pytest.approx(2.8)
        assert float(details[37386]) == pytest.approx(35)
        assert 42033 not in details
        assert image.info["icc_profile"]
    recipe_text = Path(saved["recipe_path"]).read_text(encoding="utf-8")
    assert "SECRET_BODY_SERIAL" not in recipe_text


def test_raster_cache_settings_identity_and_source_kind_separation(workspace, monkeypatch):
    path = _save(workspace, data=np.full((13, 17, 3), 64, dtype=np.uint8))
    source = engine.inspect(path)
    pixels, _, key = _baseline(path, workspace, source=source)
    again, _, same_key = _baseline(path, workspace, source=source)
    assert same_key == key
    np.testing.assert_array_equal(again, pixels)
    styled_settings = default_settings()
    styled_settings["style_id"] = "vivid"
    _, _, style_key = _baseline(path, workspace, styled_settings, source)
    assert style_key == key  # A style change reuses the unstyled raster baseline.

    import rawpy
    class SyntheticRaw:
        camera_whitebalance = [2., 1., 1.5, 1.]
        camera_white_level_per_channel = [65535] * 4
        white_level = 65535
        raw_image_visible = np.full((13, 17), 10000, dtype=np.uint16)
        raw_colors_visible = np.zeros((13, 17), dtype=np.uint8)
        black_level_per_channel = [0] * 4
        auto_whitebalance = [2., 1., 1.5, 1.]
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def postprocess(self, **_): return np.full((13, 17, 3), 10000, dtype=np.uint16)
    fake_decoder = SimpleNamespace(libraw_version=rawpy.libraw_version, imread=lambda _: SyntheticRaw(),
        ColorSpace=rawpy.ColorSpace, DemosaicAlgorithm=rawpy.DemosaicAlgorithm, HighlightMode=rawpy.HighlightMode)
    monkeypatch.setattr(engine, "_rawpy", lambda: fake_decoder)
    raw_source = copy.deepcopy(source)
    raw_source.update(source_kind="raw", image_format="ARW", camera_model="ILCE-6400")
    raw_pixels, _, raw_key = _baseline(path, workspace, source=raw_source)
    assert raw_key != key
    assert not np.array_equal(raw_pixels, pixels)


def test_raster_original_folder_protection_and_fingerprint_recheck(workspace):
    path = _save(workspace, "protected.bmp")
    before = path.read_bytes()
    source = engine.inspect(path)
    with pytest.raises(ValueError):
        engine.render(path, default_settings(), cache_dir=workspace.sources)
    failed = engine.export_item(path, default_settings(), {"output_dir": str(workspace.sources)})
    assert failed["status"] == "failed"
    assert path.read_bytes() == before
    assert set(workspace.sources.iterdir()) == {path}
    stat = path.stat()
    changed = bytearray(before)
    changed[-4] ^= 1  # A BMP pixel/padding byte; preserve a decodable file and size.
    path.write_bytes(changed)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert engine.inspect(path)["sha256"] != source["sha256"]
    with pytest.raises(ValueError, match="다시 연결"):
        engine.render(path, {"settings": default_settings(), "source": source}, cache_dir=workspace.cache)


def test_raw_camera_restriction_is_not_relaxed_by_raster_support(workspace, monkeypatch):
    path = workspace.sources / "unsupported-camera.ARW"
    path.write_bytes(b"RAW model-gate fixture; decoder is stubbed only for this test")
    class RawInspection:
        sizes = SimpleNamespace(width=17, height=13, flip=0)
        raw_image_visible = np.ones((13, 17), dtype=np.uint16)
        num_colors = 3
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(engine, "_metadata", lambda _: {"camera_model": "ILCE-7M4", "make": "SONY"})
    monkeypatch.setattr(engine, "_rawpy", lambda: SimpleNamespace(imread=lambda _: RawInspection()))
    info = engine.inspect(path)
    assert info["source_kind"] == "raw"
    assert info["image_format"] == "ARW"
    assert info["supported"] is False
    with pytest.raises(ValueError, match="ILCE-6400|지원"):
        engine.render(path, default_settings(), cache_dir=workspace.cache)
