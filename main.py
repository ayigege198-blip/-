"""小傻瓜环境配置 - 主程序(马卡龙配色 + 粉猪图标)"""
import sys
import os
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QRectF, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QBrush, QColor, QPen
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QCheckBox, QTabWidget, QTextEdit, QGroupBox,
    QMessageBox, QDialog, QListWidget, QListWidgetItem, QLineEdit,
    QFormLayout, QFileDialog, QStackedWidget, QScrollArea,
)


def resource_path(rel: str) -> str:
    """获取资源路径(支持 PyInstaller 打包后)"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)

from software_config import CORE_STACK, EXTERNAL_TOOLS, AI_TOOLS
from detector import detect
from installer import install_stream
import ai_config
from environment_diagnostics import (
    run_environment_diagnostics,
    run_environment_repair,
    run_environment_precheck,
    scan_repairable_issues,
    run_selected_repairs,
)

APP_NAME = "小傻瓜环境配置"
APP_VERSION = "0.1.0"

# ========== 马卡龙调色板 ==========
COLOR_BG = "#FFF5F7"          # 浅奶粉背景
COLOR_CARD = "#FFFFFF"        # 卡片白
COLOR_BORDER = "#FFD6E0"      # 粉边框
COLOR_TEXT = "#5C4A57"        # 暖灰文字
COLOR_TEXT_DIM = "#A89BA3"    # 浅灰文字
COLOR_PRIMARY = "#FF9EBB"     # 主色:马卡龙粉
COLOR_PRIMARY_HOVER = "#FF85A8"
COLOR_OK = "#7DC9A6"          # 薄荷绿
COLOR_WARN = "#F4A261"        # 奶橘
COLOR_TAB_BG = "#FFE5EC"      # 浅粉 Tab 背景
COLOR_LOG_BG = "#FFF9FB"      # 极浅粉日志框


# ========== 粉猪图标(代码绘制) ==========
def create_pig_icon(size: int = 64) -> QIcon:
    """用 QPainter 画一个粉色小猪图标"""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    pink = QColor("#FFB6C9")
    deep_pink = QColor("#FF8FA8")
    dark = QColor("#5C4A57")

    s = size
    # 两只耳朵(三角形)
    p.setBrush(QBrush(deep_pink))
    p.setPen(Qt.PenStyle.NoPen)
    from PyQt6.QtGui import QPolygonF
    from PyQt6.QtCore import QPointF
    left_ear = QPolygonF([QPointF(s*0.20, s*0.30), QPointF(s*0.32, s*0.10), QPointF(s*0.40, s*0.32)])
    right_ear = QPolygonF([QPointF(s*0.80, s*0.30), QPointF(s*0.68, s*0.10), QPointF(s*0.60, s*0.32)])
    p.drawPolygon(left_ear)
    p.drawPolygon(right_ear)

    # 脸(圆)
    p.setBrush(QBrush(pink))
    p.drawEllipse(QRectF(s*0.12, s*0.22, s*0.76, s*0.68))

    # 鼻子(椭圆)
    p.setBrush(QBrush(deep_pink))
    p.drawEllipse(QRectF(s*0.34, s*0.55, s*0.32, s*0.22))

    # 鼻孔
    p.setBrush(QBrush(dark))
    p.drawEllipse(QRectF(s*0.41, s*0.62, s*0.06, s*0.08))
    p.drawEllipse(QRectF(s*0.53, s*0.62, s*0.06, s*0.08))

    # 眼睛
    p.drawEllipse(QRectF(s*0.30, s*0.42, s*0.08, s*0.08))
    p.drawEllipse(QRectF(s*0.62, s*0.42, s*0.08, s*0.08))

    # 腮红
    p.setBrush(QBrush(QColor(255, 150, 170, 120)))
    p.drawEllipse(QRectF(s*0.18, s*0.55, s*0.14, s*0.10))
    p.drawEllipse(QRectF(s*0.68, s*0.55, s*0.14, s*0.10))

    p.end()
    return QIcon(pix)


def load_app_icon() -> QIcon:
    """优先用打包内置的 icon.ico(粉猪图片)作为程序图标，找不到再退回代码绘制的图标。"""
    path = resource_path("icon.ico")
    if os.path.exists(path):
        icon = QIcon(path)
        if not icon.isNull():
            return icon
    return create_pig_icon(64)


# ========== 后台线程 ==========
class DetectThread(QThread):
    result = pyqtSignal(str, bool, str)
    finished_all = pyqtSignal()

    def __init__(self, items):
        super().__init__()
        self.items = items

    def run(self):
        for item in self.items:
            ok, ver = detect(item)
            self.result.emit(item["key"], ok, ver)
        self.finished_all.emit()


class InstallThread(QThread):
    log = pyqtSignal(str)
    item_start = pyqtSignal(str)
    item_done = pyqtSignal(str, bool)
    finished_all = pyqtSignal()

    def __init__(self, items):
        super().__init__()
        self.items = items

    def run(self):
        for item in self.items:
            self.item_start.emit(item["key"])
            self.log.emit(f"\n=== 开始处理: {item['name']} ===")
            success = False
            for line in install_stream(item["install"]):
                self.log.emit(line)
                if line.startswith("[完成]"):
                    success = True
            self.item_done.emit(item["key"], success)
        self.finished_all.emit()


class PrecheckThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)  # winget_ok, winget_version

    def run(self):
        ok, ver = run_environment_precheck(self.log.emit)
        self.done.emit(ok, ver)


class DiagnosticsThread(QThread):
    result = pyqtSignal(str, object)

    def run(self):
        text = run_environment_diagnostics()
        try:
            issues = scan_repairable_issues()
        except Exception:  # noqa: BLE001 - 扫描失败不应中断诊断结果展示
            issues = []
        self.result.emit(text, issues)


class RepairThread(QThread):
    result = pyqtSignal(str)

    def __init__(self, keys, parent=None):
        super().__init__(parent)
        self.keys = keys

    def run(self):
        self.result.emit(run_selected_repairs(self.keys))


# ========== 单个软件行 ==========
class SoftwareRow(QWidget):
    def __init__(self, item: dict, show_desc: bool = False):
        super().__init__()
        self.item = item
        self.key = item["key"]
        self.installed = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)

        self.checkbox = QCheckBox(item["name"])
        self.checkbox.setChecked(True)
        layout.addWidget(self.checkbox)

        if show_desc and item.get("desc"):
            desc = QLabel(item["desc"])
            desc.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
            layout.addWidget(desc)

        layout.addStretch()

        self.status = QLabel("检测中...")
        self.status.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 12px;")
        layout.addWidget(self.status)

    def set_status(self, installed: bool, version: str):
        self.installed = installed
        if installed:
            text = f"已安装: v{version}" if version else "已安装"
            self.status.setStyleSheet(f"color: {COLOR_OK}; font-size: 12px; font-weight: bold;")
            # 已安装的默认不勾选(跳过)
            self.checkbox.setChecked(False)
        else:
            text = "未安装"
            self.status.setStyleSheet(f"color: {COLOR_WARN}; font-size: 12px; font-weight: bold;")
            self.checkbox.setChecked(True)
        self.status.setText(text)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()


# ========== 关于对话框 ==========
class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setFixedSize(360, 480)
        self.setStyleSheet(f"""
            QDialog {{ background: {COLOR_BG}; }}
            QLabel {{ color: {COLOR_TEXT}; background: transparent; }}
            QPushButton {{
                background: {COLOR_PRIMARY}; color: white;
                border: none; border-radius: 8px;
                padding: 8px 24px; font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {COLOR_PRIMARY_HOVER}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        # 图标 + 标题
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(create_pig_icon(48).pixmap(48, 48))
        head.addWidget(icon)
        title = QLabel(APP_NAME)
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {COLOR_TEXT};")
        head.addWidget(title)
        head.addStretch()
        layout.addLayout(head)

        # 版本与作者
        info = QLabel(
            f"<p style='margin:4px 0;'>版本:{APP_VERSION}</p>"
            f"<p style='margin:4px 0;'>作者:paimingqian10</p>"
            f"<p style='margin:4px 0;color:{COLOR_TEXT_DIM};'>一个干净的 Windows 开发环境一键配置工具</p>"
        )
        info.setStyleSheet("font-size: 13px;")
        layout.addWidget(info)

        # 二维码
        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_path = resource_path("111.png")
        if os.path.exists(qr_path):
            pix = QPixmap(qr_path)
            if not pix.isNull():
                pix = pix.scaled(220, 220, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                qr_label.setPixmap(pix)
        qr_label.setStyleSheet(
            f"background: white; border: 1.5px solid {COLOR_BORDER}; border-radius: 8px; padding: 8px;"
        )
        layout.addWidget(qr_label, alignment=Qt.AlignmentFlag.AlignCenter)

        tip = QLabel("扫一扫,联系作者")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        layout.addWidget(tip)

        layout.addStretch()

        # 关闭按钮
        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)


# ========== 修复项勾选对话框 ==========
class RepairSelectionDialog(QDialog):
    def __init__(self, issues: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择要修复的冲突项")
        self.setMinimumWidth(560)
        self.setMinimumHeight(360)
        self._checks: list[tuple[QCheckBox, str]] = []

        layout = QVBoxLayout(self)
        tip = QLabel(
            f"检测到 {len(issues)} 个可自动修复的冲突项，请勾选需要修复的项目，"
            "然后点击“修复所选”。有效 API Key 与代理变量不会被处理。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(4, 4, 4, 4)
        for issue in issues:
            cb = QCheckBox(issue.get("label", issue.get("key", "")))
            cb.setChecked(True)
            box.addWidget(cb)
            self._checks.append((cb, issue.get("key", "")))
        box.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        sel_row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_none = QPushButton("全不选")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch(1)
        layout.addLayout(sel_row)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_fix = QPushButton("修复所选")
        btn_fix.setDefault(True)
        btn_cancel.clicked.connect(self.reject)
        btn_fix.clicked.connect(self.accept)
        action_row.addWidget(btn_cancel)
        action_row.addWidget(btn_fix)
        layout.addLayout(action_row)

    def _set_all(self, checked: bool):
        for cb, _ in self._checks:
            cb.setChecked(checked)

    def selected_keys(self) -> list[str]:
        return [key for cb, key in self._checks if cb.isChecked() and key]


# ========== 主窗口 ==========
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowIcon(load_app_icon())
        self.resize(1000, 760)
        self.rows: dict[str, SoftwareRow] = {}
        self.winget_ok = False
        self.winget_message = ""
        self._repair_issues: list[dict] = []

        self._build_ui()
        self._apply_style()

        self.log("正在扫描当前系统环境...")
        self._start_precheck()
        self._start_detect()

        # 窗口渲染后弹出使用顺序引导
        QTimer.singleShot(0, self._show_startup_guide)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ----- 顶部标题栏 -----
        header = QHBoxLayout()

        # 图标
        icon_label = QLabel()
        icon_label.setPixmap(create_pig_icon(40).pixmap(40, 40))
        header.addWidget(icon_label)

        title = QLabel(APP_NAME)
        title.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {COLOR_TEXT};")
        header.addWidget(title)

        self.admin_badge = QLabel("● 管理员权限检测中")
        self.admin_badge.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 12px; margin-left: 12px;")
        header.addWidget(self.admin_badge)
        header.addStretch()

        btn_about = QPushButton("ℹ  关于")
        btn_about.clicked.connect(self._show_about)
        header.addWidget(btn_about)

        root.addLayout(header)

        # ----- Tabs -----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_tab_diagnostics(), "  环境冲突检测  ")
        self.tabs.addTab(self._build_tab1(), "  通用工具与程序  ")
        self.tabs.addTab(self._build_tab_ai(), "  AI 助手一键配置  ")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, stretch=3)

        # ----- 底部安装按钮 -----
        self.btn_install = QPushButton("🐷  开始一键配置 - 通用工具")
        self.btn_install.setMinimumHeight(44)
        self.btn_install.clicked.connect(self._start_install)
        root.addWidget(self.btn_install)

        # ----- 日志框 -----
        log_label = QLabel("实时安装日志 (Real-time Logs)")
        log_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 12px; margin-top: 4px;")
        root.addWidget(log_label)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFont(QFont("Consolas", 9))
        self.log_box.setMinimumHeight(160)
        root.addWidget(self.log_box, stretch=2)

        # 初始停在“环境冲突检测”页(index 0)，同步底部安装按钮的显隐
        self._on_tab_changed(self.tabs.currentIndex())

    def _build_tab1(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(14)

        left_box = QGroupBox("一键配置清单")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(10, 12, 10, 10)
        left_layout.setSpacing(10)

        core_box = QGroupBox("核心环境 (Core Stack)")
        core_layout = QVBoxLayout(core_box)
        for item in CORE_STACK:
            row = SoftwareRow(item)
            self.rows[item["key"]] = row
            core_layout.addWidget(row)
        core_layout.addStretch()
        left_layout.addWidget(core_box)

        ext_box = QGroupBox("可选应用 (External Tools)")
        ext_layout = QVBoxLayout(ext_box)
        for item in EXTERNAL_TOOLS:
            row = SoftwareRow(item, show_desc=True)
            self.rows[item["key"]] = row
            ext_layout.addWidget(row)
        ext_layout.addStretch()
        left_layout.addWidget(ext_box)
        left_layout.addStretch()

        layout.addWidget(left_box, stretch=3)

        guide_box = QGroupBox("工具使用简介 / 作者联系方式")
        guide_layout = QVBoxLayout(guide_box)
        guide_layout.setContentsMargins(16, 18, 16, 16)
        guide_layout.setSpacing(12)
        guide_box.setMinimumWidth(420)

        guide_title = QLabel("小傻瓜环境配置")
        guide_title.setStyleSheet(f"font-size: 17px; font-weight: bold; color: {COLOR_PRIMARY_HOVER};")
        guide_layout.addWidget(guide_title)

        guide = QLabel(
            "使用方法：\n"
            "1. 勾选左侧需要安装或更新的项目。\n"
            "2. 点击下方“一键配置 - 通用工具”。\n"
            "3. 安装完成后关闭旧终端，重新打开 PowerShell。\n"
            "4. 进入“AI 助手一键配置”填写 Claude Code、Codex 或 Claude 桌面版配置。\n"
            "5. 如果出现配置失败、模型错乱或 Key 污染，进入“环境冲突检测”先检测，再一键修复。\n\n"
            "注意事项：\n"
            "• 建议右键以管理员身份运行本工具。\n"
            "• 已安装项目默认取消勾选，避免重复安装。\n"
            "• Codex 和 Claude 桌面版安装器已内置在本程序中。\n\n"
            "作者联系方式：QQ 3995697915"
        )
        guide.setWordWrap(True)
        guide.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px; line-height: 1.55;")
        guide_layout.addWidget(guide)
        guide_layout.addStretch()

        layout.addWidget(guide_box, stretch=4)

        return tab

    def _build_tab_diagnostics(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(10)

        top_box = QGroupBox("配置失败 / Key 污染 / 模型错乱检测")
        top_layout = QVBoxLayout(top_box)

        desc = QLabel(
            "只读检测 Claude Code 与 Codex 常见冲突：API Key 环境变量污染、代理变量、"
            "多安装来源、配置文件异常、运行中进程、Codex 会话/沙箱残留。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {COLOR_TEXT}; font-size: 12px;")
        top_layout.addWidget(desc)

        btns = QHBoxLayout()
        self.btn_run_diag = QPushButton("🩺  开始检测环境冲突")
        self.btn_run_diag.clicked.connect(self._run_environment_diagnostics)
        btns.addWidget(self.btn_run_diag)
        self.btn_repair_diag = QPushButton("🛠  一键修复检测到的问题")
        self.btn_repair_diag.clicked.connect(self._repair_environment_conflicts)
        btns.addWidget(self.btn_repair_diag)
        btns.addStretch()
        top_layout.addLayout(btns)

        layout.addWidget(top_box)

        self.diag_box = QTextEdit()
        self.diag_box.setReadOnly(True)
        self.diag_box.setFont(QFont("Consolas", 9))
        self.diag_box.setMinimumHeight(300)
        self.diag_box.setText("点击“开始检测环境冲突”后，会在这里显示诊断报告。")
        layout.addWidget(self.diag_box, stretch=1)

        return tab

    def _build_tab_ai(self) -> QWidget:
        tab = QWidget()
        outer = QHBoxLayout(tab)
        outer.setContentsMargins(8, 12, 8, 8)
        outer.setSpacing(12)

        # ----- 左:AI 列表 -----
        left_box = QGroupBox("AI 编码助手")
        left_layout = QVBoxLayout(left_box)
        self.ai_list = QListWidget()
        self.ai_list.setSpacing(4)
        for item in AI_TOOLS:
            li = QListWidgetItem(f"{item['name']}\n{item.get('desc','')}")
            li.setData(Qt.ItemDataRole.UserRole, item["key"])
            self.ai_list.addItem(li)
            # 用一个隐藏的 SoftwareRow 跟踪安装状态,保持与现有检测/安装回调兼容
            row = SoftwareRow(item, show_desc=True)
            row.hide()
            self.rows[item["key"]] = row
        self.ai_list.currentRowChanged.connect(self._on_ai_selected)
        left_layout.addWidget(self.ai_list)

        self.btn_install_all_ai = QPushButton("🪄  一键安装全部 AI 助手")
        self.btn_install_all_ai.clicked.connect(self._install_all_ai)
        left_layout.addWidget(self.btn_install_all_ai)
        outer.addWidget(left_box, stretch=1)

        # ----- 右:配置面板 -----
        right_box = QGroupBox("配置 (Core)")
        right_layout = QVBoxLayout(right_box)

        # 顶部按钮:备份 / 恢复
        top_btns = QHBoxLayout()
        top_btns.addStretch()
        btn_backup = QPushButton("📁  备份配置")
        btn_backup.clicked.connect(self._backup_config)
        btn_restore = QPushButton("↻  恢复备份")
        btn_restore.clicked.connect(self._restore_config)
        top_btns.addWidget(btn_backup)
        top_btns.addWidget(btn_restore)
        right_layout.addLayout(top_btns)

        # 表单
        self.ai_form_title = QLabel("")
        self.ai_form_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLOR_PRIMARY_HOVER};")
        right_layout.addWidget(self.ai_form_title)

        form = QFormLayout()
        form.setSpacing(10)
        self.ai_status_label = QLabel("-")
        self.ai_status_label.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 12px;")
        self.ed_api_key = QLineEdit()
        self.ed_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_base_url = QLineEdit()
        self.ed_model = QLineEdit()
        self.ed_timeout = QLineEdit()
        form.addRow("安装状态", self.ai_status_label)
        form.addRow("API Key", self.ed_api_key)
        form.addRow("API 地址", self.ed_base_url)
        form.addRow("默认模型", self.ed_model)
        form.addRow("超时等待 (ms)", self.ed_timeout)
        right_layout.addLayout(form)

        # 底部按钮
        bottom_btns = QHBoxLayout()
        self.btn_save_ai = QPushButton("💾  保存当前配置")
        self.btn_save_ai.clicked.connect(self._save_ai_config)
        self.btn_install_ai = QPushButton("⬇  安装/更新")
        self.btn_install_ai.clicked.connect(self._install_current_ai)
        self.btn_launch_ai = QPushButton("🚀  快速启动")
        self.btn_launch_ai.clicked.connect(self._launch_current_ai)
        self.btn_skip_login = QPushButton("🔓  跳过登录")
        self.btn_skip_login.clicked.connect(self._skip_login_current_ai)
        bottom_btns.addWidget(self.btn_save_ai)
        bottom_btns.addWidget(self.btn_install_ai)
        bottom_btns.addWidget(self.btn_launch_ai)
        bottom_btns.addWidget(self.btn_skip_login)
        right_layout.addLayout(bottom_btns)

        right_layout.addStretch()

        tip = QLabel("提示:Claude Code 通过 npm 全局安装,桌面端使用内置安装器。配置保存到 %APPDATA%/小傻瓜环境配置/。")
        tip.setStyleSheet(f"color: {COLOR_TEXT_DIM}; font-size: 11px;")
        tip.setWordWrap(True)
        right_layout.addWidget(tip)

        outer.addWidget(right_box, stretch=2)

        # 默认选中第一个
        self.ai_list.setCurrentRow(0)
        return tab

    def _current_ai_item(self) -> dict | None:
        row = self.ai_list.currentRow()
        if 0 <= row < len(AI_TOOLS):
            return AI_TOOLS[row]
        return None

    def _on_ai_selected(self, row: int):
        item = self._current_ai_item()
        if not item:
            return
        self.ai_form_title.setText(f"{item['name']} 配置")
        cfg = ai_config.get_config(item["key"], item.get("defaults", {}))
        self.ed_api_key.setText(cfg.get("api_key", ""))
        self.ed_base_url.setText(cfg.get("base_url", ""))
        self.ed_model.setText(cfg.get("model", ""))
        self.ed_timeout.setText(cfg.get("timeout", ""))

        # 隐藏对应 AI 不支持的字段(如 timeout)
        env_map = item.get("env_map", {})
        self.ed_timeout.setEnabled(bool(env_map.get("timeout")))

        # Claude Code 和 Claude 桌面版都支持"跳过登录"
        self.btn_skip_login.setVisible(item.get("key") in ("claude_code", "claude_desktop_config"))

        # 状态
        srow = self.rows.get(item["key"])
        if srow and srow.installed:
            self.ai_status_label.setText("已安装 ✔")
            self.ai_status_label.setStyleSheet(f"color: {COLOR_OK}; font-size: 12px; font-weight: bold;")
        else:
            self.ai_status_label.setText("未安装")
            self.ai_status_label.setStyleSheet(f"color: {COLOR_WARN}; font-size: 12px; font-weight: bold;")

    def _collect_form(self) -> dict:
        return {
            "api_key": self.ed_api_key.text().strip(),
            "base_url": self.ed_base_url.text().strip(),
            "model": self.ed_model.text().strip(),
            "timeout": self.ed_timeout.text().strip(),
        }

    def _save_ai_config(self):
        item = self._current_ai_item()
        if not item:
            return
        cfg = self._collect_form()
        ai_config.set_config(item["key"], cfg)
        self.log(f"[配置] {item['name']} 已保存")
        if item.get("key") == "claude_code":
            try:
                actions = ai_config.apply_claude_code_config(cfg)
                for act in actions:
                    self.log(f"[强制配置] {act}")
                QMessageBox.information(
                    self, "已强制配置",
                    "已将 Claude Code 配置强制写入,确保不被其它软件覆盖:\n\n"
                    + "\n".join("· " + a for a in actions)
                    + "\n\n请重新打开终端 / Claude Code 使其生效。",
                )
            except ValueError as e:
                QMessageBox.warning(self, "缺少信息", str(e))
            except OSError as e:
                QMessageBox.warning(self, "配置失败", str(e))
            return
        QMessageBox.information(self, "已保存", f"{item['name']} 配置已保存")

    def _install_current_ai(self):
        item = self._current_ai_item()
        if not item:
            return
        self._run_install([item])

    def _install_all_ai(self):
        self._run_install(list(AI_TOOLS))

    def _run_install(self, items: list):
        if not items:
            return
        self.btn_install.setEnabled(False)
        self.btn_install_ai.setEnabled(False)
        self.btn_install_all_ai.setEnabled(False)
        self.log(f"\n======= 开始安装 {len(items)} 项 =======")
        self.install_thread = InstallThread(items)
        self.install_thread.log.connect(self.log)
        self.install_thread.item_done.connect(self._on_install_done)
        self.install_thread.finished_all.connect(self._on_ai_install_all_done)
        self.install_thread.start()

    def _on_ai_install_all_done(self):
        self.btn_install.setEnabled(True)
        self.btn_install_ai.setEnabled(True)
        self.btn_install_all_ai.setEnabled(True)
        self.log("\n======= 全部处理完成 =======")
        # 刷新右侧状态
        self._on_ai_selected(self.ai_list.currentRow())

    def _skip_login_current_ai(self):
        item = self._current_ai_item()
        if not item:
            return
        key = item.get("key")
        if key == "claude_code":
            try:
                path = ai_config.bypass_claude_onboarding()
                self.log(f"[跳过登录] 已写入 hasCompletedOnboarding=true 到 {path}")
                QMessageBox.information(
                    self, "已跳过登录",
                    f"已在 {path} 中设置 hasCompletedOnboarding=true。\n"
                    f"下次启动 Claude Code 将不再要求登录引导。",
                )
            except OSError as e:
                QMessageBox.warning(self, "操作失败", str(e))
        elif key == "claude_desktop_config":
            cfg = self._collect_form()
            try:
                path = ai_config.bypass_claude_desktop_login(
                    cfg.get("base_url", ""), cfg.get("api_key", "")
                )
                ai_config.set_config(key, cfg)  # 顺手保存当前填写
                self.log(f"[跳过登录] 已写入 Claude 桌面版 Gateway 托管配置到 {path}")
                QMessageBox.information(
                    self, "已跳过登录",
                    "已为 Claude 桌面版写入开发者模式 Gateway 托管配置：\n"
                    f"{path}\n\n"
                    "（Connection=Gateway，已勾选 Skip login-mode chooser，"
                    "启动直接进入第三方推理模式，跳过登录。）\n\n"
                    "请完全退出 Claude 桌面版（右键托盘图标 → 退出）后重新打开生效。\n"
                    "如需恢复官方登录，删除上述注册表项即可。",
                )
            except ValueError as e:
                QMessageBox.warning(self, "缺少信息", str(e))
            except OSError as e:
                QMessageBox.warning(self, "操作失败", str(e))

    def _launch_current_ai(self):
        item = self._current_ai_item()
        if not item:
            return
        srow = self.rows.get(item["key"])
        if not srow or not srow.installed:
            ret = QMessageBox.question(
                self, "未安装",
                f"{item['name']} 似乎未安装,是否先安装?",
            )
            if ret == QMessageBox.StandardButton.Yes:
                self._install_current_ai()
            return
        cfg = self._collect_form()
        # 顺手保存
        ai_config.set_config(item["key"], cfg)
        if item.get("key") == "claude_code":
            try:
                for act in ai_config.apply_claude_code_config(cfg):
                    self.log(f"[强制配置] {act}")
            except (ValueError, OSError) as e:
                self.log(f"[强制配置] 已跳过: {e}")
        try:
            launch_target = item.get("launch") or item.get("launch_cmd")
            ai_config.launch_ai(launch_target, item.get("env_map", {}), cfg)
            self.log(f"[启动] 已在新终端启动 {item['name']}")
        except OSError as e:
            QMessageBox.warning(self, "启动失败", str(e))

    def _backup_config(self):
        try:
            path = ai_config.backup_config()
            self.log(f"[备份] 已保存到 {path}")
            QMessageBox.information(self, "备份成功", f"配置已备份到:\n{path}")
        except FileNotFoundError as e:
            QMessageBox.warning(self, "无法备份", str(e))

    def _restore_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择备份文件", "", "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            ai_config.restore_config(path)
            self.log(f"[恢复] 已从 {path} 恢复配置")
            self._on_ai_selected(self.ai_list.currentRow())
            QMessageBox.information(self, "恢复成功", "配置已恢复")
        except OSError as e:
            QMessageBox.warning(self, "恢复失败", str(e))

    # ========== 逻辑 ==========
    def log(self, msg: str):
        self.log_box.append(msg)

    def _start_precheck(self):
        self.precheck_thread = PrecheckThread()
        self.precheck_thread.log.connect(self.log)
        self.precheck_thread.done.connect(self._on_precheck_done)
        self.precheck_thread.start()

    def _on_precheck_done(self, ok: bool, ver: str):
        self.winget_ok = ok
        self.winget_message = ver
        if not ok:
            self.log("[提示] winget 仍不可用，依赖 winget 的项目可在“环境冲突检测”里点击“一键修复”。")

    def _start_detect(self):
        all_items = list(CORE_STACK) + list(EXTERNAL_TOOLS) + list(AI_TOOLS)
        self.detect_thread = DetectThread(all_items)
        self.detect_thread.result.connect(self._on_detect_one)
        self.detect_thread.finished_all.connect(lambda: self.log("扫描完成。"))
        self.detect_thread.start()

    def _on_detect_one(self, key: str, ok: bool, ver: str):
        if key in self.rows:
            self.rows[key].set_status(ok, ver)
        # 若检测的是当前 AI Tab 选中项,刷新状态显示
        cur = self._current_ai_item() if hasattr(self, "ai_list") else None
        if cur and cur["key"] == key:
            self._on_ai_selected(self.ai_list.currentRow())

    def _on_tab_changed(self, idx: int):
        if idx == 1:
            # “通用工具与程序”页才显示底部一键安装按钮
            self.btn_install.setText("🐷  开始一键配置 - 通用工具")
            self.btn_install.show()
        else:
            # 诊断 / AI Tab 有自己的按钮,隐藏底部主按钮
            self.btn_install.hide()

    def _current_items(self) -> list:
        if self.tabs.currentIndex() == 1:
            return list(CORE_STACK) + list(EXTERNAL_TOOLS)
        return list(AI_TOOLS)

    def _start_install(self):
        items = self._current_items()
        selected = [it for it in items if self.rows[it["key"]].is_checked()]
        if not selected:
            QMessageBox.information(self, "提示", "请至少勾选一个软件。")
            return

        winget_items = [it for it in selected if it.get("install", {}).get("type") == "winget"]
        if winget_items and not self.winget_ok:
            names = "、".join(it["name"] for it in winget_items)
            QMessageBox.warning(
                self,
                "缺少 winget，无法继续",
                "当前电脑没有检测到 winget，所以不能一键安装这些项目：\n\n"
                f"{names}\n\n"
                "winget 由 Microsoft App Installer 提供。请按下面步骤修复：\n"
                "1. 切换到“环境冲突检测”。\n"
                "2. 点击“一键修复检测到的问题”。\n"
                "3. 工具会先把当前用户 WindowsApps 补进 PATH；如果仍没有 winget，会走无商店安装，"
                "从微软 GitHub 官方发布页下载依赖包和 App Installer。\n\n"
                "手动修复也可以打开 Microsoft Store，搜索“应用安装程序”或“App Installer”；没有商店的系统请使用一键修复的无商店安装。\n\n"
                "如果这台电脑无法使用 Microsoft Store 或企业策略禁用 App Installer，请先手动安装上述软件，再回到本工具做检测/配置。"
            )
            self.log("[中止] 缺少 winget，已阻止继续执行 winget 安装命令，避免刷屏失败。")
            return

        # 提示已安装的会跳过(实际上 winget install 已装的会直接跳过)
        self.btn_install.setEnabled(False)
        original_text = self.btn_install.text()
        self.btn_install.setText("正在安装 ...")
        self.log(f"\n======= 开始安装 {len(selected)} 项 =======")

        self.install_thread = InstallThread(selected)
        self.install_thread.log.connect(self.log)
        self.install_thread.item_done.connect(self._on_install_done)
        self.install_thread.finished_all.connect(
            lambda: self._on_install_all_done(original_text)
        )
        self.install_thread.start()

    def _on_install_done(self, key: str, success: bool):
        all_items = list(CORE_STACK) + list(EXTERNAL_TOOLS) + list(AI_TOOLS)
        item = next((it for it in all_items if it["key"] == key), None)
        if item:
            ok, ver = detect(item)
            self.rows[key].set_status(ok, ver)

    def _on_install_all_done(self, original_text: str):
        self.btn_install.setEnabled(True)
        self.btn_install.setText(original_text)
        self.log("\n======= 全部处理完成 =======")

    def _show_startup_guide(self):
        QMessageBox.information(
            self,
            "使用顺序提示",
            "建议按以下顺序使用本工具：\n\n"
            "第 1 步：先到「环境冲突检测」检测并修复 Key 污染、模型错乱等冲突。\n\n"
            "第 2 步：再到「通用工具与程序」安装依赖环境（Node.js、Git 等）。\n\n"
            "第 3 步：最后到「AI 助手一键配置」填写并配置 Claude Code、Codex 等。\n\n"
            "按这个顺序使用，能最大程度避免配置失败。",
        )

    def _show_about(self):
        AboutDialog(self).exec()

    def _run_environment_diagnostics(self):
        self.btn_run_diag.setEnabled(False)
        self.btn_repair_diag.setEnabled(False)
        self.diag_box.setText("正在检测环境冲突，请稍候 ...")
        self.diag_thread = DiagnosticsThread()
        self.diag_thread.result.connect(self._on_environment_diagnostics_done)
        self.diag_thread.start()

    def _on_environment_diagnostics_done(self, text: str, issues: list):
        self.diag_box.setText(text)
        self.btn_run_diag.setEnabled(True)
        self.btn_repair_diag.setEnabled(True)
        self._repair_issues = list(issues or [])
        self.log("[诊断] 环境冲突检测完成")
        if self._repair_issues:
            self.log(f"[诊断] 发现 {len(self._repair_issues)} 个可修复项，请在弹窗中勾选")
            self._open_repair_selection_dialog()
        else:
            self.log("[诊断] 未发现可自动修复的冲突项")

    def _repair_environment_conflicts(self):
        if not self._repair_issues:
            QMessageBox.information(
                self,
                "暂无可修复项",
                "尚未检测到可自动修复的冲突项。请先点击“开始检测环境冲突”，"
                "检测完成后会弹出可勾选的修复列表。",
            )
            return
        self._open_repair_selection_dialog()

    def _open_repair_selection_dialog(self):
        dialog = RepairSelectionDialog(self._repair_issues, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        keys = dialog.selected_keys()
        if not keys:
            QMessageBox.information(self, "未选择项目", "没有勾选任何修复项，已取消。")
            return
        self.btn_run_diag.setEnabled(False)
        self.btn_repair_diag.setEnabled(False)
        self.diag_box.setText("正在修复所选环境冲突，请稍候 ...")
        self.repair_thread = RepairThread(keys)
        self.repair_thread.result.connect(self._on_environment_repair_done)
        self.repair_thread.start()

    def _on_environment_repair_done(self, text: str):
        self.diag_box.setText(text)
        self.btn_run_diag.setEnabled(True)
        self.btn_repair_diag.setEnabled(True)
        self._repair_issues = []
        self.log("[诊断] 环境冲突修复完成，如需再次确认请重新检测")

    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {COLOR_BG};
                color: {COLOR_TEXT};
                font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif;
            }}
            QGroupBox {{
                background-color: {COLOR_CARD};
                border: 1.5px solid {COLOR_BORDER};
                border-radius: 12px;
                margin-top: 14px;
                padding-top: 14px;
                font-size: 13px;
                font-weight: bold;
                color: {COLOR_PRIMARY_HOVER};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                background-color: {COLOR_CARD};
            }}
            QTabWidget::pane {{
                border: 1.5px solid {COLOR_BORDER};
                border-radius: 10px;
                background: {COLOR_CARD};
                top: -1px;
            }}
            QTabBar::tab {{
                background: {COLOR_TAB_BG};
                color: {COLOR_TEXT};
                padding: 9px 22px;
                border: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 4px;
                font-size: 13px;
            }}
            QTabBar::tab:selected {{
                background: {COLOR_PRIMARY};
                color: white;
                font-weight: bold;
            }}
            QPushButton {{
                background: {COLOR_CARD};
                color: {COLOR_TEXT};
                border: 1.5px solid {COLOR_BORDER};
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {COLOR_TAB_BG};
                border-color: {COLOR_PRIMARY};
            }}
            QTextEdit {{
                background: {COLOR_LOG_BG};
                color: {COLOR_TEXT};
                border: 1.5px solid {COLOR_BORDER};
                border-radius: 8px;
                padding: 6px;
            }}
            QCheckBox {{
                color: {COLOR_TEXT};
                spacing: 8px;
                font-size: 13px;
            }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 1.5px solid {COLOR_BORDER};
                border-radius: 4px;
                background: {COLOR_CARD};
            }}
            QCheckBox::indicator:checked {{
                background: {COLOR_PRIMARY};
                border-color: {COLOR_PRIMARY};
            }}
            QLabel {{ background: transparent; }}
        """)
        # 主安装按钮单独样式
        self.btn_install.setStyleSheet(f"""
            QPushButton {{
                background: {COLOR_PRIMARY};
                color: white;
                font-size: 15px;
                font-weight: bold;
                border: none;
                border-radius: 12px;
            }}
            QPushButton:hover {{ background: {COLOR_PRIMARY_HOVER}; }}
            QPushButton:disabled {{ background: #F4C2D2; color: #fff; }}
        """)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(load_app_icon())
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
