"""Korean desktop editing workspace. Rendering lives in a spawned process."""
from __future__ import annotations

from copy import deepcopy
from collections import OrderedDict
from pathlib import Path
import json
import os
import sys
import uuid

from PySide6.QtCore import Qt, QSize, QRectF, QTimer, QStandardPaths, QSettings
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QFrame, QSplitter, QListWidget, QListWidgetItem,
    QAbstractItemView, QScrollArea, QSlider, QComboBox, QSpinBox, QDoubleSpinBox,
    QFileDialog, QMessageBox, QDialog, QDialogButtonBox, QCheckBox, QFormLayout,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QProgressBar, QButtonGroup,
)

from .models import default_settings, validate_export_options
from .profiles import list_profiles
from .session import Session
from .worker import Processor


STYLE_NAMES = {
    "baseline": "기본 현상", "documentary": "다큐멘터리 스타일",
    "negative": "네거티브 스타일", "cinema": "시네마 스타일",
    "vivid": "비비드 스타일", "monochrome": "흑백 스타일",
}
STYLE_COLORS = ["#667377", "#a49b82", "#87916d", "#7b8b8b", "#a77c65", "#a7a7a7"]
STATUS_LABELS = {"completed": "완료", "partial": "부분 완료", "failed": "실패", "cancelled": "취소"}

STYLESHEET = """
QMainWindow, QDialog { background: #111315; color: #e6e6e4; }
QWidget { font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 12px; color: #e4e5e4; }
QWidget:disabled { color: #828e93; }
QWidget#EditContent { background: #191c1e; }
QFrame#Header { background: #171a1c; border-bottom: 1px solid #303436; }
QFrame#SidePanel { background: #191c1e; }
QFrame#Footer { background: #1b1e20; border-top: 1px solid #34383a; }
QFrame#Toolbar { background: #141719; border-bottom: 1px solid #2b3032; }
QLabel#Muted { color: #939b9e; }
QLabel#Accent { color: #deb88e; }
QLabel#Section { color: #b8c0c3; font-size: 11px; font-weight: 600; }
QPushButton, QToolButton { background: #24292c; border: 1px solid #3a4246; border-radius: 5px; padding: 7px 11px; }
QPushButton:hover, QToolButton:hover { background: #30383d; border-color: #647177; }
QPushButton:pressed, QToolButton:pressed { background: #3b4144; }
QPushButton:disabled, QToolButton:disabled { color: #687174; background: #1c2123; border-color: #2c3235; }
QPushButton#Primary { background: #d4aa7d; color: #191b1d; border: 1px solid #d4aa7d; font-weight: 600; }
QPushButton#Primary:hover { background: #e2bb91; }
QPushButton#Primary:disabled { background: #534a3e; color: #94897b; border-color: #534a3e; }
QPushButton#Flat { border: none; background: transparent; color: #aeb7bb; padding: 5px; }
QPushButton#Flat:hover { color: #edc194; background: #292e31; }
QToolButton#Style { padding: 6px; background: #222729; border: 1px solid #373e42; border-radius: 5px; font-size: 10px; }
QToolButton#Style:checked { background: #34302a; border: 2px solid #d4aa7d; color: #efd7ba; }
QToolButton#Style:disabled { color: #687174; }
QListWidget { background: #191c1e; border: none; outline: none; padding: 5px; }
QListWidget::item { background: #202527; border: 1px solid #2e3639; border-radius: 5px; padding: 8px; margin-bottom: 6px; }
QListWidget::item:selected { background: #34312b; border: 1px solid #d4aa7d; }
QListWidget::item:hover { background: #2b3134; }
QGraphicsView { background: #101214; border: none; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: #1b2023; width: 7px; margin: 0; }
QScrollBar:horizontal { background: #1b2023; height: 7px; margin: 0; }
QScrollBar::handle { background: #4d575d; border-radius: 3px; min-height: 28px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSlider::groove:horizontal { height: 3px; background: #414b50; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #a58b6d; }
QSlider::handle:horizontal { width: 11px; height: 11px; margin: -4px 0; background: #d8b58c; border-radius: 5px; }
QSlider::handle:horizontal:disabled { background: #60676a; }
QComboBox, QSpinBox, QDoubleSpinBox { background: #242a2d; color: #dce0e0; border: 1px solid #424b50; border-radius: 4px; padding: 5px 7px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #242a2d; selection-background-color: #51473b; }
QProgressBar { border: none; background: #353e42; border-radius: 2px; max-height: 4px; }
QProgressBar::chunk { background: #d4aa7d; border-radius: 2px; }
QMenuBar { background: #171a1c; color: #aab3b7; font-size: 11px; }
QMenuBar::item:selected, QMenu::item:selected { background: #3a403f; }
QMenu { background: #23292c; border: 1px solid #434e53; padding: 5px; }
QMenu::item { padding: 7px 25px; }
QCheckBox { spacing: 8px; padding: 5px; }
QCheckBox::indicator { width: 15px; height: 15px; }
QCheckBox::indicator:unchecked { background: #252c30; border: 1px solid #596368; border-radius: 3px; }
QCheckBox::indicator:checked { background: #d4aa7d; border: 1px solid #d4aa7d; border-radius: 3px; }
QToolTip { color: #f3f2ed; background: #343c41; border: 1px solid #67757c; padding: 6px; }
"""


def label(text, role=None):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    return widget


def button(text, callback, primary=False, flat=False):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    if primary:
        widget.setObjectName("Primary")
    if flat:
        widget.setObjectName("Flat")
    return widget


class EmptyIllustration(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(190, 116)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(7, 7)
        painter.setPen(QPen(QColor("#3a4447"), 1))
        painter.setBrush(QColor("#181e20"))
        painter.drawRoundedRect(QRectF(17, 10, 137, 86), 5, 5)
        painter.setPen(QPen(QColor("#a98c6b"), 1.5))
        painter.setBrush(QColor("#20282a"))
        painter.drawRoundedRect(QRectF(30, 0, 137, 86), 5, 5)
        painter.setPen(QPen(QColor("#526367"), 1.2))
        painter.drawLine(42, 72, 80, 32)
        painter.drawLine(80, 32, 113, 64)
        painter.drawLine(105, 56, 125, 36)
        painter.drawLine(125, 36, 155, 72)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d3b28a"))
        painter.drawEllipse(QRectF(124, 17, 11, 11))
        painter.setPen(QPen(QColor("#d3b28a"), 1.5))
        painter.drawLine(24, 74, 24, 92)
        painter.drawLine(15, 83, 33, 83)


class CompareItem(QGraphicsItem):
    def __init__(self):
        super().__init__()
        self.after, self.before = QPixmap(), QPixmap()
        self.mode = "split"
        self.ratio = 0.5
        self.tiles = None
        self.tile_cache = OrderedDict()
        self.width, self.height = 0, 0
        self.pane_width = 1
        self.detail = False
        self.viewport_rect = QRectF()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)

    def boundingRect(self):
        if self.tiles and self.detail and self.mode == "side":
            return QRectF(0, 0, self.width + self.pane_width, self.height)
        return QRectF(0, 0, self.width * (2 if self.mode == "side" else 1), self.height)

    def set_images(self, after, before):
        self.prepareGeometryChange()
        self.after, self.before = after, before
        self.width, self.height = after.width(), after.height()
        self.tiles = None
        self.tile_cache.clear()
        self.update()

    def set_tiles(self, tiles):
        self.prepareGeometryChange()
        self.tiles = tiles
        self.after, self.before = QPixmap(), QPixmap()
        self.width, self.height = tiles["width"], tiles["height"]
        self.tile_cache.clear()
        self.update()

    def set_mode(self, mode):
        self.prepareGeometryChange()
        self.mode = mode
        self.update()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.tiles:
            exposed = option.exposedRect
            if self.mode == "side" and self.detail:
                # Both panes show the same sensor coordinates at 1:1. Moving
                # the common scrollbars therefore pans before and after together.
                visible = self.viewport_rect
                left = QRectF(visible.left(), visible.top(), self.pane_width, visible.height())
                right = left.translated(self.pane_width, 0)
                painter.save()
                painter.setClipRect(left)
                self._paint_tiles(painter, "before", exposed.intersected(left))
                painter.restore()
                painter.save()
                painter.setClipRect(right)
                self._paint_tiles(painter, "after", exposed.intersected(right), self.pane_width)
                painter.restore()
                pen = QPen(QColor("#69767c"), 0)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLine(int(right.left()), int(visible.top()), int(right.left()), int(visible.bottom()))
            elif self.mode == "side":
                self._paint_tiles(painter, "before", exposed)
                self._paint_tiles(painter, "after", exposed, self.width)
            else:
                self._paint_tiles(painter, "after", exposed)
                if self.mode == "split":
                    edge = self.width * self.ratio
                    painter.save()
                    painter.setClipRect(QRectF(0, 0, edge, self.height))
                    self._paint_tiles(painter, "before", exposed.intersected(QRectF(0, 0, edge, self.height)))
                    painter.restore()
                    pen = QPen(QColor("#f7ddbb"), 0)
                    pen.setCosmetic(True)
                    painter.setPen(pen)
                    painter.drawLine(int(edge), 0, int(edge), self.height)
            return
        if self.mode == "side":
            painter.drawPixmap(0, 0, self.before)
            painter.drawPixmap(self.after.width(), 0, self.after)
        else:
            painter.drawPixmap(0, 0, self.after)
            if self.mode == "split":
                edge = self.after.width() * self.ratio
                painter.save()
                painter.setClipRect(QRectF(0, 0, edge, self.after.height()))
                painter.drawPixmap(0, 0, self.before)
                painter.restore()
                pen = QPen(QColor("#f7ddbb"), 0)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLine(int(edge), 0, int(edge), self.after.height())

    def _paint_tiles(self, painter, which, exposed, offset=0):
        visible = exposed.translated(-offset, 0).intersected(QRectF(0, 0, self.width, self.height))
        if visible.isEmpty():
            return
        size = self.tiles["size"]
        columns = (self.width + size - 1) // size
        rows = (self.height + size - 1) // size
        for row in range(max(0, int(visible.top()) // size), min(rows, int(visible.bottom()) // size + 1)):
            for column in range(max(0, int(visible.left()) // size), min(columns, int(visible.right()) // size + 1)):
                path = self.tiles[which][row * columns + column]
                pixmap = self.tile_cache.get(path)
                if pixmap is None:
                    pixmap = QPixmap(path)
                    self.tile_cache[path] = pixmap
                    # At most 64 RGBA 512px tiles (~64 MiB), regardless of RAW size.
                    while len(self.tile_cache) > 64:
                        self.tile_cache.popitem(last=False)
                self.tile_cache.move_to_end(path)
                painter.drawPixmap(offset + column * size, row * size, pixmap)


class Preview(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = CompareItem()
        self.scene().addItem(self.item)
        self.fit = True
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def set_images(self, after, before, tiles=None):
        if tiles:
            self.item.set_tiles(tiles)
        else:
            self.item.set_images(QPixmap(after), QPixmap(before))
        self.scene().setSceneRect(self.item.boundingRect())
        self.refresh_scale()

    def set_mode(self, mode):
        self.item.set_mode(mode)
        self.scene().setSceneRect(self.item.boundingRect())
        self.refresh_scale()

    def refresh_scale(self):
        self.resetTransform()
        self.item.prepareGeometryChange()
        self.item.detail = not self.fit
        self.item.pane_width = max(1, self.viewport().width() // 2)
        self.scene().setSceneRect(self.item.boundingRect())
        if self.fit and self.item.width:
            self.fitInView(self.item.boundingRect().adjusted(-10, -10, 10, 10), Qt.AspectRatioMode.KeepAspectRatio)
        self.viewport().update()

    def paintEvent(self, event):
        self.item.viewport_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        super().paintEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_scale()

    def mousePressEvent(self, event):
        if self.item.mode == "split" and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.item.width:
                self.item.ratio = max(0, min(1, point.x() / self.item.width))
                self.item.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.item.mode == "split" and event.buttons() & Qt.MouseButton.LeftButton and self.fit:
            point = self.mapToScene(event.position().toPoint())
            if self.item.width:
                self.item.ratio = max(0, min(1, point.x() / self.item.width))
                self.item.update()
                return
        super().mouseMoveEvent(event)


class Adjustment(QWidget):
    def __init__(self, title, minimum, maximum, default, changed, key, scale=1, suffix=""):
        super().__init__()
        self.key, self.scale, self.default = key, scale, default
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        row = QHBoxLayout()
        row.addWidget(label(title))
        row.addStretch()
        self.value_label = label("0", "Accent")
        self.value_label.setMinimumWidth(39)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(self.value_label)
        reset = button("↺", self.reset, flat=True)
        reset.setToolTip(f"{title} 초기화")
        reset.setFixedSize(23, 22)
        row.addWidget(reset)
        outer.addLayout(row)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setAccessibleName(title)
        self.slider.setFixedHeight(17)
        self.suffix = suffix
        self.slider.valueChanged.connect(self.show_value)
        self.slider.valueChanged.connect(lambda _: changed(key, self.slider.value() / scale))
        outer.addWidget(self.slider)
        self.set_value(default)

    def show_value(self, value):
        number = value / self.scale
        text = f"{number:+.1f}" if self.scale == 10 else f"{int(number)}"
        self.value_label.setText(text + self.suffix)

    def set_value(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(round(value * self.scale))
        self.show_value(self.slider.value())
        self.slider.blockSignals(False)

    def reset(self):
        self.slider.setValue(round(self.default * self.scale))


class ExportDialog(QDialog):
    def __init__(self, count, options, parent):
        super().__init__(parent)
        self.setWindowTitle("사진 내보내기")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        title = label(f"{count}장의 사진 내보내기")
        title.setStyleSheet("font-size: 19px; font-weight: 600; padding: 8px 0;")
        layout.addWidget(title)
        note = label("등록 시점의 보정값으로 저장합니다. 원본 ARW는 그대로 보존됩니다.", "Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.format = QComboBox()
        self.format.addItem("JPEG · 8비트 sRGB", "jpeg")
        self.format.addItem("TIFF · 16비트 sRGB", "tiff")
        self.format.setCurrentIndex(max(0, self.format.findData(options.get("format", "jpeg"))))
        form.addRow("파일 형식", self.format)
        self.quality = QSpinBox()
        self.quality.setRange(80, 100)
        self.quality.setValue(options.get("quality", 95))
        form.addRow("JPEG 품질", self.quality)
        self.size = QComboBox()
        self.size.addItem("원본 크기", None)
        self.size.addItem("긴 변 3840 px", 3840)
        self.size.addItem("긴 변 2048 px", 2048)
        self.size.setCurrentIndex(max(0, self.size.findData(options.get("long_edge"))))
        form.addRow("출력 크기", self.size)
        self.directory = str(options.get("output_dir", ""))
        self.directory_button = button(self.directory or "저장 폴더 선택…", self.pick_directory)
        self.directory_button.setMaximumWidth(340)
        self.directory_button.setToolTip(self.directory)
        form.addRow("저장 위치", self.directory_button)
        layout.addLayout(form)
        note = label("동일 이름은 순번을 붙여 저장합니다. 각 이미지에 레시피 JSON이 함께 저장됩니다. GPS와 기기 일련번호는 제외합니다.", "Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        actions.button(QDialogButtonBox.StandardButton.Ok).setText("내보내기 시작")
        actions.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        actions.accepted.connect(self.validate)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)
        self.format.currentIndexChanged.connect(lambda: self.quality.setEnabled(self.format.currentData() == "jpeg"))
        self.quality.setEnabled(self.format.currentData() == "jpeg")

    def pick_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "결과 저장 폴더", self.directory)
        if directory:
            self.directory = directory
            self.directory_button.setText(directory)
            self.directory_button.setToolTip(directory)

    def validate(self):
        if not self.directory:
            self.pick_directory()
        if not self.directory:
            return
        try:
            validate_export_options(self.options())
        except ValueError as exc:
            QMessageBox.warning(self, "저장 설정 확인", str(exc))
            return
        self.accept()

    def options(self):
        return {"format": self.format.currentData(), "quality": self.quality.value(),
                "long_edge": self.size.currentData(), "color_space": "sRGB", "output_dir": self.directory}


class FilmLabWindow(QMainWindow):
    def __init__(self, start_worker=True, project_path=None):
        super().__init__()
        # Qt's Windows offscreen platform has an empty font database. Loading the
        # installed OS fonts also keeps headless visual checks representative.
        if not QFontDatabase.families():
            font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for filename in ("segoeui.ttf", "malgun.ttf", "seguisym.ttf"):
                if (font_dir / filename).is_file():
                    QFontDatabase.addApplicationFont(str(font_dir / filename))
        self.setWindowTitle("FILM LAB · α6400")
        self.resize(1480, 960)
        self.setMinimumSize(1180, 760)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLESHEET)
        data_dir = Path(os.environ.get("FILM_LAB_DATA", str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "FilmLab")))
        self.data_dir = data_dir
        if "FILM_LAB_DATA" in os.environ:
            data_dir.mkdir(parents=True, exist_ok=True)
            self.preferences = QSettings(str(data_dir / "preferences.ini"), QSettings.Format.IniFormat)
        else:
            self.preferences = QSettings("FilmLab", "A6400")
        self.session = Session(project_path or data_dir / "Projects" / "자동저장.filmproject.json")
        self.current_id = None
        self.request_id = ""
        self.requests = {}
        self.loading_controls = False
        self.exit_pending = False
        self.worker = Processor(data_dir / "Cache") if start_worker else None
        self.adjustments, self.style_buttons, self.style_placeholders = {}, {}, {}
        self._build()
        self._menus()
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(150)
        self.debounce.timeout.connect(self.request_render)
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.setInterval(2000)
        self.autosave.timeout.connect(self.save_session)
        self.poller = QTimer(self)
        self.poller.setInterval(60)
        self.poller.timeout.connect(self.poll)
        self.poller.start()
        self._set_editor_enabled(False)
        self.update_counts()
        path = project_path or self.preferences.value("last_project", str(self.session.path))
        if path and Path(path).is_file():
            self.load_project(path)

    def _build(self):
        base = QWidget()
        outer = QVBoxLayout(base)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(base)
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(75)
        row = QHBoxLayout(header)
        row.setContentsMargins(25, 12, 24, 12)
        brand = label("FILM LAB")
        brand.setStyleSheet("font-size: 25px; font-weight: 600; letter-spacing: 4px;")
        row.addWidget(brand)
        mark = label("α6400")
        mark.setStyleSheet("color: #d5b085; border: 1px solid #5a4c3b; border-radius: 3px; padding: 3px 7px; margin-left: 8px;")
        row.addWidget(mark)
        row.addSpacing(15)
        row.addWidget(label("RAW에서 시작하는 나만의 필름 톤", "Muted"))
        row.addStretch()
        local = label("●  로컬 처리", "Muted")
        local.setStyleSheet("color: #a5b4a6;")
        row.addWidget(local)
        row.addSpacing(15)
        row.addWidget(button("프로젝트 저장", self.save_project_as))
        row.addWidget(button("＋ 사진 추가", self.pick_photos, primary=True))
        outer.addWidget(header)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)
        # Film strip.
        left = QFrame()
        left.setObjectName("SidePanel")
        left.setMinimumWidth(210)
        left.setMaximumWidth(315)
        col = QVBoxLayout(left)
        col.setContentsMargins(13, 20, 13, 12)
        col.setSpacing(12)
        title_row = QHBoxLayout()
        title_row.addWidget(label("라이브러리", "Section"))
        title_row.addStretch()
        self.library_count = label("0 / 100", "Muted")
        title_row.addWidget(self.library_count)
        col.addLayout(title_row)
        self.photos = QListWidget()
        self.photos.setIconSize(QSize(73, 55))
        self.photos.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.photos.currentItemChanged.connect(self.select_photo)
        self.photos.itemSelectionChanged.connect(self.update_counts)
        col.addWidget(self.photos, 1)
        self.library_empty = label("아직 사진이 없습니다\n\nARW 파일을 추가하면\n이곳에서 모아 볼 수 있어요.", "Muted")
        self.library_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.library_empty.setStyleSheet("color: #7e898e;")
        col.addWidget(self.library_empty)
        self.list_hint = label("Ctrl / Shift로 여러 사진 선택", "Muted")
        self.list_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_hint.setStyleSheet("font-size: 10px; color: #828e93;")
        col.addWidget(self.list_hint)
        list_actions = QHBoxLayout()
        list_actions.addWidget(button("재연결", self.reconnect))
        list_actions.addWidget(button("목록에서 제거", self.remove_selected))
        col.addLayout(list_actions)
        splitter.addWidget(left)
        # Main preview.
        center = QWidget()
        center_col = QVBoxLayout(center)
        center_col.setContentsMargins(0, 0, 0, 0)
        center_col.setSpacing(0)
        toolbar = QFrame()
        toolbar.setObjectName("Toolbar")
        toolbar.setFixedHeight(56)
        tools_row = QHBoxLayout(toolbar)
        tools_row.setContentsMargins(18, 7, 18, 7)
        self.preview_title = label("미리보기", "Muted")
        tools_row.addWidget(self.preview_title)
        tools_row.addStretch()
        self.zoom = QComboBox()
        self.zoom.addItem("화면 맞춤", "preview")
        self.zoom.addItem("100% · 원본 해상도", "full")
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        tools_row.addWidget(self.zoom)
        self.compare = QComboBox()
        self.compare.addItem("분할 비교", "split")
        self.compare.addItem("좌우 비교", "side")
        self.compare.addItem("결과만 보기", "after")
        self.compare.currentIndexChanged.connect(lambda: self.preview.set_mode(self.compare.currentData()))
        tools_row.addWidget(self.compare)
        center_col.addWidget(toolbar)
        self.preview = Preview()
        self.preview.hide()
        center_col.addWidget(self.preview, 1)
        self.empty = QWidget()
        empty_col = QVBoxLayout(self.empty)
        empty_col.setSpacing(15)
        empty_col.addStretch()
        empty_col.addWidget(EmptyIllustration(), 0, Qt.AlignmentFlag.AlignHCenter)
        title = label("사진에 새로운 분위기를 더하세요")
        title.setStyleSheet("font-size: 24px; font-weight: 500; color: #e5e7e5;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_col.addWidget(title)
        subtitle = label("Sony α6400의 ARW 사진을 이곳에 놓아 주세요.\n기본 현상부터 다섯 가지 필름 스타일을 한 화면에서.", "Muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #8d999f; font-size: 13px;")
        empty_col.addWidget(subtitle)
        empty_col.addSpacing(8)
        choose = button("＋ ARW 사진 선택", self.pick_photos, primary=True)
        choose.setFixedSize(171, 42)
        empty_col.addWidget(choose, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_col.addSpacing(10)
        hint = label(".ARW   ·   최대 100장   ·   원본 보존", "Muted")
        hint.setStyleSheet("color: #68767e; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_col.addWidget(hint)
        empty_col.addStretch()
        workflow = label("01  사진 추가     /     02  스타일과 보정     /     03  내보내기", "Muted")
        workflow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        workflow.setStyleSheet("color: #758186; font-size: 11px; padding-bottom: 44px;")
        empty_col.addWidget(workflow)
        center_col.addWidget(self.empty, 1)
        self.image_info = label("비교 기준: 같은 노출·화이트밸런스를 적용한 RAW 기본 현상", "Muted")
        self.image_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_info.setWordWrap(True)
        self.image_info.setStyleSheet("font-size: 10px; color: #879197; padding: 12px; background: #15191b;")
        center_col.addWidget(self.image_info)
        splitter.addWidget(center)
        # Adjustments.
        right = QFrame()
        right.setObjectName("SidePanel")
        right.setMinimumWidth(322)
        right.setMaximumWidth(344)
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        editor = QWidget()
        editor.setObjectName("EditContent")
        self.editor = editor
        edit_col = QVBoxLayout(editor)
        edit_col.setContentsMargins(19, 18, 19, 15)
        edit_col.setSpacing(11)
        row = QHBoxLayout()
        row.addWidget(label("필름 스타일", "Section"))
        row.addStretch()
        badge = label("초기 스타일", "Accent")
        badge.setStyleSheet("font-size: 10px; color: #c2a17d; background: #302c26; padding: 3px 6px; border-radius: 3px;")
        row.addWidget(badge)
        edit_col.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(7)
        self.styles_group = QButtonGroup(self)
        self.styles_group.setExclusive(True)
        for i, profile in enumerate(list_profiles()):
            key = profile["id"]
            tile = QToolButton()
            tile.setObjectName("Style")
            tile.setText(STYLE_NAMES.get(key, profile.get("name", key)))
            tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            tile.setIconSize(QSize(123, 57))
            tile.setFixedWidth(132)
            tile.setMinimumHeight(91)
            tile.setSizePolicy(tile.sizePolicy().Policy.Expanding, tile.sizePolicy().Policy.Fixed)
            tile.setCheckable(True)
            tile.setEnabled(profile.get("available", True))
            tile.setToolTip(profile.get("error") or "현재 사진을 불러오면 실제 스타일 미리보기가 표시됩니다.")
            pixmap = QPixmap(123, 57)
            pixmap.fill(QColor(STYLE_COLORS[i % len(STYLE_COLORS)]))
            painter = QPainter(pixmap)
            painter.fillRect(0, 30, 123, 27, QColor("#273033"))
            painter.setPen(QColor("#bbc0ba"))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "사진 추가 후 미리보기")
            painter.end()
            tile.setIcon(QIcon(pixmap))
            self.style_placeholders[key] = QIcon(pixmap)
            tile.clicked.connect(lambda checked, style=key: self.set_style(style))
            self.style_buttons[key] = tile
            self.styles_group.addButton(tile)
            grid.addWidget(tile, i // 2, i % 2)
        edit_col.addLayout(grid)
        self.add_adjustment(edit_col, "스타일 강도", "strength", 0, 100, 100, suffix="%")
        edit_col.addSpacing(3)
        edit_col.addWidget(label("빛과 색", "Section"))
        self.add_adjustment(edit_col, "노출", "exposure_ev", -20, 30, 0, scale=10, suffix=" EV")
        self.add_adjustment(edit_col, "따뜻함", "warmth", -100, 100, 0)
        self.add_adjustment(edit_col, "틴트", "tint", -100, 100, 0)
        wb_note = label("촬영 화이트밸런스 기준 상대 보정", "Muted")
        wb_note.setStyleSheet("font-size: 10px; color: #7f8d92;")
        edit_col.addWidget(wb_note)
        edit_col.addSpacing(3)
        edit_col.addWidget(label("명암과 질감", "Section"))
        self.add_adjustment(edit_col, "대비", "contrast", -100, 100, 0)
        self.add_adjustment(edit_col, "채도", "saturation", -100, 100, 0)
        self.add_adjustment(edit_col, "하이라이트", "highlights", -100, 100, 0)
        self.add_adjustment(edit_col, "그림자", "shadows", -100, 100, 0)
        self.add_adjustment(edit_col, "입자감", "grain.amount", 0, 100, 0)
        grain_row = QHBoxLayout()
        grain_row.addWidget(label("입자 크기"))
        grain_row.addStretch()
        self.grain_size = QComboBox()
        self.grain_size.addItem("작게", "small")
        self.grain_size.addItem("크게", "large")
        self.grain_size.currentIndexChanged.connect(lambda: self.change_setting("grain.size", self.grain_size.currentData()))
        grain_row.addWidget(self.grain_size)
        edit_col.addLayout(grain_row)
        edit_col.addStretch()
        scroll.setWidget(editor)
        right_col.addWidget(scroll, 1)
        edit_actions = QWidget()
        action_grid = QGridLayout(edit_actions)
        action_grid.setContentsMargins(17, 12, 17, 13)
        self.undo_button = button("↶ 실행 취소", self.undo)
        self.redo_button = button("↷ 다시 실행", self.redo)
        action_grid.addWidget(self.undo_button, 0, 0)
        action_grid.addWidget(self.redo_button, 0, 1)
        action_grid.addWidget(button("설정 복사…", self.copy_settings), 1, 0)
        action_grid.addWidget(button("전체 초기화", self.reset_all), 1, 1)
        right_col.addWidget(edit_actions)
        splitter.addWidget(right)
        splitter.setSizes([235, 925, 320])
        # Durable export queue.
        footer = QFrame()
        footer.setObjectName("Footer")
        footer.setMinimumHeight(92)
        foot_col = QVBoxLayout(footer)
        foot_col.setContentsMargins(22, 11, 22, 11)
        foot_col.setSpacing(7)
        row = QHBoxLayout()
        self.selection_info = label("선택 0장", "Accent")
        row.addWidget(self.selection_info)
        row.addSpacing(10)
        self.stage = label("사진을 추가하여 시작하세요", "Muted")
        row.addWidget(self.stage, 1)
        self.cancel_button = button("작업 취소", self.cancel_exports)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        self.one_export = button("한 장 저장", lambda: self.export_photos("one"))
        self.selected_export = button("선택 사진 저장", lambda: self.export_photos("selected"))
        self.all_export = button("전체 저장", lambda: self.export_photos("all"), primary=True)
        row.addWidget(self.one_export)
        row.addWidget(self.selected_export)
        row.addWidget(self.all_export)
        foot_col.addLayout(row)
        bottom_row = QHBoxLayout()
        self.location = label("저장 위치: 내보낼 때 선택", "Muted")
        self.location.setStyleSheet("color: #849297; font-size: 10px;")
        bottom_row.addWidget(self.location, 1)
        self.saved_label = label("변경 사항 자동 저장 · 2초", "Muted")
        self.saved_label.setStyleSheet("color: #7d8b90; font-size: 10px;")
        bottom_row.addWidget(self.saved_label)
        foot_col.addLayout(bottom_row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        foot_col.addWidget(self.progress)
        outer.addWidget(footer)

    def add_adjustment(self, layout, title, key, minimum, maximum, default, scale=1, suffix=""):
        control = Adjustment(title, minimum, maximum, default, self.change_setting, key, scale, suffix)
        self.adjustments[key] = control
        layout.addWidget(control)

    def _menus(self):
        file_menu = self.menuBar().addMenu("프로젝트")
        actions = [("사진 추가…", "Ctrl+O", self.pick_photos), ("프로젝트 열기…", "Ctrl+Shift+O", self.open_project),
                   ("프로젝트 저장", "Ctrl+S", self.save_session), ("다른 이름으로 저장…", "Ctrl+Shift+S", self.save_project_as),
                   ("작업 기록 / 복구", "", self.show_jobs)]
        for title, shortcut, callback in actions:
            action = QAction(title, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            file_menu.addAction(action)
        edit = self.menuBar().addMenu("편집")
        for title, shortcut, callback in [("실행 취소", "Ctrl+Z", self.undo), ("다시 실행", "Ctrl+Y", self.redo), ("설정 복사…", "Ctrl+Shift+C", self.copy_settings)]:
            action = QAction(title, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            edit.addAction(action)
        help_menu = self.menuBar().addMenu("도움말")
        about = QAction("FILM LAB 정보", self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)

    @property
    def current(self):
        return self.session.find(self.current_id)

    def selected_photos(self):
        return [photo for item in self.photos.selectedItems() if (photo := self.session.find(item.data(Qt.ItemDataRole.UserRole)))]

    def pick_photos(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Sony α6400 ARW 사진 추가", "", "Sony RAW (*.ARW *.arw)")
        if paths:
            self.add_paths(paths)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.toLocalFile().lower().endswith(".arw") for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def add_paths(self, paths):
        skipped = 0
        for path in paths:
            if not str(path).lower().endswith(".arw"):
                skipped += 1
                continue
            try:
                photo = self.session.add({"id": uuid.uuid4().hex, "path": str(Path(path).resolve()), "supported": False})
            except ValueError as exc:
                QMessageBox.information(self, "사진 추가", str(exc))
                break
            if photo is None:
                skipped += 1
                continue
            photo.status = "검사 중"
            item = self.add_list_item(photo)
            if self.worker:
                token = self.worker.submit("inspect", {"path": photo.path})
                self.requests[token] = {"photo_id": photo.id}
            if self.photos.currentItem() is None:
                self.photos.setCurrentItem(item)
        self.update_counts()
        self.mark_dirty()
        if skipped:
            self.stage.setText(f"중복 경로 또는 ARW가 아닌 파일 {skipped}개를 건너뛰었습니다")

    def add_list_item(self, photo):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, photo.id)
        item.setSizeHint(QSize(185, 79))
        self.photos.addItem(item)
        self.refresh_photo(photo)
        return item

    def item_for(self, photo_id):
        return next((self.photos.item(i) for i in range(self.photos.count()) if self.photos.item(i).data(Qt.ItemDataRole.UserRole) == photo_id), None)

    def refresh_photo(self, photo):
        item = self.item_for(photo.id)
        if item:
            name = Path(photo.path).name
            item.setText(f"{name}\n{photo.status}")
            item.setToolTip(photo.path + ("\n" + photo.error if photo.error else ""))
            if photo.preview and Path(photo.preview).is_file():
                item.setIcon(QIcon(photo.preview))

    def select_photo(self, item, previous=None):
        self.current_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.request_id = uuid.uuid4().hex
        self.load_controls()
        photo = self.current
        self.empty.setVisible(photo is None)
        self.preview.setVisible(photo is not None)
        if photo:
            for key, tile in self.style_buttons.items():
                tile.setIcon(self.style_placeholders[key])
                tile.setToolTip("현재 사진의 스타일 미리보기를 준비합니다.")
            self.preview.item.set_images(QPixmap(), QPixmap())
            self.preview_title.setText(Path(photo.path).name)
            self.stage.setText(photo.error or "미리보기 준비 중")
            self.debounce.start()
        else:
            self.preview_title.setText("미리보기")
        self.update_counts()

    def _set_editor_enabled(self, enabled):
        self.editor.setEnabled(enabled)
        self.zoom.setEnabled(enabled)
        self.compare.setEnabled(enabled)
        self.undo_button.setEnabled(enabled and bool(self.current and self.current.undo_stack))
        self.redo_button.setEnabled(enabled and bool(self.current and self.current.redo_stack))

    def load_controls(self):
        self.loading_controls = True
        photo = self.current
        settings = photo.settings if photo else default_settings()
        for key, control in self.adjustments.items():
            value = settings["grain"][key.split(".")[1]] if key.startswith("grain.") else settings[key]
            control.set_value(value * 100 if key == "strength" else value)
        for key, tile in self.style_buttons.items():
            tile.setChecked(settings["style_id"] == key)
        self.grain_size.setCurrentIndex(max(0, self.grain_size.findData(settings["grain"]["size"])))
        self.loading_controls = False
        self._set_editor_enabled(bool(photo and photo.source.get("supported") and photo.status != "재연결 필요"))
        self.adjustments["saturation"].setEnabled(settings["style_id"] != "monochrome")

    def change_setting(self, key, value):
        if self.loading_controls or not self.current:
            return
        settings = deepcopy(self.current.settings)
        if key == "strength":
            value /= 100
        if key in ("warmth", "tint", "contrast", "saturation", "highlights", "shadows", "grain.amount"):
            value = int(value)
        if key.startswith("grain."):
            settings["grain"][key.split(".")[1]] = value
        else:
            settings[key] = value
        if key in ("warmth", "tint"):
            settings["wb_mode"] = "relative" if settings["warmth"] or settings["tint"] else "as_shot"
        if self.current.update(settings):
            self.changed()

    def set_style(self, style):
        if self.current:
            settings = deepcopy(self.current.settings)
            settings["style_id"] = style
            if self.current.update(settings):
                self.load_controls()
                self.changed()

    def changed(self):
        self.request_id = uuid.uuid4().hex  # Invalidate a running render immediately, before debounce.
        self.debounce.start()
        self.mark_dirty()
        self.undo_button.setEnabled(bool(self.current and self.current.undo_stack))
        self.redo_button.setEnabled(bool(self.current and self.current.redo_stack))

    def mark_dirty(self):
        self.saved_label.setText("변경 사항 저장 대기…")
        self.autosave.start()

    def undo(self):
        if self.current and self.current.undo():
            self.load_controls()
            self.changed()

    def redo(self):
        if self.current and self.current.redo():
            self.load_controls()
            self.changed()

    def reset_all(self):
        if self.current and self.current.update(default_settings()):
            self.load_controls()
            self.changed()

    def change_zoom(self):
        self.preview.fit = self.zoom.currentData() == "preview"
        self.preview.refresh_scale()
        self.request_id = uuid.uuid4().hex
        self.debounce.start()

    def request_render(self):
        photo = self.current
        if not self.worker or not photo or not photo.source.get("supported") or photo.status == "재연결 필요":
            return
        self.request_id = uuid.uuid4().hex
        self.requests = {key: value for key, value in self.requests.items() if value.get("kind") != "render"}
        self.requests[self.request_id] = {"photo_id": photo.id, "kind": "render"}
        self.worker.submit("render", {"source": photo.path, "recipe": {"source": deepcopy(photo.source), "settings": deepcopy(photo.settings)},
                                      "quality": self.zoom.currentData()}, token=self.request_id)
        self.stage.setText("RAW 현상 · 미리보기 준비 중")

    def poll(self):
        if not self.worker:
            return
        for event in self.worker.poll():
            self.handle_event(event)
        if self.exit_pending and not self.worker.process.is_alive():
            self.save_session()
            self.close()

    def handle_event(self, event):
        kind, token, event_type = event["kind"], event["token"], event["event"]
        context = self.requests.get(token, {})
        photo = self.session.find(context.get("photo_id"))
        if kind == "export":
            self.handle_export(event)
        elif kind == "verify":
            if event_type == "done":
                self.session.jobs = event["result"]
                for job in self.session.jobs:
                    for result in job.get("results", []):
                        matching = next((p for p in self.session.photos if p.path == result.get("source")), None)
                        if matching:
                            matching.status = STATUS_LABELS.get(result.get("status"), matching.status)
                            matching.error = result.get("error") or ""
                            self.refresh_photo(matching)
                self.mark_dirty()
        elif kind == "inspect" and photo:
            if event_type == "error":
                photo.status = "재연결 필요" if context.get("reconnect") else "실패"
                photo.error = event["error"]
            else:
                source = event["result"]
                source["id"] = photo.id
                photo.source = source
                photo.status = "대기" if source.get("supported") else "실패"
                photo.error = "\n".join(source.get("warnings", []))
                if not source.get("supported"):
                    photo.error = "Sony ILCE-6400에서 촬영한 ARW만 지원합니다. " + photo.error
            self.refresh_photo(photo)
            if self.current_id == photo.id:
                self.load_controls()
                self.stage.setText(photo.error or "원본 검사 완료")
                if photo.source.get("supported"):
                    self.debounce.start()
            self.mark_dirty()
        elif kind == "render" and token == self.request_id and photo and photo.id == self.current_id:
            if event_type == "error":
                photo.status, photo.error = "실패", event["error"]
                self.stage.setText("미리보기 실패: " + photo.error)
            else:
                result = event["result"]
                self.preview.set_images(result["path"], result["baseline_path"], result.get("tiles"))
                photo.preview = result.get("tiles", {}).get("thumbnail", result["path"])
                photo.error = "\n".join(result.get("warnings", []))
                for style, path in result.get("thumbnails", {}).items():
                    if style in self.style_buttons:
                        self.style_buttons[style].setIcon(QIcon(path))
                        self.style_buttons[style].setToolTip(STYLE_NAMES.get(style, style) + " · 현재 사진의 초기 스타일 미리보기")
                photo.status = "준비"
                self.stage.setText(photo.error or f"미리보기 완료 · {result.get('elapsed_seconds', 0):.1f}초")
                width, height = photo.source.get("width", ""), photo.source.get("height", "")
                detail = "원본 해상도 100%" if self.zoom.currentData() == "full" else "화면 맞춤 · 긴 변 최대 2048 px"
                self.image_info.setText(f"{width} × {height}  ·  {detail}  |  비교 기준: 같은 노출·WB의 RAW 기본 현상")
            self.refresh_photo(photo)
        if event_type in ("done", "error"):
            self.requests.pop(token, None)
        self.update_counts()

    def update_counts(self):
        count = len(self.session.photos)
        selected = len(self.photos.selectedItems())
        self.library_count.setText(f"{count} / 100")
        self.library_empty.setVisible(count == 0)
        self.selection_info.setText(f"선택 {selected}장")
        supported = lambda p: bool(p and p.source.get("supported") and p.status != "재연결 필요")
        self.one_export.setEnabled(supported(self.current))
        self.selected_export.setEnabled(any(supported(p) for p in self.selected_photos()))
        self.all_export.setEnabled(any(supported(p) for p in self.session.photos))

    def remove_selected(self):
        ids = {p.id for p in self.selected_photos()}
        self.session.photos = [p for p in self.session.photos if p.id not in ids]
        for i in reversed(range(self.photos.count())):
            if self.photos.item(i).data(Qt.ItemDataRole.UserRole) in ids:
                self.photos.takeItem(i)
        self.update_counts()
        self.mark_dirty()

    def reconnect(self):
        photo = self.current
        if not photo or not self.worker:
            return
        path, _ = QFileDialog.getOpenFileName(self, "같은 원본 ARW 다시 연결", "", "Sony RAW (*.ARW *.arw)")
        if path:
            token = self.worker.submit("inspect", {"path": path, "expected": photo.source if photo.source.get("sha256") else None})
            self.requests[token] = {"photo_id": photo.id, "reconnect": True}
            photo.status = "검사 중"
            self.refresh_photo(photo)
            self.stage.setText("원본 크기와 SHA-256을 확인하는 중")

    def copy_settings(self):
        photo = self.current
        targets = [p for p in self.selected_photos() if p.id != self.current_id]
        if not photo or not targets:
            QMessageBox.information(self, "설정 복사", "Ctrl 또는 Shift를 눌러 두 장 이상 선택하세요. 현재 화면의 사진 설정을 다른 선택 사진에 적용합니다.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("선택 사진에 설정 복사")
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"{Path(photo.path).name} → 선택한 {len(targets)}장"))
        groups = [("스타일과 강도", ["style_id", "style_version", "strength"], True),
                  ("명암과 채도", ["contrast", "saturation", "highlights", "shadows"], True),
                  ("입자감", ["grain"], True), ("노출", ["exposure_ev"], False),
                  ("화이트밸런스", ["wb_mode", "warmth", "tint"], False)]
        checks = []
        for title, keys, selected in groups:
            check = QCheckBox(title)
            check.setChecked(selected)
            layout.addWidget(check)
            checks.append((check, keys))
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        actions.button(QDialogButtonBox.StandardButton.Ok).setText("선택 사진에 적용")
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        actions.accepted.connect(dialog.accept)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for target in targets:
                settings = deepcopy(target.settings)
                for check, keys in checks:
                    if check.isChecked():
                        for key in keys:
                            settings[key] = deepcopy(photo.settings[key])
                target.update(settings)
            self.mark_dirty()
            self.stage.setText(f"선택한 {len(targets)}장에 설정을 적용했습니다")

    def export_photos(self, scope):
        candidates = [self.current] if scope == "one" else self.selected_photos() if scope == "selected" else self.session.photos
        photos = [p for p in candidates if p and p.source.get("supported") and p.status != "재연결 필요"]
        if not photos or not self.worker:
            return
        dialog = ExportDialog(len(photos), self.session.export_options, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        options = dialog.options()
        self.session.export_options = options
        job = self.session.snapshot_job(photos, options)
        if not self.save_session():
            job["status"] = "failed"
            job["error"] = "프로젝트에 작업 상태를 기록할 수 없습니다."
            self.stage.setText(job["error"])
            return
        self.worker.submit("export", {"items": job["items"], "options": options}, token=job["id"])
        for photo in photos:
            photo.status = "대기"
            self.refresh_photo(photo)
        self.location.setText("저장 위치: " + options["output_dir"])
        self.location.setToolTip(options["output_dir"])
        self.stage.setText(f"{len(photos)}장 내보내기 대기 · 등록 시점의 설정 사용")
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, len(photos))
        self.progress.setValue(0)

    def handle_export(self, event):
        job = next((j for j in self.session.jobs if j["id"] == event["token"]), None)
        if not job:
            return
        if event["event"] == "started":
            job["status"] = "processing"
            if job["items"]:
                photo = self.session.find(job["items"][0]["photo_id"])
                if photo:
                    photo.status = "처리"
                    self.refresh_photo(photo)
            self.stage.setText(f"RAW 현상 및 저장 · 0/{len(job['items'])}장 완료")
            self.save_session()
        elif event["event"] == "progress":
            result = event["result"]
            if not isinstance(result, dict) or "status" not in result:
                self.stage.setText(str(result))
                return
            job["status"] = "processing"
            job["results"].append(result)
            self._export_photo_status(job, result)
            self.progress.setRange(0, len(job["items"]))
            self.progress.setValue(len(job["results"]))
            self.stage.setText(f"내보내기 {len(job['results'])}/{len(job['items'])}장 · {STATUS_LABELS.get(result['status'], result['status'])}")
            next_index = len(job["results"])
            if next_index < len(job["items"]):
                photo = self.session.find(job["items"][next_index]["photo_id"])
                if photo:
                    photo.status = "처리"
                    self.refresh_photo(photo)
            self.save_session()
        elif event["event"] == "error":
            job["status"] = "failed"
            job["error"] = event["error"]
            self.stage.setText("내보내기 실패: " + event["error"])
            self.save_session()
        else:
            results = event["result"]
            job["results"] = results
            for result in results:
                self._export_photo_status(job, result)
            complete = sum(r.get("status") == "completed" for r in results)
            partial = sum(r.get("status") == "partial" for r in results)
            failed = sum(r.get("status") == "failed" for r in results)
            cancelled = sum(r.get("status") == "cancelled" for r in results)
            job["status"] = "completed" if complete == len(job["items"]) else "cancelled" if cancelled else "partial" if complete or partial else "failed"
            self.stage.setText(f"내보내기 완료 {complete}장 · 부분 완료 {partial} · 실패 {failed} · 취소 {cancelled}")
            self.progress.setValue(len(results))
            self.save_session()
        self.cancel_button.setEnabled(any(j["status"] in ("waiting", "processing") for j in self.session.jobs))

    def _export_photo_status(self, job, result):
        source = result.get("source")
        if isinstance(source, dict):
            source = source.get("path")
        item = next((item for item in job["items"] if item["source"] == source), None)
        photo = self.session.find(item["photo_id"]) if item else None
        if photo:
            photo.status = STATUS_LABELS.get(result.get("status"), "실패")
            photo.error = result.get("error") or ""
            self.refresh_photo(photo)

    def cancel_exports(self):
        if not self.worker:
            return
        cancelled = self.worker.cancel()
        for task in cancelled:
            job = next((j for j in self.session.jobs if j["id"] == task["token"]), None)
            if job:
                job["status"] = "cancelled"
                for item in job["items"]:
                    photo = self.session.find(item["photo_id"])
                    if photo:
                        photo.status = "취소"
                        self.refresh_photo(photo)
        self.stage.setText("취소 요청됨 · 현재 처리 단계가 끝나면 중단합니다")
        self.save_session()

    def save_session(self):
        try:
            self.session.save()
            self.preferences.setValue("last_project", str(self.session.path))
            self.saved_label.setText("프로젝트 저장됨")
            self.saved_label.setToolTip(str(self.session.path))
            return True
        except Exception as exc:
            self.saved_label.setText("저장 실패 · " + str(exc))
            return False

    def save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "프로젝트 저장", str(self.session.path), "FILM LAB 프로젝트 (*.filmproject.json)")
        if path:
            if not path.lower().endswith(".json"):
                path += ".filmproject.json"
            self.session.path = Path(path)
            self.save_session()

    def open_project(self):
        if self.worker and (self.worker.active or self.worker.pending):
            QMessageBox.information(self, "프로젝트 열기", "진행 중인 작업이 끝난 뒤 프로젝트를 열어 주세요. 내보내기는 작업 취소로 중단할 수 있습니다.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 열기", "", "FILM LAB 프로젝트 (*.filmproject.json *.json)")
        if path:
            self.save_session()
            self.load_project(path)

    def load_project(self, path):
        try:
            session = Session.load(path)
        except Exception as exc:
            QMessageBox.warning(self, "프로젝트 열기 실패", str(exc))
            return
        self.photos.clear()
        self.session = session
        self.current_id = None
        for photo in session.photos:
            self.add_list_item(photo)
            if self.worker and not photo.source.get("sha256"):
                photo.status = "검사 중"
                token = self.worker.submit("inspect", {"path": photo.path})
                self.requests[token] = {"photo_id": photo.id}
                self.refresh_photo(photo)
        if self.photos.count():
            self.photos.setCurrentRow(0)
        if self.worker and session.jobs:
            self.worker.submit("verify", {"jobs": deepcopy(session.jobs)})
        self.location.setText("저장 위치: " + (session.export_options.get("output_dir") or "내보낼 때 선택"))
        self.saved_label.setText("프로젝트 복구됨")
        self.saved_label.setToolTip(str(session.path))
        interrupted = sum(j.get("status") == "interrupted" for j in session.jobs)
        if interrupted:
            self.stage.setText(f"중단된 작업 {interrupted}개 복구 · 프로젝트 > 작업 기록에서 확인")
        self.update_counts()

    def show_jobs(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("작업 기록 / 복구")
        dialog.resize(720, 430)
        layout = QVBoxLayout(dialog)
        note = label("중단된 작업은 자동으로 재실행하지 않습니다. 결과를 확인한 뒤 필요한 사진을 다시 내보내세요.", "Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        listing = QListWidget()
        for job in reversed(self.session.jobs):
            status = STATUS_LABELS.get(job.get("status"), {"waiting": "대기", "processing": "처리", "interrupted": "중단됨"}.get(job.get("status"), "확인 필요"))
            listing.addItem(f"{status} · {len(job.get('items', []))}장 · {job.get('options', {}).get('output_dir', '')}")
            for result in job.get("results", []):
                listing.addItem(f"    {STATUS_LABELS.get(result.get('status'), result.get('status', ''))}  {result.get('path') or result.get('source', '')}\n    {result.get('error') or ''}")
        if not self.session.jobs:
            listing.addItem("아직 내보내기 기록이 없습니다.")
        layout.addWidget(listing)
        done = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        done.rejected.connect(dialog.reject)
        layout.addWidget(done)
        dialog.exec()

    def about(self):
        QMessageBox.information(self, "FILM LAB · α6400", "FILM LAB 1.0.0\nSony ILCE-6400 ARW를 위한 로컬 사진 현상 프로그램\n\n사진에 필름 느낌을 더하는 자체 제작 스타일을 제공합니다.\n현재 프리셋은 비교 촬영으로 보정되기 전의 ‘초기 스타일’입니다. 실제 필름의 색감과 동일한 결과를 보장하지 않습니다.\n\n비교 화면의 기본 현상은 같은 RAW의 기준 렌더이며 카메라 내장 JPEG가 아닙니다. 하이라이트·그림자는 명암 조절입니다.\n\n16비트 TIFF는 현상된 이미지입니다. RAW와 같은 편집 여유를 갖지 않습니다.")

    def closeEvent(self, event):
        self.autosave.stop()
        self.debounce.stop()
        self.save_session()
        if self.worker and self.worker.process.is_alive():
            if not self.exit_pending:
                self.exit_pending = True
                self.worker.shutdown()
                self.stage.setText("종료 준비 · 현재 처리 단계 완료 후 안전하게 종료합니다")
                self.centralWidget().setEnabled(False)
            event.ignore()
        else:
            event.accept()


def main() -> int:
    import multiprocessing
    multiprocessing.freeze_support()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("FilmLab")
    app.setOrganizationName("FilmLab")
    app.setFont(QFont("Segoe UI", 10))
    window = FilmLabWindow()
    window.show()
    return app.exec()


def self_test(source=None, output=None) -> int:
    """Exercise the real desktop worker, including in a frozen Windows build.

    The explicit data directory keeps smoke tests separate from user sessions.
    A supplied RAW must produce a real preview before this reports success.
    """
    import multiprocessing
    import time
    multiprocessing.freeze_support()
    data_dir = os.environ.get("FILM_LAB_DATA")
    if not data_dir:
        print("GUI_SELF_TEST_ERROR: FILM_LAB_DATA must point to an isolated test directory", file=sys.stderr)
        return 2
    if source and not Path(source).is_file():
        print(f"GUI_SELF_TEST_ERROR: source does not exist: {source}", file=sys.stderr)
        return 2
    destination = Path(output) if output else Path(data_dir) / "gui-self-test.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("FilmLab")
    app.setOrganizationName("FilmLab")
    window = FilmLabWindow(start_worker=True, project_path=Path(data_dir) / ("self-test-" + uuid.uuid4().hex + ".filmproject.json"))
    window.resize(1440, 900)
    window.show()
    result = {"code": 1, "finishing": False, "message": "timeout"}
    started = time.monotonic()
    timer = QTimer(window)
    timer.setInterval(100)

    def finish(code, message):
        if result["finishing"]:
            return
        result.update(code=code, finishing=True, message=message)
        if not window.grab().save(str(destination)):
            result.update(code=1, message="screenshot could not be saved")
        window.close()  # Keeps pumping Qt while cancellation reaches a boundary.

    def check():
        if time.monotonic() - started >= 120:
            result.update(code=1, finishing=True, message="120-second timeout")
            if window.worker and window.worker.process.is_alive():
                window.worker.process.terminate()
                window.worker.process.join(timeout=0.5)
            window.close()
            return
        if result["finishing"]:
            return
        if source is None:
            finish(0, "empty workspace opened")
            return
        photo = window.current
        if photo and photo.preview and window.preview.item.width:
            finish(0, "real RAW preview rendered")
        elif photo and photo.status == "실패":
            finish(1, photo.error)

    if source:
        window.add_paths([str(Path(source).resolve())])
    timer.timeout.connect(check)
    timer.start()
    app.exec()
    print(("GUI_SELF_TEST_OK" if result["code"] == 0 else "GUI_SELF_TEST_ERROR")
          + f": {result['message']}; screenshot={destination}")
    return result["code"]
