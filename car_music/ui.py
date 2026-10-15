from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QScrollArea,
)

from . import __version__
from .convert import Options, Result, convert_one
from .crypto import AudioError, Cancelled, Library
from .discovery import EXTENSIONS, Track, load_library, music_roots, scan_paths
from .reports import export_results


def app_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]


def app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#194D73"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QPen(QColor("#FFFFFF"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for x, low, high in [(17, 29, 40), (27, 19, 46), (37, 13, 43), (47, 24, 36)]:
        painter.drawLine(x, low, x, high)
    painter.end()
    return QIcon(pix)


STYLE = """
QMainWindow { background: #EFF4F8; }
QWidget#settingsViewport { background: #EFF4F8; }
QScrollBar:vertical { background: #EFF4F8; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #C5D6E3; min-height: 35px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QWidget { color: #203A4E; font-size: 13px; }
QLabel#title { font-size: 27px; font-weight: 700; color: #163C59; }
QLabel#subtitle, QLabel#muted { color: #627989; }
QLabel#section { font-size: 17px; font-weight: 700; }
QLabel#warning { color: #986019; }
QLabel#keyStatus { color: #627989; font-size: 12px; }
QLabel#empty { color: #78909F; font-size: 17px; }
QFrame#sidebar { background: #FFFFFF; border: 1px solid #DCE6EE; border-radius: 10px; }
QPushButton { background: #FFFFFF; border: 1px solid #C9D8E3; border-radius: 6px; padding: 9px 13px; font-weight: 500; }
QPushButton:hover { background: #E7F0F6; border-color: #819FB4; }
QPushButton:pressed { background: #DCE9F2; }
QPushButton:focus { border: 2px solid #2F7BA7; }
QPushButton:disabled { color: #9AAAB5; background: #F4F6F8; border-color: #E0E7EC; }
QPushButton#primary { background: #225E88; color: #FFFFFF; border: 1px solid #225E88; }
QPushButton#primary:hover { background: #174B70; }
QPushButton#primary:disabled { background: #A8BECE; border-color: #A8BECE; }
QPushButton#start { background: #225E88; color: white; padding: 13px 24px; font-size: 16px; font-weight: 700; border-color: #225E88; }
QPushButton#start:hover { background: #174B70; }
QPushButton#start:disabled { background: #A8BECE; border-color: #A8BECE; }
QLineEdit, QComboBox { background: white; border: 1px solid #CAD9E3; border-radius: 5px; padding: 8px; min-height: 19px; }
QLineEdit:focus, QComboBox:focus { border: 2px solid #2F7BA7; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #DAE9F3; selection-color: #203A4E; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QTableWidget { background: white; alternate-background-color: #F6F9FB; border: 1px solid #DCE6EE; border-radius: 6px; gridline-color: #EBF0F4; selection-background-color: #DDEDF7; selection-color: #203A4E; }
QHeaderView::section { background: #E5EEF5; color: #476479; border: none; border-bottom: 1px solid #D3E0EA; padding: 11px 8px; font-weight: 600; }
QTableWidget::item { padding: 8px; }
QProgressBar { background: #DDE8F0; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #347AA4; border-radius: 4px; }
QTextEdit { background: #E8F0F6; color: #526F83; border: none; border-radius: 6px; padding: 6px; font-size: 12px; }
QToolTip { background: #FFFFFF; color: #203A4E; border: 1px solid #B3C8D6; padding: 6px; }
"""


def setup_font(app):
    # The offscreen Qt plugin does not enumerate Windows fonts. Register the
    # installed CJK font explicitly, also covering systems with unusual font settings.
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    families = QFontDatabase.families()
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        path = fonts / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.families()
    family = next((name for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimSun", "Segoe UI") if name in families), "sans-serif")
    app.setFont(QFont(family, 10))


class ScanWorker(QThread):
    completed = Signal(object, object, object)
    count = Signal(int)

    def __init__(self, paths, db, output, automatic=False, load_only=False):
        super().__init__()
        self.paths, self.db, self.output = paths, db, output
        self.automatic, self.load_only = automatic, load_only
        self.stop = threading.Event()

    def run(self):
        try:
            library = load_library(self.db)
            paths = music_roots() if self.automatic else self.paths
            if self.load_only:
                self.completed.emit([], library, library.errors)
                return
            tracks, errors = scan_paths(paths, library, self.stop.is_set, self.count.emit, self.output)
            if self.automatic and not paths:
                errors.append("未找到酷狗默认目录，请用“添加文件夹”选择下载或缓存目录。")
            self.completed.emit(tracks, library, library.errors + errors)
        except Exception as exc:
            self.completed.emit([], Library(), [f"扫描失败：{exc}"])


class BatchWorker(QThread):
    changed = Signal(int, int, str)
    item_done = Signal(int, object)
    completed = Signal(object)

    def __init__(self, jobs, options, library):
        super().__init__()
        self.jobs, self.options, self.library = jobs, options, library
        self.stop = threading.Event()

    def run(self):
        results = []
        for row, track in self.jobs:
            if self.stop.is_set():
                break
            try:
                result = convert_one(track, self.options, self.library, self.stop,
                                     lambda p, text, r=row: self.changed.emit(r, p, text))
            except Cancelled as exc:
                result = Result(str(track.path), "已取消", note=str(exc))
            except Exception as exc:
                result = Result(str(track.path), "失败", note=str(exc))
            results.append(result)
            self.item_done.emit(row, result)
            if self.stop.is_set():
                break
        self.completed.emit(results)


class Window(QMainWindow):
    def __init__(self, inputs: list[Path] | None = None):
        super().__init__()
        self.setWindowTitle("随行音乐转换器")
        self.setWindowIcon(app_icon())
        self.resize(1240, 820)
        self.setMinimumSize(1060, 740)
        self.setAcceptDrops(True)
        self.library = Library()
        self.tracks: list[Track] = []
        self.results: dict[int, Result] = {}
        self.batch_results: list[Result] = []
        self.worker = None
        self.busy = False
        self.jobs = []
        self.completed_count = 0
        self.settings_path = app_dir() / "设置.json"
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        self.custom_db = str(settings.get("database", ""))

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 25, 28, 22)
        outer.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("随行音乐转换器")
        title.setObjectName("title")
        subtitle = QLabel("把本机音乐转换成车机能播放的格式。原文件始终保留。")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        local = QLabel(f"本地处理\n版本 {__version__}")
        local.setObjectName("muted")
        local.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(local)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(22)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(296)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(20, 20, 20, 20)
        sl.setSpacing(11)
        sl.addWidget(self.label("输出设置", "section"))
        sl.addSpacing(3)
        sl.addWidget(QLabel("目标格式"))
        self.format = QComboBox()
        for text, value in [("MP3 · 通用车载播放", "mp3"), ("FLAC · 保留无损音源", "flac"), ("原始格式 · 只解密", "original")]:
            self.format.addItem(text, value)
        self.format.setCurrentIndex(max(0, self.format.findData(settings.get("format", "mp3"))))
        sl.addWidget(self.format)
        self.format_note = self.label("", "muted")
        self.format_note.setWordWrap(True)
        self.format_note.setMinimumHeight(52)
        sl.addWidget(self.format_note)
        lyric_row = QHBoxLayout()
        lyric_row.addWidget(QLabel("歌词输出"))
        self.lyric_format = QComboBox()
        self.lyric_format.addItem("LRC · 同步歌词", "lrc")
        self.lyric_format.addItem("TXT · 纯文本", "txt")
        self.lyric_format.setCurrentIndex(max(0, self.lyric_format.findData(settings.get("lyric_format", "lrc"))))
        self.lyric_format.setToolTip("KRC 自动解码；已有翻译会保留。纯 TXT 无时间轴，会继续保存为 TXT。")
        lyric_row.addWidget(self.lyric_format, 1)
        sl.addLayout(lyric_row)
        sl.addWidget(QLabel("MP3 码率"))
        self.bitrate = QComboBox()
        for value in (320, 256, 192, 128):
            self.bitrate.addItem(f"{value} kbps", value)
        self.bitrate.setCurrentIndex(max(0, self.bitrate.findData(settings.get("bitrate", 320))))
        sl.addWidget(self.bitrate)
        self.car_mode = QCheckBox("车载兼容模式")
        self.car_mode.setChecked(bool(settings.get("car_mode", True)))
        self.car_mode.setToolTip("重采样为 44.1 kHz、双声道；FLAC 限制为 16 bit。高解析音源会降至 CD 规格。")
        sl.addWidget(self.car_mode)
        self.car_note = self.label("44.1 kHz、双声道；FLAC 为 16 bit。\n高解析音源会降至 CD 规格。", "muted")
        self.car_note.setWordWrap(True)
        sl.addWidget(self.car_note)
        sl.addSpacing(7)
        sl.addWidget(QLabel("输出目录"))
        self.output = QLineEdit(str(settings.get("output", app_dir() / "已转换")))
        self.output.setAccessibleName("输出目录")
        self.output.setToolTip(self.output.text())
        sl.addWidget(self.output)
        choose = QPushButton("选择输出目录…")
        choose.clicked.connect(self.choose_output)
        sl.addWidget(choose)
        self.choose_output_button = choose
        open_output = QPushButton("打开输出目录")
        open_output.clicked.connect(self.open_output)
        sl.addWidget(open_output)
        sl.addStretch()
        sl.addWidget(self.label("新版文件密钥", "section"))
        self.key_status = self.label("正在检查本机酷狗数据库…", "keyStatus")
        self.key_status.setWordWrap(True)
        self.key_status.setMinimumHeight(49)
        sl.addWidget(self.key_status)
        self.db_button = QPushButton("选择 KGMusicV3.db…")
        self.db_button.clicked.connect(self.choose_database)
        sl.addWidget(self.db_button)
        self.reload = QPushButton("重新扫描密钥库")
        self.reload.clicked.connect(lambda: self.scan([], load_only=True))
        sl.addWidget(self.reload)
        # Keep every control reachable on smaller displays as options grow.
        side.setMinimumWidth(280)
        side.setMaximumWidth(296)
        side_scroll = QScrollArea()
        side_scroll.setFixedWidth(312)
        side_scroll.setWidgetResizable(True)
        side_scroll.viewport().setObjectName("settingsViewport")
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setWidget(side)
        body.addWidget(side_scroll)

        main = QVBoxLayout()
        main.setSpacing(12)
        queue_header = QHBoxLayout()
        queue_header.addWidget(self.label("转换队列", "section"))
        queue_header.addStretch()
        self.counter = self.label("0 首", "muted")
        queue_header.addWidget(self.counter)
        main.addLayout(queue_header)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.add_files_button = QPushButton("添加文件")
        self.add_files_button.setObjectName("primary")
        self.add_files_button.clicked.connect(self.add_files)
        self.add_folder_button = QPushButton("添加文件夹")
        self.add_folder_button.clicked.connect(self.add_folder)
        self.find_button = QPushButton("扫描酷狗缓存")
        self.find_button.clicked.connect(lambda: self.scan([], automatic=True))
        for b in (self.add_files_button, self.add_folder_button, self.find_button):
            tools.addWidget(b)
        tools.addStretch()
        main.addLayout(tools)
        subtools = QHBoxLayout()
        self.remove_button = QPushButton("移除选中")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("清空列表")
        self.clear_button.clicked.connect(self.clear_queue)
        self.report_button = QPushButton("导出转换报告")
        self.report_button.clicked.connect(self.save_report)
        for b in (self.remove_button, self.clear_button, self.report_button):
            subtools.addWidget(b)
        subtools.addStretch()
        main.addLayout(subtools)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["歌曲 / 歌词文件", "源格式", "大小", "状态"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 85)
        self.table.setColumnWidth(3, 150)
        self.table.cellDoubleClicked.connect(self.open_item)
        self.table.setToolTip("双击成功记录定位输出文件；其他记录显示源文件所在目录。")
        main.addWidget(self.table, 1)
        self.empty = self.label("拖入音乐、KRC 歌词或文件夹，也可以扫描酷狗目录。", "empty")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(55)
        main.addWidget(self.empty)
        self.status = QLabel("支持 KGM、KGMA、KGG 音频和 KRC、LRC、TXT 歌词。")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(40)
        main.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        main.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.start_button = QPushButton("开始批量转换")
        self.start_button.setObjectName("start")
        self.start_button.clicked.connect(self.start_batch)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        bottom.addWidget(self.start_button)
        bottom.addWidget(self.cancel_button)
        bottom.addStretch()
        main.addLayout(bottom)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        self.log.setPlaceholderText("处理说明和失败原因会显示在这里。转换后会自动保存 CSV 报告。")
        main.addWidget(self.log)
        body.addLayout(main, 1)
        outer.addLayout(body, 1)
        self.format.currentIndexChanged.connect(self.format_changed)
        self.format_changed(initial=True)
        self.scan(inputs or [], load_only=not inputs)

    @staticmethod
    def label(text, name):
        result = QLabel(text)
        result.setObjectName(name)
        return result

    def format_changed(self, *args, initial=False):
        fmt = self.format.currentData()
        self.bitrate.setEnabled(fmt == "mp3" and not self.busy)
        self.car_mode.setEnabled(fmt != "original" and not self.busy)
        if not initial:
            self.car_mode.setChecked(fmt == "mp3")
        self.format_note.setText({
            "mp3": "兼容多数车机。MP3 编码会有音质损失；码率越高，文件越大。",
            "flac": "无损音源可保留原音质。MP3 等有损音源转 FLAC，不能恢复音质。",
            "original": "只移除加密，保留原音频字节。原来是 MP3 就输出 MP3，是 FLAC 就输出 FLAC。",
        }[fmt])

    def set_busy(self, busy):
        self.busy = busy
        for button in (self.add_files_button, self.add_folder_button, self.find_button, self.remove_button,
                       self.clear_button, self.db_button, self.reload, self.choose_output_button):
            button.setEnabled(not busy)
        self.output.setEnabled(not busy)
        self.lyric_format.setEnabled(not busy)
        self.format.setEnabled(not busy)
        self.bitrate.setEnabled(not busy and self.format.currentData() == "mp3")
        self.car_mode.setEnabled(not busy and self.format.currentData() != "original")
        self.start_button.setEnabled(not busy and bool(self.tracks))
        self.report_button.setEnabled(not busy and bool(self.batch_results))
        self.cancel_button.setEnabled(busy)

    def scan(self, paths, automatic=False, load_only=False):
        if self.busy:
            return
        self.set_busy(True)
        self.status.setText("正在读取密钥库…" if load_only else "正在扫描文件头，识别下载文件和缓存…")
        self.progress.setRange(0, 0)
        worker = ScanWorker(paths, self.custom_db, Path(self.output.text()).resolve(), automatic, load_only)
        worker.count.connect(lambda count: self.status.setText(f"已发现 {count} 个音频或歌词文件…"))
        worker.completed.connect(self.scan_complete)
        worker.finished.connect(self.scan_finished)
        self.worker = worker
        worker.start()

    def scan_complete(self, tracks, library, errors):
        self.library = library
        known = {t.path for t in self.tracks}
        for track in tracks:
            if track.path in known:
                continue
            known.add(track.path)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.tracks.append(track)
            size = f"{track.size / 1024:.1f} KB" if track.size < 1024 * 1024 else f"{track.size / 1024 / 1024:.1f} MB"
            for column, value in enumerate((track.name, track.kind, size, "等待转换")):
                item = QTableWidgetItem(value)
                item.setToolTip(str(track.path))
                self.table.setItem(row, column, item)
        if library.databases:
            self.key_status.setText(f"已读取 {len(library.databases)} 个数据库\n新版歌曲密钥：{len(library.keys)} 条")
            self.key_status.setToolTip("\n".join(library.databases))
        else:
            self.key_status.setText("未找到可读取的酷狗数据库。\nKGM v3 和歌词仍可直接转换。")
        unknown = sum("待验证" in t.kind for t in self.tracks)
        self.counter.setText(f"{len(self.tracks)} 首")
        self.empty.setVisible(not self.tracks)
        if tracks:
            self.status.setText(f"队列 {len(self.tracks)} 首，其中 {unknown} 个缓存需进一步验证。缺少密钥或未知格式会单独报告。")
        else:
            self.status.setText("密钥库检查完成。添加文件或扫描酷狗缓存，即可开始。" if not self.tracks else f"队列 {len(self.tracks)} 首，准备就绪。")
        for error in errors[:30]:
            self.log.append(error)

    def scan_finished(self):
        self.worker = None
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.set_busy(False)

    def add_files(self):
        names, _ = QFileDialog.getOpenFileNames(self, "选择本机音乐或歌词", "", "音乐与歌词 (*.kgm *.kgma *.kgg *.flac *.mp3 *.wav *.ogg *.m4a *.aac *.vpr *.krc *.lrc *.txt);;歌词 (*.krc *.lrc *.txt);;所有文件 (*)")
        if names:
            self.scan([Path(p) for p in names])

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择下载或缓存文件夹")
        if path:
            self.scan([Path(path)])

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output.text())
        if path:
            self.output.setText(path)

    def choose_database(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择酷狗本机数据库", "", "酷狗数据库 (KGMusicV3.db);;数据库 (*.db)")
        if path:
            self.custom_db = path
            self.scan([], load_only=True)

    def open_output(self):
        path = Path(self.output.text()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        except OSError as exc:
            QMessageBox.warning(self, "无法打开目录", str(exc))

    def open_item(self, row, column):
        result = self.results.get(row)
        path = Path(result.output) if result and result.output else self.tracks[row].path
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.tracks.pop(row)
            self.table.removeRow(row)
        self.results.clear()
        self.counter.setText(f"{len(self.tracks)} 首")
        self.empty.setVisible(not self.tracks)
        self.set_busy(False)

    def clear_queue(self):
        self.table.setRowCount(0)
        self.tracks.clear()
        self.results.clear()
        self.counter.setText("0 首")
        self.empty.setVisible(True)
        self.progress.setValue(0)
        self.status.setText("列表已清空。原文件和已完成输出均已保留。")
        self.set_busy(False)

    def start_batch(self):
        if self.busy or not self.tracks:
            return
        if not self.output.text().strip():
            QMessageBox.warning(self, "选择输出目录", "请先指定输出目录。")
            return
        self.jobs = [(row, track) for row, track in enumerate(self.tracks)]
        self.completed_count = 0
        self.batch_results = []
        self.results.clear()
        for row in range(len(self.tracks)):
            self.table.item(row, 3).setText("等待转换")
            self.table.item(row, 3).setForeground(QColor("#203A4E"))
        options = Options(Path(self.output.text()), self.format.currentData(), self.bitrate.currentData(), self.car_mode.isChecked(), self.lyric_format.currentData())
        self.set_busy(True)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.save_settings()
        worker = BatchWorker(self.jobs, options, self.library)
        worker.changed.connect(self.item_progress)
        worker.item_done.connect(self.item_done)
        worker.completed.connect(self.batch_complete)
        worker.finished.connect(self.batch_finished)
        self.worker = worker
        worker.start()

    def item_progress(self, row, percent, text):
        self.table.item(row, 3).setText(f"{text} {percent}%")
        self.progress.setValue(int((self.completed_count + percent / 100) / len(self.jobs) * 1000))
        self.status.setText(f"{self.completed_count + 1} / {len(self.jobs)}  {self.tracks[row].name}  {text}")

    def item_done(self, row, result):
        self.results[row] = result
        self.completed_count += 1
        item = self.table.item(row, 3)
        item.setText(result.status)
        item.setForeground(QColor("#26745E" if result.status == "成功" else "#A05C27"))
        item.setToolTip(result.output + ("\n" + result.note if result.note else ""))
        if result.original_format:
            self.table.item(row, 1).setText(result.original_format.upper())
        if result.status != "成功" or result.note:
            self.log.append(f"{self.tracks[row].name}：{result.note}")
        self.progress.setValue(int(self.completed_count / len(self.jobs) * 1000))

    def batch_complete(self, results):
        self.batch_results = results
        success = sum(r.status == "成功" for r in results)
        failed = sum(r.status == "失败" for r in results)
        cancelled = len(self.jobs) - success - failed
        self.status.setText(f"本批次：成功 {success} 首，失败 {failed} 首" + (f"，取消 / 未处理 {cancelled} 首。" if cancelled else "。") + "原文件均已保留。")
        if results:
            try:
                path = Path(self.output.text()) / f"转换报告-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1000000:06d}.csv"
                path.parent.mkdir(parents=True, exist_ok=True)
                export_results(path, results)
                self.log.append(f"转换报告已保存：{path}")
            except OSError as exc:
                self.log.append(f"报告未能自动保存，可点“导出转换报告”：{exc}")

    def batch_finished(self):
        self.worker = None
        self.set_busy(False)

    def cancel(self):
        if self.worker:
            self.worker.stop.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消并清理本次临时文件…已完成输出会保留。")

    def save_report(self):
        name, _ = QFileDialog.getSaveFileName(self, "保存本批次转换报告", str(Path(self.output.text()) / "转换报告.csv"), "CSV 报告 (*.csv)")
        if name:
            try:
                export_results(Path(name), self.batch_results)
            except OSError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))

    def save_settings(self):
        settings = {"output": self.output.text(), "format": self.format.currentData(), "bitrate": self.bitrate.currentData(),
                    "car_mode": self.car_mode.isChecked(), "database": self.custom_db, "lyric_format": self.lyric_format.currentData()}
        try:
            self.settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            self.log.append("当前程序目录不可写，本次设置未保存。转换功能仍可使用。")

    def closeEvent(self, event):
        if self.busy:
            self.cancel()
            event.ignore()
            self.log.append("正在结束当前任务，完成清理后可关闭窗口。")
            return
        self.save_settings()
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.busy:
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.busy:
            paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.scan(paths)
                event.acceptProposedAction()


def run_gui(inputs=None) -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("随行音乐转换器")
    app.setStyle("Fusion")
    setup_font(app)
    app.setStyleSheet(STYLE)
    window = Window(inputs)
    window.show()
    return app.exec()
