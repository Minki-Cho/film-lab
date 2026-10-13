"""CLI selection tests preserve mixed photo support and headless RAW behavior."""
from pathlib import Path
import json

import pytest

from film_lab.__main__ import collect_sources, main


def test_folder_scan_accepts_case_insensitive_mixed_formats(tmp_path):
    names = ["camera.ArW", "a.JPG", "b.Jpeg", "c.pNg", "d.TIF", "e.Tiff", "f.WebP", "g.bMp"]
    for name in names:
        (tmp_path / name).write_bytes(b"content is inspected by the engine")
    (tmp_path / "recipe.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "animation.gif").write_bytes(b"GIF")
    nested = tmp_path / "nested.png"
    nested.mkdir()
    (nested / "not-recursive.jpg").write_bytes(b"nested")
    result = collect_sources([str(tmp_path), str(tmp_path / "a.JPG")])
    assert {Path(path).name for path in result} == set(names)
    assert len(result) == 8
    assert all(Path(path).is_absolute() for path in result)


def test_source_count_limits_apply_after_filtering_and_deduplication(tmp_path):
    with pytest.raises(ValueError, match="1~100"):
        collect_sources([str(tmp_path)])
    for index in range(100):
        (tmp_path / f"{index}.png").touch()
    assert len(collect_sources([str(tmp_path), str(tmp_path / "0.png")])) == 100
    (tmp_path / "overflow.arw").touch()
    with pytest.raises(ValueError, match="1~100"):
        collect_sources([str(tmp_path)])


def test_explicit_unsupported_source_is_reported(tmp_path, capsys):
    assert main(["export", str(tmp_path / "notes.txt"), "--output", str(tmp_path / "output")]) == 1
    assert "지원하지 않는 사진 형식" in capsys.readouterr().err


def test_export_passes_mixed_sources_and_existing_raw_options(tmp_path, monkeypatch, capsys):
    from film_lab import engine
    sources = tmp_path / "input"
    sources.mkdir()
    for name in ("RAW.ARW", "portrait.JPG", "landscape.PNG"):
        (sources / name).touch()
    (sources / "readme.txt").touch()
    called = {}
    def export(items, options):
        called.update(items=items, options=options)
        return [{"source": item["source"], "status": "completed"} for item in items]
    monkeypatch.setattr(engine, "export", export)
    result = main(["export", str(sources), str(sources / "RAW.ARW"), "--output", str(tmp_path / "output"),
                   "--style", "documentary", "--exposure", "0.4", "--strength", "0.7", "--format", "tiff", "--long-edge", "2048"])
    assert result == 0
    assert {Path(item["source"]).name for item in called["items"]} == {"RAW.ARW", "portrait.JPG", "landscape.PNG"}
    assert all(item["recipe"]["settings"]["style_id"] == "documentary" for item in called["items"])
    assert all(item["recipe"]["settings"]["exposure_ev"] == 0.4 for item in called["items"])
    assert called["options"]["format"] == "tiff"
    assert called["options"]["long_edge"] == 2048
    assert len(json.loads(capsys.readouterr().out)) == 3


@pytest.mark.parametrize("suffix,kind", [("ARW", "raw"), ("PNG", "raster")])
def test_inspect_and_render_cli_preserve_source_dispatch(tmp_path, monkeypatch, capsys, suffix, kind):
    from film_lab import engine
    source = str(tmp_path / ("photo." + suffix))
    inspected = []
    rendered = []
    def inspect(path):
        inspected.append(path)
        return {"path": path, "source_kind": kind, "supported": True}
    def render(path, settings, **kwargs):
        rendered.append((path, settings, kwargs))
        return {"path": str(tmp_path / "preview.png")}
    monkeypatch.setattr(engine, "inspect", inspect)
    monkeypatch.setattr(engine, "render", render)
    assert main(["inspect", source]) == 0
    assert json.loads(capsys.readouterr().out)["source_kind"] == kind
    assert inspected == [source]
    assert main(["render", source, "--style", "monochrome", "--full"]) == 0
    assert rendered[0][0] == source
    assert rendered[0][1]["style_id"] == "monochrome"
    assert rendered[0][2]["quality"] == "full"
    assert json.loads(capsys.readouterr().out)["path"].endswith("preview.png")


def test_export_partial_failure_keeps_nonzero_exit(tmp_path, monkeypatch, capsys):
    from film_lab import engine
    monkeypatch.setattr(engine, "export", lambda items, options: [{"status": "completed"}, {"status": "failed"}])
    assert main(["export", str(tmp_path / "good.jpg"), str(tmp_path / "bad.ARW"), "--output", str(tmp_path / "out")]) == 1
    assert len(json.loads(capsys.readouterr().out)) == 2
