# -*- coding: utf-8 -*-
"""
PyQt5 UWB TWR location viewer.

This file is intentionally kept small after the refactor. It owns only the
application shell: Windows process setup, icon/resource handling, main-window
composition, and the GUI entry point. Feature-specific behavior lives in the
mixin modules:

- twr_ui_layout.py: window layout, adaptive sizing, communication controls.
- twr_plot.py: main positioning canvas, zoom/pan, anchor/tag drawing.
- twr_debug_panel.py: raw log, packet parse panel, distance visualization.
- twr_comm.py: TCP server and serial reader services.
"""
import ctypes
import os
import sys

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import QApplication, QMessageBox

import globalvar
from mainwindow import Ui_MainWindow
from twr_config import CONFIG_VERSION, load_config, save_config, serialize_anchors
from twr_comm import SERIAL_SERVER, TCP_SERVER
from twr_debug_panel import DebugPanelMixin
import twr_plot as twr_plot_module
from twr_plot import PlotMixin
import twr_ui_layout as twr_ui_layout_module
from twr_ui_layout import UILayoutMixin
from uwb_logging import configure_logging, get_logger


gAnthor_Node_Configure = globalvar.get_anthor()
logger = get_logger(__name__)
APP_ID = "landian.uwb.twr.host.v1"
APP_ICON_FILE = "uwb_location.ico"
_GUI_STDIO_STREAMS = []


def resource_path(filename):
    """Resolve a resource path relative to the application directory."""
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, filename)


def set_windows_app_id():
    """Set Windows AppUserModelID so taskbar icon grouping is correct."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def prepare_windows_gui_process():
    """Detach the console and silence stdio for GUI-style Windows startup."""
    if sys.platform != "win32":
        return

    try:
        stdout_sink = open(os.devnull, "w")
        stderr_sink = open(os.devnull, "w")
        _GUI_STDIO_STREAMS.extend([stdout_sink, stderr_sink])
        sys.stdout = stdout_sink
        sys.stderr = stderr_sink
    except OSError:
        pass

    try:
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def get_app_icon():
    """Load the preferred application icon with a PNG fallback."""
    icon_path = resource_path(APP_ICON_FILE)
    if not os.path.exists(icon_path):
        icon_path = resource_path("Pngtree.png")
    return QIcon(icon_path)


class HuiTu(UILayoutMixin, DebugPanelMixin, PlotMixin, QtWidgets.QMainWindow, Ui_MainWindow):
    """Main window assembled from focused UI, plotting, debug, and service mixins."""

    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.persistent_config = load_config()
        self.preferred_com_port = self.persistent_config["communication"]["com_port"]
        self.preferred_com_baud = self.persistent_config["communication"]["baudrate"]
        self.preferred_comm_tab = self.persistent_config["communication"]["default_tab"]
        self.saved_zoom_factor = self.persistent_config["display"]["zoom_factor"]
        self.apply_loaded_anchor_config()

        # Build static UI first. The mixin computes an adaptive window size
        # before any fixed-looking controls are created, so different screen
        # resolutions keep the same relative layout without clipping.
        self.apply_localized_texts()
        self.zoom_factor = 1.0
        self.zoom_min = 0.35
        self.zoom_max = 4.0
        self.zoom_step = 1.2
        self.view_angle_deg = self.persistent_config["display"]["view_angle_deg"]
        self.configure_window_layout()

        self.setWindowIcon(get_app_icon())

        self.graphicsView.setStyleSheet("padding: 0px; border: 0px;")
        self.graphicsView.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.graphicsView.setSceneRect(
            0,
            0,
            self.graphicsView.viewport().width(),
            self.graphicsView.height(),
        )
        self.scene = QtWidgets.QGraphicsScene(self)
        self.graphicsView.setScene(self.scene)
        self.graphicsView.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
        self.graphicsView.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.graphicsView.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.graphicsView.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.graphicsView.viewport().installEventFilter(self)

        self.label_ip.setText(self.get_local_ip())
        self.ratio = 100
        self.plot_origin_x = 0
        self.plot_origin_y = 0
        self.plot_padding = 96
        self.scene_center_x = 0
        self.scene_center_y = 0
        self.display_mode = "2D"
        self.view_angle_deg = self.persistent_config["display"]["view_angle_deg"]
        self.z_scale = 0.65
        self.origin_margin_x = 80
        self.origin_margin_y = 120
        self.anchor_marker_size = 19
        self.tag_marker_size = 16
        self.scene_label_font = QtGui.QFont("Microsoft YaHei UI", self.adaptive_px(14, 11))
        self.scene_label_font.setBold(True)
        self.MAX_HISTORY = self.persistent_config["display"]["history_count"]
        self.gTag_Result = []
        self.measurement_overlay_items = []
        self.measurement_overlay_by_tag = {}
        self.measurement_aid_enabled = self.persistent_config["display"]["measurement_aid_enabled"]
        self.last_location_overlay_refresh = 0
        self.location_overlay_interval = 0.04
        self.debug_parse_enabled = False
        self.debug_parse_data = {}
        self.debug_parse_ttl_seconds = 120.0
        self.max_debug_tags = 64
        self.debug_distance_history = {}
        self.debug_distance_history_seconds = 20.0
        self.file_log_enabled = False
        self.file_log_path = ""
        self.gQtColor = [
            QColor("#D97706"),
            QColor("#BE123C"),
            QColor("#7C3AED"),
            QColor("#15803D"),
            QColor("#C2410C"),
            QColor("#A21CAF"),
            QColor("#4D7C0F"),
            QColor("#B91C1C"),
            QColor("#92400E"),
            QColor("#6D28D9"),
        ]

        # Communication services run in background threads and communicate
        # with the GUI only through Qt signals. This keeps socket/serial reads
        # away from the GUI thread and prevents high-frequency packets from
        # blocking painting or user input.
        self.tcp_server = TCP_SERVER()
        self.tcp_server.data_result.connect(self.insert_result)
        self.tcp_server.data_draf.connect(self.do_insert_log)
        self.tcp_server.data_parse.connect(self.update_debug_parse_result)
        self.tcp_server.algorithm_status.connect(self.set_algorithm_status)
        self.serial_server = SERIAL_SERVER()
        self.serial_server.data_result.connect(self.insert_result)
        self.serial_server.data_draf.connect(self.do_insert_log)
        self.serial_server.data_parse.connect(self.update_debug_parse_result)
        self.serial_server.status_message.connect(self.on_serial_status_message)
        self.serial_server.algorithm_status.connect(self.set_algorithm_status)

        # Wire user actions after all dynamically-created widgets exist.
        self.pushButton.clicked.connect(self.do_tcp_server_start)
        self.btn_clear.clicked.connect(self.do_btn_clear)
        self.btn_start.clicked.connect(self.do_btn_start_log)
        self.btn_save_log.clicked.connect(self.do_btn_save_log)
        self.btn_parse.clicked.connect(self.do_btn_parse_log)
        self.spinBox.valueChanged.connect(self.do_adjust_maxhistory)
        self.table_anthor.cellChanged.connect(self.do_table_anthor_cellChanged)

        self.label_port_state.setStyleSheet("color: #B42318; font-weight: 600;")
        self.table_anthor.setAlternatingRowColors(True)
        self.table_tag.setAlternatingRowColors(True)
        self.edit_log.setMaximumBlockCount(1000)
        self.enable_log = False
        self.setup_debug_log_flush_timer()
        self.setup_status_bar()
        self.apply_business_theme()
        self.set_comm_status_style(self.label_port_state, "closed")
        self.set_comm_status_style(self.com_status_label, "closed")
        self.apply_loaded_widget_config()

        self.anchor_timer = QtCore.QTimer(self)
        self.anchor_timer.timeout.connect(self.refresh_anthor_status)
        self.anchor_timer.start(2000)
        self.refresh_serial_ports()

    def apply_loaded_anchor_config(self):
        """Apply persisted anchors before layout code sizes the tables."""
        global gAnthor_Node_Configure
        anchors = self.persistent_config["anchors"]
        gAnthor_Node_Configure = anchors
        globalvar.set_anthor(anchors)
        twr_plot_module.gAnthor_Node_Configure = anchors
        twr_ui_layout_module.gAnthor_Node_Configure = anchors

    def apply_loaded_widget_config(self):
        """Restore saved user-editable controls after dynamic widgets exist."""
        comm_config = self.persistent_config["communication"]
        display_config = self.persistent_config["display"]

        self.lineEdit_Port.setText(str(comm_config["tcp_port"]))

        baud_text = str(comm_config["baudrate"])
        baud_index = self.com_baud_combo.findText(baud_text)
        if baud_index < 0:
            self.com_baud_combo.addItem(baud_text)
            baud_index = self.com_baud_combo.findText(baud_text)
        self.com_baud_combo.setCurrentIndex(max(0, baud_index))

        self.spinBox.setValue(display_config["history_count"])
        self.MAX_HISTORY = self.spinBox.value()

        self.measurement_aid_enabled = display_config["measurement_aid_enabled"]
        self.measurement_toggle_button.setText("隐藏测距" if self.measurement_aid_enabled else "显示测距")

        self.view_angle_deg = display_config["view_angle_deg"]
        self.view_angle_slider.blockSignals(True)
        self.view_angle_slider.setValue(self.view_angle_deg)
        self.view_angle_slider.blockSignals(False)
        self.update_view_angle_controls()

        if comm_config["default_tab"] == "TCP":
            self.comm_tab_widget.setCurrentWidget(self.tcp_tab)
        else:
            self.comm_tab_widget.setCurrentWidget(self.com_tab)

    def apply_saved_view_state(self):
        """Restore canvas zoom after the first scene has been drawn."""
        self.set_zoom(self.saved_zoom_factor)
        self.update_zoom_label()

    def build_persistent_config(self):
        """Collect current UI state into the persisted config schema."""
        try:
            tcp_port = int(self.lineEdit_Port.text())
        except ValueError:
            tcp_port = self.persistent_config["communication"]["tcp_port"]

        try:
            baudrate = int(self.com_baud_combo.currentText())
        except ValueError:
            baudrate = self.persistent_config["communication"]["baudrate"]

        com_port = self.com_port_combo.currentData() or self.preferred_com_port or ""
        default_tab = "TCP" if self.comm_tab_widget.currentWidget() is self.tcp_tab else "COM"

        return {
            "version": CONFIG_VERSION,
            "anchor_count": len(globalvar.get_anthor()),
            "anchors": serialize_anchors(globalvar.get_anthor()),
            "communication": {
                "tcp_port": tcp_port,
                "com_port": com_port,
                "baudrate": baudrate,
                "default_tab": default_tab,
            },
            "display": {
                "history_count": self.spinBox.value(),
                "zoom_factor": self.zoom_factor,
                "view_angle_deg": self.view_angle_deg,
                "measurement_aid_enabled": self.measurement_aid_enabled,
            },
        }

    def save_persistent_config(self):
        """Save current user configuration without interrupting app shutdown."""
        try:
            path = save_config(self.build_persistent_config())
            logger.info("Saved user config to %s", path)
        except Exception as exc:
            logger.warning("Failed to save user config: %s", exc)

    def closeEvent(self, event):
        """Stop file logging and communication services before closing the GUI."""
        reply = QMessageBox.question(
            self,
            "本程序",
            "是否要退出程序？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.save_persistent_config()
            self.flush_debug_logs()
            self.stop_file_log()
            self.tcp_server.tcp_close()
            self.serial_server.serial_close()
            event.accept()
        else:
            event.ignore()


def main():
    """Create QApplication, show the main window, and enter the Qt event loop."""
    configure_logging()
    prepare_windows_gui_process()
    set_windows_app_id()
    if QtWidgets.QApplication.instance() is None and hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    if QtWidgets.QApplication.instance() is None and hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("蓝点UWB-TWR 上位机V1.0")
    app.setOrganizationName("蓝点UWB")
    app.setWindowIcon(get_app_icon())
    form = HuiTu()
    form.show_anthor_configure(globalvar.get_anthor())
    form.show()
    app.processEvents()
    form.center_on_screen()
    form.Display_Anthor(globalvar.get_anthor())
    form.apply_saved_view_state()
    auto_tcp_port = os.environ.get("UWB_AUTO_TCP_PORT")
    if auto_tcp_port:
        form.lineEdit_Port.setText(auto_tcp_port)
        QtCore.QTimer.singleShot(500, form.do_tcp_server_start)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
