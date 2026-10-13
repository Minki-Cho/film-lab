"""Synthetic encoder/safety tests. Real RAW integration is run separately."""
import copy
import json
import os
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
import tifffile

from film_lab import engine
from film_lab.cache import DiskCache, cache_key
from film_lab.color import apply_grain
from film_lab.models import default_settings, sha256_file


META = dict(camera_model="ILCE-6400", make="SONY", captured_at="2026:09:30 12:34:56",
            exposure_time=[1, 125], f_number=[28, 10], iso=320, focal_length=[35, 1],
            GPS="must not be copied", serial_number="must not be copied")


@pytest.mark.parametrize("fmt", ["jpeg", "tiff"])
def test_encoders_preserve_safe_metadata_icc_and_bit_depth(tmp_path, fmt):
    path = tmp_path / ("색 검증." + fmt)
    ramp = np.linspace(0, 1, 97, dtype=np.float32)
    rgb = np.tile(np.stack([ramp, ramp[::-1], ramp * 0.4], axis=-1), (53, 1, 1))
    engine._write_image(path, rgb, dict(format=fmt, quality=95), META)
    if fmt == "jpeg":
        with Image.open(path) as im:
            assert im.size == (97, 53)
            assert im.mode == "RGB"
            assert im.info["icc_profile"]
            exif = im.getexif()
            assert exif[274] == 1
            assert exif[272] == "ILCE-6400"
            assert 34853 not in exif  # GPS
            details = exif.get_ifd(34665)
            assert details[34855] == 320
            assert float(details[33434]) == pytest.approx(1 / 125)
            assert 42033 not in details  # body serial number
    else:
        with tifffile.TiffFile(path) as im:
            page = im.pages[0]
            assert page.dtype == np.dtype("uint16")
            assert page.tags[34675].value
            assert page.tags[274].value == 1
            assert page.tags[272].value == "ILCE-6400"
            details = page.tags[34665].value
            assert details["ISOSpeedRatings"] == 320
            assert details["ExposureTime"] == (1, 125)
            assert details["DateTimeOriginal"] == META["captured_at"]
            assert 34853 not in page.tags
            actual = page.asarray()
            expected = np.floor(rgb * 65535 + 0.5).astype(np.uint16)
            np.testing.assert_array_equal(actual, expected)
            assert len(np.unique(actual[..., 0])) > 80


def test_atomic_output_never_overwrites_image_or_recipe(tmp_path):
    existing = tmp_path / "photo.jpg"
    existing.write_bytes(b"existing-image")
    (tmp_path / "photo_001.recipe.json").write_text("existing-recipe")
    temporary = tmp_path / "temporary"
    temporary.write_bytes(b"new-image")
    target = engine._unique_commit(temporary, tmp_path, "photo", ".jpg", ".recipe.json")
    assert target.name == "photo_002.jpg"
    assert target.read_bytes() == b"new-image"
    assert existing.read_bytes() == b"existing-image"
    assert not temporary.exists()


def test_source_folder_rejected_before_development(tmp_path, monkeypatch):
    source = tmp_path / "original.arw"
    source.write_bytes(b"synthetic source fingerprint used only for path guard test")
    source_info = dict(path=str(source), supported=True)
    monkeypatch.setattr(engine, "_check_source", lambda *_: source_info)
    monkeypatch.setattr(engine, "_processed", lambda *_: pytest.fail("Must reject before decoding"))
    before = source.read_bytes()
    result = engine.export_item(source, default_settings(), dict(output_dir=str(tmp_path)))
    assert result["status"] == "failed"
    assert "다른 저장 폴더" in result["error"]
    assert source.read_bytes() == before
    assert len(list(tmp_path.iterdir())) == 1


def test_cache_cannot_write_into_original_folder(tmp_path, monkeypatch):
    source = tmp_path / "source.arw"
    source.write_bytes(b"original folder path guard fixture")
    monkeypatch.setattr(engine, "_check_source", lambda *_: {"path": str(source)})
    monkeypatch.setattr(engine, "_baseline", lambda *_: pytest.fail("Must reject cache before RAW decoding"))
    with pytest.raises(ValueError, match="캐시는 원본 사진 폴더와 다른 폴더"):
        engine.render(source, default_settings(), cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("field,value", [("size_bytes", 10), ("mtime_ns", 20), ("sha256", "old")])
def test_changed_source_fingerprint_rejected(monkeypatch, field, value):
    source = dict(supported=True, size_bytes=1, mtime_ns=2, sha256="new")
    monkeypatch.setattr(engine, "inspect", lambda _: source)
    with pytest.raises(ValueError, match="다시 연결"):
        engine._check_source("unused.arw", {"source": {field: value}})


def test_cancelled_batch_continues_reporting_without_reading_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "inspect", lambda _: pytest.fail("Cancelled jobs must not decode"))
    progress = []
    items = [{"source": str(tmp_path / "sources" / f"{i}.arw"), "recipe": {}} for i in range(3)]
    results = engine.export(items, dict(output_dir=str(tmp_path / "exports")), progress.append, lambda: True)
    assert [r["status"] for r in results] == ["cancelled"] * 3
    assert len(progress) == 3
    assert Path(results[0]["batch_csv"]).is_file()


def test_recipe_failure_is_partial_and_keeps_verified_image(tmp_path, monkeypatch):
    source = tmp_path / "raw" / "source.arw"
    source.parent.mkdir()
    source.write_bytes(b"only a fingerprint fixture; LibRaw is not invoked")
    stat = source.stat()
    source_info = dict(path=str(source), width=32, height=16, supported=True, sha256=sha256_file(source),
                       size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, metadata=META)
    monkeypatch.setattr(engine, "_check_source", lambda *_: source_info)
    rgb = np.full((16, 32, 3), 0.4, dtype=np.float32)
    info = dict(warnings=[], raw_processing={}, libraries={}, baseline_version="1.0.0", baseline_profile={})
    work_path = tmp_path / "work.npy"
    monkeypatch.setattr(engine, "_processed", lambda *_: (rgb, rgb.copy(), info, "a" * 64, work_path))
    monkeypatch.setattr(engine, "atomic_json", lambda *_: (_ for _ in ()).throw(OSError("sidecar failure")))
    monkeypatch.setenv("FILM_LAB_CACHE", str(tmp_path / "cache"))
    result = engine.export_item(source, default_settings(), dict(output_dir=str(tmp_path / "exports")))
    assert result["status"] == "partial"
    assert result["recipe_path"] is None
    assert result["error"] == "sidecar failure"
    with Image.open(result["path"]) as im:
        im.verify()


def test_full_grain_matches_untiled_field():
    settings = default_settings()
    settings["grain"]["amount"] = 70
    rgb = np.full((301, 193, 3), 0.4, dtype=np.float32)
    expected = apply_grain(rgb, settings, "source", original_size=(193, 301))
    actual = engine._final_rgb(rgb.copy(), settings, "source", None, None)
    np.testing.assert_array_equal(actual, expected)


def test_disk_cache_removes_oldest_and_protects_active(tmp_path):
    cache = DiskCache(tmp_path, max_bytes=20)
    entries = []
    for i in range(3):
        path = cache.path(cache_key({"i": i}))
        path.write_bytes(b"x" * 10)
        os.utime(path, (i + 1, i + 1))
        entries.append(path)
    assert cache.prune(protected=[entries[0]]) == 10
    assert entries[0].exists()
    assert not entries[1].exists()
    assert entries[2].exists()


def test_engine_version_in_recipe_is_rejected():
    with pytest.raises(ValueError, match="처리 엔진 버전"):
        engine._settings({"schema_version": 1, "engine_version": "9.0.0", "settings": default_settings()})


@pytest.mark.parametrize("broken", ["missing", "changed"])
def test_baseline_asset_is_checked_even_for_color_style(tmp_path, monkeypatch, broken):
    from film_lab import profiles
    directory = tmp_path / "profiles"
    shutil.copytree(profiles.PROFILE_ROOT / "baseline", directory / "baseline")
    monkeypatch.setattr(profiles, "PROFILE_ROOT", directory)
    curve = directory / "baseline" / "curve.json"
    if broken == "missing":
        curve.unlink()
    else:
        curve.write_text(curve.read_text(encoding="utf-8") + " ", encoding="utf-8")
    monkeypatch.setattr(engine, "_rawpy", lambda: pytest.fail("Broken baseline must stop before RAW decoding"))
    settings = default_settings()
    settings["style_id"] = "documentary"
    with pytest.raises(profiles.ProfileError):
        engine._baseline({"sha256": "a" * 64}, settings, DiskCache(tmp_path / "cache"), None)


def test_inspection_cache_rehashes_when_size_and_mtime_are_restored(tmp_path, monkeypatch):
    source = tmp_path / "cache-identity-only.arw"
    source.write_bytes(b"original fixture data")
    stat = source.stat()
    fingerprint = sha256_file(source)
    identity = (str(source.resolve()), stat.st_size, stat.st_mtime_ns, fingerprint)
    cached = dict(path=str(source), sha256=fingerprint, supported=True)
    monkeypatch.setattr(engine, "_INSPECTION_CACHE", engine.OrderedDict({identity: cached}))
    assert engine.inspect(source) == cached
    source.write_bytes(b"modified fixture data")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    assert source.stat().st_mtime_ns == stat.st_mtime_ns
    class ReinspectionRequired(Exception):
        pass
    def reinspection(_):
        raise ReinspectionRequired()
    monkeypatch.setattr(engine, "_metadata", reinspection)
    with pytest.raises(ReinspectionRequired):
        engine.inspect(source)


def test_source_changed_during_work_detected_with_restored_timestamp(tmp_path):
    source = tmp_path / "fingerprint.arw"
    source.write_bytes(b"original-data")
    stat = source.stat()
    source_info = dict(path=str(source), size_bytes=stat.st_size,
                       mtime_ns=stat.st_mtime_ns, sha256=sha256_file(source))
    source.write_bytes(b"modified-data")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match="현상 중 원본이 변경"):
        engine._assert_source_unchanged(source_info)
