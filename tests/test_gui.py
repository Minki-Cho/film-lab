"""GUI state tests run with real Qt and no RAW processing process."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from film_lab.gui import FilmLabWindow
from film_lab.formats import IMAGE_FILE_FILTER


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("FILM_LAB_DATA", str(tmp_path / "application"))
    app = QApplication.instance() or QApplication([])
    window = FilmLabWindow(start_worker=False)
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    yield window
    window.close()
    app.processEvents()


def test_edit_history_bw_control_and_frozen_export(window, tmp_path):
    photo = window.session.add({"id": "a", "path": str(tmp_path / "A.ARW"), "supported": True})
    item = window.add_list_item(photo)
    window.photos.setCurrentItem(item)
    window.change_setting("exposure_ev", 0.5)
    window.set_style("monochrome")
    assert photo.settings["exposure_ev"] == 0.5
    assert not window.adjustments["saturation"].isEnabled()
    window.undo()
    assert photo.settings["style_id"] == "baseline"
    assert window.adjustments["saturation"].isEnabled()
    window.redo()
    assert photo.settings["style_id"] == "monochrome"
    window.change_setting("strength", 0)
    assert photo.settings["strength"] == 0
    window.change_setting("warmth", 10)
    assert photo.settings["wb_mode"] == "relative"
    window.reset_all()
    assert photo.settings["wb_mode"] == "as_shot"


def test_stale_render_never_updates_current_photo(window, tmp_path):
    photo = window.session.add({"id": "a", "path": str(tmp_path / "A.ARW"), "supported": True})
    item = window.add_list_item(photo)
    window.photos.setCurrentItem(item)
    window.request_id = "old-request"
    window.requests["old-request"] = {"photo_id": photo.id}
    window.change_setting("contrast", 25)
    assert window.request_id != "old-request"
    window.handle_event({"kind": "render", "token": "old-request", "event": "done", "result": {"path": "should-not-be-opened"}})
    assert photo.preview == ""
    assert window.preview.item.after.isNull()


def test_empty_workspace_layout_and_korean_fonts(window):
    from PySide6.QtGui import QFontDatabase
    assert len(window.style_buttons) == 6
    assert not window.one_export.isEnabled()
    assert not window.all_export.isEnabled()
    assert window.empty.isVisible()
    assert QFontDatabase.families()
    for tile in window.style_buttons.values():
        assert tile.width() == 132


def test_full_resolution_preview_uses_visible_tiles(window, tmp_path):
    import threading
    from PIL import Image, ImageDraw
    from film_lab.worker import _render_tiles
    after = tmp_path / ("a" * 64 + ".png")
    before = tmp_path / ("b" * 64 + ".png")
    after_image = Image.new("RGB", (1300, 700))
    before_image = Image.new("RGB", (1300, 700))
    after_draw, before_draw = ImageDraw.Draw(after_image), ImageDraw.Draw(before_image)
    for x in range(1300):
        before_draw.line((x, 0, x, 699), fill=(x % 256, 0, 0))
        after_draw.line((x, 0, x, 699), fill=(0, x % 256, 0))
    after_image.save(after)
    before_image.save(before)
    tiles = _render_tiles({"path": str(after), "baseline_path": str(before), "width": 1300, "height": 700}, tmp_path, threading.Event())
    assert len(tiles["after"]) == 6
    window.empty.hide()
    window.preview.show()
    window.preview.fit = False
    window.preview.set_images(str(after), str(before), tiles)
    window.preview.set_mode("side")
    QApplication.processEvents()
    assert window.preview.item.after.isNull()  # No full-resolution QPixmap in GUI.
    assert window.preview.item.before.isNull()
    assert window.preview.item.boundingRect().width() == 1300 + window.preview.item.pane_width
    assert window.preview.transform().m11() == 1
    assert 0 < len(window.preview.item.tile_cache) <= 64
    # Panning must keep identical RAW coordinates in both side-by-side panes.
    for scroll_x in (0, 230, 500):
        window.preview.horizontalScrollBar().setValue(scroll_x)
        QApplication.processEvents()
        screenshot = window.preview.viewport().grab().toImage()
        half = window.preview.item.pane_width
        for offset in (20, 70, half - 20):
            left = screenshot.pixelColor(offset, 100)
            right = screenshot.pixelColor(offset + half, 100)
            assert left.red() == right.green()
            assert left.green() == right.red() == 0
    with Image.open(tiles["after"][-1]) as edge:
        assert edge.size == (276, 188)


def test_self_test_requires_isolated_data_directory(monkeypatch):
    from film_lab.gui import self_test
    monkeypatch.delenv("FILM_LAB_DATA", raising=False)
    assert self_test() == 2


class RecordingWorker:
    def __init__(self):
        from types import SimpleNamespace
        self.tasks = []
        self.process = SimpleNamespace(is_alive=lambda: False)

    def submit(self, kind, payload, token=None):
        token = token or f"task-{len(self.tasks)}"
        self.tasks.append({"kind": kind, "payload": payload, "token": token})
        return token

    def poll(self):
        return []


def test_mixed_image_selection_filters_duplicates_and_unrelated_files(window, tmp_path):
    from pathlib import Path
    window.worker = RecordingWorker()
    names = ["sony.ArW", "portrait.JPG", "portrait.Jpeg", "image.pNg", "scan.TIF", "scan.Tiff", "web.WebP", "bitmap.bMp"]
    paths = [str(tmp_path / name) for name in names]
    unsupported = tmp_path / "not-an-image.txt"
    fake_directory = tmp_path / "folder.png"
    fake_directory.mkdir()
    window.add_paths([*paths, paths[1], str(unsupported), str(fake_directory)])
    assert [Path(photo.path).name for photo in window.session.photos] == names
    assert window.session.photos[0].source["source_kind"] == "raw"
    assert all(photo.source["source_kind"] == "raster" for photo in window.session.photos[1:])
    assert len([task for task in window.worker.tasks if task["kind"] == "inspect"]) == 8
    assert "3개" in window.stage.text()
    # Once inspected, mixed source kinds remain selectable and exportable together.
    for task in list(window.worker.tasks):
        source = {"path": task["payload"]["path"], "supported": True,
                  "source_kind": "raw" if task["payload"]["path"].lower().endswith(".arw") else "raster"}
        window.handle_event({"kind": "inspect", "token": task["token"], "event": "done", "result": source})
    window.photos.selectAll()
    assert len(window.selected_photos()) == 8
    assert window.selected_export.isEnabled()
    assert window.all_export.isEnabled()


def test_mixed_drag_drop_accepts_local_supported_images(window, tmp_path):
    from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    window.worker = RecordingWorker()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "photo.JpG")),
                  QUrl.fromLocalFile(str(tmp_path / "raw.ArW")),
                  QUrl.fromLocalFile(str(tmp_path / "notes.txt")),
                  QUrl("https://example.com/remote.png")])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(drop)
    assert drop.isAccepted()
    assert len(window.session.photos) == 2
    assert {window.photo_kind(photo) for photo in window.session.photos} == {"raw", "raster"}


@pytest.mark.parametrize("suffix,kind,wb_text,reference", [
    ("ARW", "raw", "촬영 화이트밸런스", "RAW 기본 현상"),
    ("JPEG", "raster", "기존 이미지 색상", "원본 이미지"),
    ("TIFF", "raster", "기존 이미지 색상", "원본 이미지"),
])
def test_source_specific_correction_and_comparison_notes(window, tmp_path, suffix, kind, wb_text, reference):
    photo = window.session.add({"id": suffix, "path": str(tmp_path / ("photo." + suffix)),
                                "supported": True, "source_kind": kind, "camera_model": "ILCE-6400"})
    item = window.add_list_item(photo)
    window.photos.setCurrentItem(item)
    assert wb_text in window.wb_note.text()
    assert reference in window.image_info.text()
    assert window.style_buttons["baseline"].text() == ("기본 현상" if kind == "raw" else "기본 이미지")
    window.worker = RecordingWorker()
    window.request_render()
    assert ("RAW 현상" if kind == "raw" else "이미지 보정") in window.stage.text()
    if kind == "raster":
        assert "RAW" not in window.adjustments["exposure_ev"].toolTip()


def test_picker_and_reconnect_use_all_supported_formats(window, tmp_path, monkeypatch):
    from film_lab.gui import QFileDialog
    from pathlib import Path
    window.worker = RecordingWorker()
    calls = []
    first = str(tmp_path / "photo.png")
    second = str(tmp_path / "moved-photo.png")
    def pick(*args):
        calls.append(args[-1])
        return [first], ""
    def reconnect(*args):
        calls.append(args[-1])
        return second, ""
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", pick)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", reconnect)
    window.pick_photos()
    photo = window.current
    photo.source.update(sha256="original-hash", size_bytes=321)
    window.reconnect()
    assert calls == [IMAGE_FILE_FILTER, IMAGE_FILE_FILTER]
    task = window.worker.tasks[-1]
    assert task["kind"] == "inspect"
    assert task["payload"]["path"] == second
    assert task["payload"]["expected"]["sha256"] == "original-hash"
    assert Path(photo.path).name == "photo.png"  # Reconnect waits for identity verification.
