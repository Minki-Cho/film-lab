"""GUI state tests run with real Qt and no RAW processing process."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from film_lab.gui import FilmLabWindow


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
