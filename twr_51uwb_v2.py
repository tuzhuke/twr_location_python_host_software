# -*- coding: utf-8 -*-
"""
PyQt5 UWB TWR location viewer.
"""
import datetime
import ctypes
import ipaddress
import math
import os
import socket
import sys
import time
from threading import Thread

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.Qt import Qt
from PyQt5.QtGui import QBrush, QColor, QIcon, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QMessageBox,
    QTableWidgetItem,
)

import globalvar
from mainwindow import Ui_MainWindow
from twr_main import Process_String_Before_Udp, extract_packets, twr_main

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


gAnthor_Node_Configure = globalvar.get_anthor()
APP_ID = "51uwb.twr.location.tool"
APP_ICON_FILE = "uwb_location.ico"
_GUI_STDIO_STREAMS = []


def resource_path(filename):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def set_windows_app_id():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def prepare_windows_gui_process():
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
    icon_path = resource_path(APP_ICON_FILE)
    if not os.path.exists(icon_path):
        icon_path = resource_path("Pngtree.png")
    return QIcon(icon_path)


class HuiTu(QtWidgets.QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.apply_localized_texts()
        self.zoom_factor = 1.0
        self.zoom_min = 0.35
        self.zoom_max = 4.0
        self.zoom_step = 1.2
        self.view_angle_deg = 35
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
        self.view_angle_deg = 35
        self.z_scale = 0.65
        self.origin_margin_x = 80
        self.origin_margin_y = 120
        self.anchor_marker_size = 19
        self.tag_marker_size = 16
        self.scene_label_font = QtGui.QFont("Microsoft YaHei", 14)
        self.scene_label_font.setBold(True)
        self.MAX_HISTORY = 5
        self.gTag_Result = []
        self.measurement_overlay_items = []
        self.measurement_aid_enabled = True
        self.last_location_overlay_refresh = 0
        self.location_overlay_interval = 0.04
        self.debug_parse_enabled = False
        self.debug_parse_data = {}
        self.file_log_enabled = False
        self.file_log_path = ""
        self.gQtColor = [
            QColor(QtCore.Qt.darkCyan),
            QColor(QtCore.Qt.black),
            QColor(QtCore.Qt.red),
            QColor(QtCore.Qt.darkGreen),
            QColor(QtCore.Qt.darkMagenta),
            QColor(QtCore.Qt.darkRed),
            QColor(QtCore.Qt.gray),
            QColor(QtCore.Qt.green),
            QColor(QtCore.Qt.blue),
            QColor(QtCore.Qt.cyan),
        ]

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
        self.setup_status_bar()
        self.apply_business_theme()
        self.set_comm_status_style(self.label_port_state, "closed")
        self.set_comm_status_style(self.com_status_label, "closed")

        self.anchor_timer = QtCore.QTimer(self)
        self.anchor_timer.timeout.connect(self.refresh_anthor_status)
        self.anchor_timer.start(2000)
        self.refresh_serial_ports()

    def apply_localized_texts(self):
        self.setWindowTitle("51UWB TWR Location Tool V0.8")
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab), "定位")
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab_2), "调试")
        self.btn_start.setText("开始")
        self.btn_clear.setText("清空")
        self.groupBox.setTitle("TCP")
        self.label.setText("本机IP:")
        self.label_2.setText("端口Port:")
        self.pushButton.setText("OPEN")
        self.label_port_state.setText("TCP Status:端口没有打开")
        self.groupBox_3.setTitle("定位结果")
        self.groupBox_4.setTitle("基站配置")

        tag_headers = ["地址", "x", "y", "z"]
        for column, text in enumerate(tag_headers):
            item = self.table_tag.horizontalHeaderItem(column)
            if item is not None:
                item.setText(text)

        anthor_headers = ["使能", "地址", "x", "y", "z"]
        for column, text in enumerate(anthor_headers):
            item = self.table_anthor.horizontalHeaderItem(column)
            if item is not None:
                item.setText(text)

    def configure_window_layout(self):
        self.resize(1770, 1935)
        self.setMinimumSize(1500, 1620)

        base_font = QtGui.QFont("Microsoft YaHei", 10)
        title_font = QtGui.QFont("Microsoft YaHei", 11)
        title_font.setBold(True)
        button_font = QtGui.QFont("Microsoft YaHei", 12)
        button_font.setBold(True)
        status_font = QtGui.QFont("Microsoft YaHei", 18)
        status_font.setBold(True)

        for widget in self.findChildren(QtWidgets.QWidget):
            widget.setFont(base_font)
        for group in (self.groupBox, self.groupBox_2, self.groupBox_3, self.groupBox_4):
            group.setFont(title_font)

        main_layout = QtWidgets.QVBoxLayout(self.centralWidget)
        main_layout.setContentsMargins(14, 10, 14, 14)
        main_layout.setSpacing(10)

        top_layout = QtWidgets.QHBoxLayout()
        top_layout.setSpacing(14)
        side_layout = QtWidgets.QVBoxLayout()
        side_layout.setSpacing(10)

        self.groupBox_4.setMinimumSize(430, 225)
        self.groupBox_3.setMinimumSize(360, 225)
        self.groupBox.setTitle("通信")
        self.groupBox.setMinimumSize(360, 250)
        self.groupBox_2.setMinimumSize(320, 58)

        anchor_layout = QtWidgets.QVBoxLayout(self.groupBox_4)
        anchor_layout.setContentsMargins(10, 22, 10, 10)
        anchor_layout.addWidget(self.table_anthor)

        tag_layout = QtWidgets.QVBoxLayout(self.groupBox_3)
        tag_layout.setContentsMargins(10, 22, 10, 10)
        tag_layout.addWidget(self.table_tag)

        self.comm_tab_widget = QtWidgets.QTabWidget(self.groupBox)
        self.tcp_tab = QtWidgets.QWidget()
        self.com_tab = QtWidgets.QWidget()
        self.comm_tab_widget.addTab(self.tcp_tab, "TCP")
        self.comm_tab_widget.addTab(self.com_tab, "COM")

        comm_layout = QtWidgets.QVBoxLayout(self.groupBox)
        comm_layout.setContentsMargins(12, 24, 12, 12)
        comm_layout.addWidget(self.comm_tab_widget)

        tcp_layout = QtWidgets.QGridLayout(self.tcp_tab)
        tcp_layout.setContentsMargins(14, 16, 14, 14)
        tcp_layout.setHorizontalSpacing(10)
        tcp_layout.setVerticalSpacing(10)
        tcp_layout.addWidget(self.label, 0, 0)
        tcp_layout.addWidget(self.label_ip, 0, 1)
        tcp_layout.addWidget(self.label_2, 1, 0)
        tcp_layout.addWidget(self.lineEdit_Port, 1, 1)
        tcp_layout.addWidget(self.pushButton, 2, 0, 1, 2, Qt.AlignCenter)
        tcp_layout.addWidget(self.label_port_state, 3, 0, 1, 2)
        tcp_layout.setColumnStretch(0, 0)
        tcp_layout.setColumnStretch(1, 1)
        self.label_port_state.show()

        self.com_port_label = QtWidgets.QLabel("串口:")
        self.com_port_combo = QtWidgets.QComboBox()
        self.com_refresh_button = QtWidgets.QPushButton("刷新")
        self.com_baud_label = QtWidgets.QLabel("波特率:")
        self.com_baud_combo = QtWidgets.QComboBox()
        self.com_baud_combo.addItems(["115200", "921600", "460800", "230400", "57600", "38400", "19200", "9600"])
        self.com_open_button = QtWidgets.QPushButton("OPEN")
        self.com_status_label = QtWidgets.QLabel("COM Status:串口没有打开")
        self.com_status_label.setStyleSheet("color: #B42318; font-weight: 700;")
        self.com_refresh_button.clicked.connect(self.refresh_serial_ports)
        self.com_open_button.clicked.connect(self.do_serial_start)

        com_layout = QtWidgets.QGridLayout(self.com_tab)
        com_layout.setContentsMargins(14, 16, 14, 14)
        com_layout.setHorizontalSpacing(10)
        com_layout.setVerticalSpacing(10)
        com_layout.addWidget(self.com_port_label, 0, 0)
        com_layout.addWidget(self.com_port_combo, 0, 1)
        com_layout.addWidget(self.com_refresh_button, 0, 2)
        com_layout.addWidget(self.com_baud_label, 1, 0)
        com_layout.addWidget(self.com_baud_combo, 1, 1, 1, 2)
        com_layout.addWidget(self.com_open_button, 2, 0, 1, 3, Qt.AlignCenter)
        com_layout.addWidget(self.com_status_label, 3, 0, 1, 3)
        com_layout.setColumnStretch(1, 1)

        history_layout = QtWidgets.QHBoxLayout(self.groupBox_2)
        history_layout.setContentsMargins(28, 14, 28, 12)
        history_layout.addStretch(1)
        history_layout.addWidget(self.label_3)
        history_layout.addWidget(self.spinBox)
        history_layout.addStretch(1)

        side_layout.addWidget(self.groupBox)
        side_layout.addWidget(self.groupBox_2)
        side_layout.addStretch(1)

        top_layout.addWidget(self.groupBox_4, 4)
        top_layout.addWidget(self.groupBox_3, 3)
        top_layout.addLayout(side_layout, 3)
        main_layout.addLayout(top_layout)
        main_layout.addWidget(self.tabWidget, 1)

        locate_layout = QtWidgets.QVBoxLayout(self.tab)
        locate_layout.setContentsMargins(0, 0, 0, 0)
        locate_layout.setSpacing(8)

        zoom_bar = QtWidgets.QHBoxLayout()
        zoom_bar.setContentsMargins(12, 10, 12, 0)
        self.zoom_text = QtWidgets.QLabel("\u7f29\u653e")
        self.zoom_text.setObjectName("ZoomLabel")
        self.zoom_out_button = QtWidgets.QPushButton("-")
        self.zoom_out_button.setObjectName("ZoomButton")
        self.zoom_out_button.setFixedSize(42, 32)
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.zoom_label = QtWidgets.QLabel("100%")
        self.zoom_label.setObjectName("ZoomLabel")
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(70)
        self.zoom_reset_button = QtWidgets.QPushButton("\u91cd\u7f6e")
        self.zoom_reset_button.setObjectName("ZoomButton")
        self.zoom_reset_button.setFixedSize(78, 32)
        self.zoom_reset_button.clicked.connect(self.zoom_reset)
        self.zoom_in_button = QtWidgets.QPushButton("+")
        self.zoom_in_button.setObjectName("ZoomButton")
        self.zoom_in_button.setFixedSize(42, 32)
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.measurement_toggle_button = QtWidgets.QPushButton("\u9690\u85cf\u6d4b\u8ddd")
        self.measurement_toggle_button.setObjectName("ZoomButton")
        self.measurement_toggle_button.setFixedSize(112, 32)
        self.measurement_toggle_button.setToolTip("\u663e\u793a\u6216\u9690\u85cf\u6d4b\u8ddd\u5706\u3001\u8ddd\u79bb\u8fde\u7ebf\u548c\u8ddd\u79bb\u6807\u6ce8")
        self.measurement_toggle_button.clicked.connect(self.toggle_measurement_aid)
        zoom_bar.addWidget(self.zoom_text)
        zoom_bar.addSpacing(8)
        zoom_bar.addWidget(self.zoom_out_button)
        zoom_bar.addWidget(self.zoom_label)
        zoom_bar.addWidget(self.zoom_reset_button)
        zoom_bar.addWidget(self.zoom_in_button)
        zoom_bar.addSpacing(14)
        zoom_bar.addWidget(self.measurement_toggle_button)
        zoom_bar.addStretch(1)
        self.view_angle_text = QtWidgets.QLabel("3D\u89c6\u56fe")
        self.view_angle_text.setObjectName("ZoomLabel")
        self.view_angle_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.view_angle_slider.setRange(15, 75)
        self.view_angle_slider.setValue(self.view_angle_deg)
        self.view_angle_slider.setFixedWidth(180)
        self.view_angle_slider.valueChanged.connect(self.on_view_angle_changed)
        self.view_angle_label = QtWidgets.QLabel("%d deg" % self.view_angle_deg)
        self.view_angle_label.setObjectName("ZoomLabel")
        self.view_angle_label.setAlignment(Qt.AlignCenter)
        self.view_angle_label.setMinimumWidth(74)
        self.view_angle_reset_button = QtWidgets.QPushButton("\u89c6\u89d2\u91cd\u7f6e")
        self.view_angle_reset_button.setObjectName("ZoomButton")
        self.view_angle_reset_button.setFixedSize(116, 32)
        self.view_angle_reset_button.clicked.connect(self.reset_view_angle)
        zoom_bar.addWidget(self.view_angle_text)
        zoom_bar.addSpacing(8)
        zoom_bar.addWidget(self.view_angle_slider)
        zoom_bar.addWidget(self.view_angle_label)
        zoom_bar.addWidget(self.view_angle_reset_button)
        locate_layout.addLayout(zoom_bar)
        locate_layout.addWidget(self.graphicsView)

        debug_layout = QtWidgets.QVBoxLayout(self.tab_2)
        debug_layout.setContentsMargins(0, 0, 0, 0)
        debug_layout.setSpacing(8)
        self.debug_splitter = QtWidgets.QSplitter(Qt.Vertical)
        self.debug_splitter.addWidget(self.edit_log)
        self.debug_parse_panel = QtWidgets.QWidget()
        parse_panel_layout = QtWidgets.QVBoxLayout(self.debug_parse_panel)
        parse_panel_layout.setContentsMargins(0, 0, 0, 0)
        parse_panel_layout.setSpacing(8)
        self.debug_parse_splitter = QtWidgets.QSplitter(Qt.Horizontal)

        self.debug_distance_table = QtWidgets.QTableWidget()
        self.debug_distance_table.setColumnCount(6)
        self.debug_distance_table.setHorizontalHeaderLabels(["标签", "帧", "基站", "距离(m)", "RSSI", "状态"])
        self.debug_distance_table.verticalHeader().setVisible(False)
        self.debug_distance_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.debug_distance_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.debug_distance_table.setAlternatingRowColors(True)
        self.debug_distance_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.debug_parse_splitter.addWidget(self.debug_distance_table)

        self.debug_plot_panel = QtWidgets.QWidget()
        debug_plot_layout = QtWidgets.QVBoxLayout(self.debug_plot_panel)
        debug_plot_layout.setContentsMargins(0, 0, 0, 0)
        debug_plot_layout.setSpacing(8)
        tag_select_layout = QtWidgets.QHBoxLayout()
        tag_select_layout.addStretch(1)
        tag_select_layout.addWidget(QtWidgets.QLabel("标签:"))
        self.debug_tag_combo = QtWidgets.QComboBox()
        self.debug_tag_combo.setMinimumWidth(140)
        self.debug_tag_combo.currentIndexChanged.connect(self.on_debug_tag_changed)
        tag_select_layout.addWidget(self.debug_tag_combo)
        debug_plot_layout.addLayout(tag_select_layout)
        self.debug_graphics_view = QtWidgets.QGraphicsView()
        self.debug_graphics_view.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
        self.debug_graphics_view.setStyleSheet("background: #F8FAFC; border: 1px solid #D7DEE8; border-radius: 6px;")
        self.debug_graphics_scene = QtWidgets.QGraphicsScene(self)
        self.debug_graphics_view.setScene(self.debug_graphics_scene)
        debug_plot_layout.addWidget(self.debug_graphics_view, 1)
        self.debug_parse_splitter.addWidget(self.debug_plot_panel)
        self.debug_parse_splitter.setSizes([520, 760])

        parse_panel_layout.addWidget(self.debug_parse_splitter)
        self.debug_parse_panel.setVisible(False)
        self.debug_splitter.addWidget(self.debug_parse_panel)
        self.debug_splitter.setSizes([780, 420])
        debug_layout.addWidget(self.debug_splitter, 1)
        debug_button_layout = QtWidgets.QHBoxLayout()
        debug_button_layout.addStretch(1)
        self.btn_save_log = QtWidgets.QPushButton("\u65e5\u5fd7")
        self.btn_parse = QtWidgets.QPushButton("\u89e3\u6790")
        for debug_button in (self.btn_start, self.btn_clear, self.btn_parse, self.btn_save_log):
            debug_button.setFixedSize(132, 43)
        debug_button_layout.addWidget(self.btn_start)
        debug_button_layout.addSpacing(48)
        debug_button_layout.addWidget(self.btn_clear)
        debug_button_layout.addSpacing(48)
        debug_button_layout.addWidget(self.btn_parse)
        debug_button_layout.addSpacing(48)
        debug_button_layout.addWidget(self.btn_save_log)
        debug_button_layout.addStretch(1)
        debug_layout.addLayout(debug_button_layout)

        self.pushButton.setFont(button_font)
        self.lineEdit_Port.setFont(button_font)
        self.label_port_state.setFont(status_font)
        self.com_status_label.setFont(status_font)
        self.label_port_state.setMinimumHeight(44)
        self.com_status_label.setMinimumHeight(44)
        for debug_button in (self.btn_start, self.btn_clear, self.btn_parse, self.btn_save_log):
            debug_button.setFont(button_font)

        self.table_anthor.setRowCount(len(gAnthor_Node_Configure))
        self.table_anthor.setMinimumHeight(176)
        self.table_anthor.setColumnWidth(0, 58)
        self.table_anthor.setColumnWidth(1, 108)
        self.table_anthor.setColumnWidth(2, 72)
        self.table_anthor.setColumnWidth(3, 72)
        self.table_anthor.setColumnWidth(4, 72)
        self.table_anthor.verticalHeader().setDefaultSectionSize(34)
        self.table_anthor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table_anthor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table_anthor.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        self.table_anthor.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Fixed)
        self.table_anthor.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table_anthor.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.table_anthor.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)

        self.table_tag.setRowCount(8)
        self.table_tag.setMinimumHeight(176)
        self.table_tag.setColumnWidth(0, 104)
        self.table_tag.setColumnWidth(1, 76)
        self.table_tag.setColumnWidth(2, 76)
        self.table_tag.setColumnWidth(3, 76)
        self.table_tag.verticalHeader().setDefaultSectionSize(34)
        self.table_tag.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table_tag.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        self.table_tag.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table_tag.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table_tag.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)

    def center_on_screen(self):
        screen = self.screen() or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        top_left = frame.topLeft()

        if frame.width() <= available.width():
            top_left.setX(max(available.left(), min(top_left.x(), available.right() - frame.width() + 1)))
        else:
            top_left.setX(available.left())

        if frame.height() <= available.height():
            top_left.setY(max(available.top(), min(top_left.y(), available.bottom() - frame.height() + 1)))
        else:
            top_left.setY(available.top())

        self.move(top_left)

    def apply_business_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background: #EEF2F6;
                color: #1F2933;
            }
            QGroupBox {
                background: #FFFFFF;
                border: 1px solid #D7DEE8;
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 8px;
                color: #243B53;
                background: #EEF2F6;
                font-weight: 700;
            }
            QTabWidget::pane {
                border: 1px solid #D7DEE8;
                background: #FFFFFF;
                top: -1px;
            }
            QTabBar::tab {
                background: #E6EBF1;
                color: #52606D;
                border: 1px solid #CBD5E1;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 9px 20px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                color: #1F2933;
            }
            QTableWidget {
                background: #FFFFFF;
                alternate-background-color: #F7F9FC;
                gridline-color: #E1E7EF;
                border: 1px solid #D7DEE8;
                border-radius: 6px;
                selection-background-color: #0F766E;
                selection-color: #FFFFFF;
            }
            QHeaderView::section {
                background: #F2F5F8;
                color: #334E68;
                border: 0;
                border-right: 1px solid #D7DEE8;
                border-bottom: 1px solid #D7DEE8;
                padding: 7px;
                font-weight: 700;
            }
            QLineEdit, QSpinBox, QPlainTextEdit {
                background: #FFFFFF;
                color: #1F2933;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 5px 8px;
                selection-background-color: #0F766E;
            }
            QPushButton {
                background: #243B53;
                color: #FFFFFF;
                border: 1px solid #1B2F45;
                border-radius: 7px;
                padding: 7px 18px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #2F4B66;
            }
            QPushButton:pressed {
                background: #152536;
            }
            QPushButton#ZoomButton {
                background: #FFFFFF;
                color: #243B53;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton#ZoomButton:hover {
                background: #F2F5F8;
                border-color: #94A3B8;
            }
            QLabel#ZoomLabel {
                background: #FFFFFF;
                color: #334E68;
                border: 1px solid #D7DEE8;
                border-radius: 6px;
                padding: 5px 10px;
                font-weight: 700;
            }
            QGraphicsView {
                background: #F8FAFC;
                border: 1px solid #D7DEE8;
                border-radius: 6px;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: #E6EBF1;
                border: none;
                margin: 0;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: #AAB7C4;
                border-radius: 4px;
                min-height: 24px;
                min-width: 24px;
            }
            QLabel {
                background: transparent;
            }
            QStatusBar {
                background: #243B53;
                color: #FFFFFF;
                border-top: 1px solid #1B2F45;
            }
            QStatusBar QLabel {
                color: #FFFFFF;
                padding: 2px 12px;
                font-weight: 600;
            }
        """)
        self.scene.setBackgroundBrush(QBrush(QColor("#F8FAFC")))
        self.gQtColor = [
            QColor("#0F766E"),
            QColor("#B7791F"),
            QColor("#7C3AED"),
            QColor("#2563EB"),
            QColor("#C2410C"),
            QColor("#047857"),
            QColor("#4B5563"),
            QColor("#BE123C"),
            QColor("#0E7490"),
            QColor("#92400E"),
        ]

    def refresh_serial_ports(self):
        current = self.com_port_combo.currentData()
        self.com_port_combo.clear()

        if serial is None or list_ports is None:
            self.com_port_combo.addItem("pyserial not installed", "")
            self.com_status_label.setText("COM Status:pyserial \u672a\u5b89\u88c5")
            self.set_comm_status_style(self.com_status_label, "closed")
            self.update_comm_controls()
            return

        ports = list(list_ports.comports())
        for port in ports:
            self.com_port_combo.addItem("%s  %s" % (port.device, port.description), port.device)

        if not ports:
            self.com_port_combo.addItem("\u672a\u53d1\u73b0\u4e32\u53e3", "")
            self.com_status_label.setText("COM Status:\u672a\u53d1\u73b0\u4e32\u53e3")
            self.set_comm_status_style(self.com_status_label, "warning")
        else:
            index = self.com_port_combo.findData(current)
            if index >= 0:
                self.com_port_combo.setCurrentIndex(index)
            self.com_status_label.setText("COM Status:\u4e32\u53e3\u6ca1\u6709\u6253\u5f00")
            self.set_comm_status_style(self.com_status_label, "closed")

        self.update_comm_controls()

    def do_tcp_server_start(self):
        if self.pushButton.text() == "OPEN":
            if self.serial_server.is_open():
                QMessageBox.information(self, "COM Connected", "Please close COM before opening TCP.")
                self.update_comm_controls()
                return

            try:
                port = int(self.lineEdit_Port.text())
                if not 1 <= port <= 65535:
                    raise ValueError
                self.label_ip.setText(self.get_local_ip())
                self.tcp_server.tcp_init(port)
            except ValueError:
                QMessageBox.warning(self, "Port Error", "Please enter a TCP port from 1 to 65535.")
                return
            except OSError as exc:
                QMessageBox.warning(self, "TCP Open Failed", str(exc))
                return

            self.lineEdit_Port.setReadOnly(True)
            thread = Thread(target=self.tcp_server.accept_client, daemon=True)
            thread.start()
            self.pushButton.setText("CLOSE")
            self.label_port_state.setText("TCP Status:\u7aef\u53e3\u5df2\u7ecf\u6253\u5f00")
            self.set_comm_status_style(self.label_port_state, "open")
            self.set_connection_status("TCP \u5df2\u8fde\u63a5 %s:%d" % (self.label_ip.text(), port))

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            self.update_comm_controls()
            return

        reply = QMessageBox.question(self, "Confirm", "Close TCP?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.lineEdit_Port.setReadOnly(False)
            self.tcp_server.tcp_close()
            self.pushButton.setText("OPEN")
            self.label_port_state.setText("TCP Status:\u7aef\u53e3\u6ca1\u6709\u6253\u5f00")
            self.set_comm_status_style(self.label_port_state, "closed")
            self.set_connection_status("\u672a\u8fde\u63a5")
            self.do_unlock_Table()
            self.update_comm_controls()

    def do_serial_start(self):
        if self.com_open_button.text() == "OPEN":
            if self.tcp_server.is_open():
                QMessageBox.information(self, "TCP Connected", "Please close TCP before opening COM.")
                self.update_comm_controls()
                return

            port = self.com_port_combo.currentData()
            if not port:
                QMessageBox.warning(self, "COM Error", "Please select an available serial port.")
                self.update_comm_controls()
                return

            try:
                baudrate = int(self.com_baud_combo.currentText())
                self.serial_server.serial_init(port, baudrate)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "COM Open Failed", str(exc))
                return

            thread = Thread(target=self.serial_server.read_loop, daemon=True)
            thread.start()
            self.com_open_button.setText("CLOSE")
            self.com_status_label.setText("COM Status:%s \u5df2\u6253\u5f00" % port)
            self.set_comm_status_style(self.com_status_label, "open")
            self.set_connection_status("COM \u5df2\u8fde\u63a5 %s @ %d" % (port, baudrate))

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            self.update_comm_controls()
            return

        self.serial_server.serial_close()
        self.com_open_button.setText("OPEN")
        self.com_status_label.setText("COM Status:\u4e32\u53e3\u6ca1\u6709\u6253\u5f00")
        self.set_comm_status_style(self.com_status_label, "closed")
        self.set_connection_status("\u672a\u8fde\u63a5")
        self.do_unlock_Table()
        self.update_comm_controls()

    def setup_status_bar(self):
        self.connection_status_label = QtWidgets.QLabel("连接状态:未连接")
        self.algorithm_status_label = QtWidgets.QLabel("定位算法:等待数据")
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(self.connection_status_label, 1)
        self.statusBar().addPermanentWidget(self.algorithm_status_label, 1)

    def set_connection_status(self, status):
        self.connection_status_label.setText("连接状态:%s" % status)

    def set_algorithm_status(self, status):
        self.algorithm_status_label.setText("定位算法:%s" % status)

    def eventFilter(self, source, event):
        if source is self.graphicsView.viewport() and event.type() == QtCore.QEvent.Wheel:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            return True
        return super().eventFilter(source, event)

    def update_zoom_label(self):
        if hasattr(self, "zoom_label"):
            self.zoom_label.setText("%d%%" % round(self.zoom_factor * 100))

    def set_zoom(self, target_zoom):
        target_zoom = max(self.zoom_min, min(self.zoom_max, target_zoom))
        if abs(target_zoom - self.zoom_factor) < 0.001:
            return
        scale_factor = target_zoom / self.zoom_factor
        self.zoom_factor = target_zoom
        self.graphicsView.scale(scale_factor, scale_factor)
        self.update_zoom_label()

    def zoom_in(self):
        self.set_zoom(self.zoom_factor * self.zoom_step)

    def zoom_out(self):
        self.set_zoom(self.zoom_factor / self.zoom_step)

    def zoom_reset(self):
        self.graphicsView.resetTransform()
        self.zoom_factor = 1.0
        self.graphicsView.centerOn(self.scene_center_x, self.scene_center_y)
        self.update_zoom_label()

    def on_view_angle_changed(self, value):
        self.view_angle_deg = value
        if self.display_mode != "3D":
            self.view_angle_label.setText("2D")
            return
        self.view_angle_label.setText("%d deg" % value)
        self.redraw_scene_keep_tags()

    def reset_view_angle(self):
        if self.display_mode != "3D":
            return
        self.view_angle_slider.setValue(35)

    def update_view_angle_controls(self):
        is_3d = self.display_mode == "3D"
        for widget in (
            getattr(self, "view_angle_text", None),
            getattr(self, "view_angle_slider", None),
            getattr(self, "view_angle_label", None),
            getattr(self, "view_angle_reset_button", None),
        ):
            if widget is not None:
                widget.setEnabled(is_3d)
        if hasattr(self, "view_angle_label"):
            self.view_angle_label.setText("%d deg" % self.view_angle_deg if is_3d else "2D")

    def toggle_measurement_aid(self):
        self.measurement_aid_enabled = not self.measurement_aid_enabled
        if hasattr(self, "measurement_toggle_button"):
            self.measurement_toggle_button.setText(
                "\u9690\u85cf\u6d4b\u8ddd" if self.measurement_aid_enabled else "\u663e\u793a\u6d4b\u8ddd"
            )
        self.refresh_location_measurement_overlay_throttled(force=True)

    def redraw_scene_keep_tags(self):
        existing_tags = []
        for item in self.gTag_Result:
            if item["result"]:
                last = item["result"][-1]
                existing_tags.append((item["short_address"], {"x": last["x"], "y": last["y"], "z": last["z"]}))
        self.scene.clear()
        self.Display_Anthor(globalvar.get_anthor())
        self.gTag_Result = []
        self.table_tag.clearContents()
        for short_address, coor_info in existing_tags:
            self.Insert_Tag_Result(short_address, coor_info)
        self.refresh_location_measurement_overlay_throttled(force=True)

    @staticmethod
    def get_local_ip():
        candidates = []

        def add_candidate(ip):
            try:
                ip_obj = ipaddress.ip_address(ip)
            except ValueError:
                return
            if ip_obj.version != 4:
                return
            if ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_unspecified:
                return
            if ip not in candidates:
                candidates.append(ip)

        for target in ("8.8.8.8", "1.1.1.1", "223.5.5.5", "114.114.114.114"):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect((target, 80))
                add_candidate(sock.getsockname()[0])
            except OSError:
                pass
            finally:
                sock.close()

        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                add_candidate(info[4][0])
        except OSError:
            pass

        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                add_candidate(ip)
        except OSError:
            pass

        for ip in candidates:
            if ipaddress.ip_address(ip).is_private:
                return ip
        if candidates:
            return candidates[0]
        return "127.0.0.1"

    def compute_ratio(self, width, height, anthor_node_configure):
        enabled = [item for item in anthor_node_configure if item["enable"] == 1]
        if not enabled:
            return 100

        projected_points = []
        for item in enabled:
            projected_points.append(self.project_model_point(item["x"], item["y"], item["z"]))
            if self.display_mode == "3D":
                projected_points.append(self.project_model_point(item["x"], item["y"], 0))

        projected_points.append(self.project_model_point(0, 0, 0))
        min_x = min(point[0] for point in projected_points)
        max_x = max(point[0] for point in projected_points)
        min_y = min(point[1] for point in projected_points)
        max_y = max(point[1] for point in projected_points)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        usable_width = max(1, width - self.origin_margin_x - 260)
        usable_height = max(1, height - self.origin_margin_y * 2)
        ratio = max(1, int(min(usable_width / span_x, usable_height / span_y) * 0.72))

        self.plot_origin_x = self.origin_margin_x - min(0, min_x) * ratio
        self.plot_origin_y = height - self.origin_margin_y + min(0, min_y) * ratio
        return ratio

    def is_3d_anchor_config(self, anthor_node_configure):
        enabled = [item for item in anthor_node_configure if item["enable"] == 1]
        if len(enabled) != 4:
            return False
        first_z = enabled[0]["z"]
        return any(abs(item["z"] - first_z) > 1e-6 for item in enabled[1:])

    def project_model_point(self, point_x, point_y, point_z=0):
        if self.display_mode == "3D":
            angle = math.radians(self.view_angle_deg)
            return point_x + point_y * math.cos(angle), point_y * math.sin(angle) + point_z * self.z_scale
        return point_x, point_y

    def map_scene_point(self, point_x, point_y, point_z=0):
        projected_x, projected_y = self.project_model_point(point_x, point_y, point_z)
        return (
            int(self.plot_origin_x + projected_x * self.ratio),
            int(self.plot_origin_y - projected_y * self.ratio),
        )

    @staticmethod
    def anchor_number_map(anthor_node_configure):
        enabled = [item for item in anthor_node_configure if item["enable"] == 1]
        sorted_anchors = sorted(enabled, key=lambda item: item["short_address"])
        return {item["short_address"]: index + 1 for index, item in enumerate(sorted_anchors)}

    def Display_Anthor(self, anthor_node_configure):
        if hasattr(self, "measurement_overlay_items"):
            self.clear_measurement_overlay()
        self.display_mode = "3D" if self.is_3d_anchor_config(anthor_node_configure) else "2D"
        self.update_view_angle_controls()
        if hasattr(self, "display_status_label"):
            self.set_display_status()
        viewport_width = self.graphicsView.viewport().width()
        viewport_height = self.graphicsView.viewport().height()
        scene_margin_x = viewport_width * 2
        scene_margin_y = viewport_height * 2
        self.scene_center_x = viewport_width / 2
        self.scene_center_y = viewport_height / 2
        self.scene.setSceneRect(
            -scene_margin_x,
            -scene_margin_y,
            viewport_width + scene_margin_x * 2,
            viewport_height + scene_margin_y * 2,
        )
        self.graphicsView.setSceneRect(self.scene.sceneRect())
        self.ratio = self.compute_ratio(
            viewport_width,
            viewport_height,
            anthor_node_configure,
        )
        width = viewport_width
        height = viewport_height
        scene_rect = self.scene.sceneRect()
        anchor_numbers = self.anchor_number_map(anthor_node_configure)

        for item in anthor_node_configure:
            if item["enable"] == 0:
                item["qt"] = 0
                continue

            marker = self.anchor_marker_size
            qitem = QGraphicsEllipseItem(-marker / 2, -marker / 2, marker, marker)
            if time.time() - item["time"] > 3:
                qitem.setBrush(QBrush(QColor("#60A5FA")))
            else:
                qitem.setBrush(QBrush(QColor("#2563EB")))
            qitem.setPen(QPen(QColor("#FFFFFF"), 1.5))

            ground_x, ground_y = self.map_scene_point(item["x"], item["y"], 0)
            point_x, point_y = self.map_scene_point(item["x"], item["y"], item["z"])
            if self.display_mode == "3D":
                stem = QGraphicsLineItem(ground_x, ground_y, point_x, point_y)
                stem_pen = QPen(QColor("#64748B"))
                stem_pen.setWidthF(1.8)
                stem_pen.setStyle(Qt.DashLine)
                stem.setPen(stem_pen)
                self.scene.addItem(stem)

            qitem.setPos(point_x, point_y)
            self.scene.addItem(qitem)

            anchor_name = "基站%d" % anchor_numbers.get(item["short_address"], 0)
            label_html = (
                '<span style="color:#1F2933;">%s</span>'
                '<span style="color:#2563EB;">（</span>'
                '<span style="color:#1F2933;">%s</span>'
                '<span style="color:#2563EB;">, </span>'
                '<span style="color:#1F2933;">%s</span>'
                '<span style="color:#2563EB;">, </span>'
                '<span style="color:#1F2933;">%s</span>'
                '<span style="color:#2563EB;">）</span>'
            ) % (anchor_name, item["x"], item["y"], item["z"])
            label = self.scene.addText("")
            label.setFont(self.scene_label_font)
            label.setHtml(label_html)
            label.setDefaultTextColor(QColor("#1F2933"))
            label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            label.setPos(point_x + marker / 2 + 8, point_y - marker / 2 - 10)
            item["qt"] = qitem

        pen = QPen()
        pen.setColor(QColor("#7A8798"))
        pen.setWidthF(1.4)
        pen.setStyle(Qt.DashLine)
        pen.setDashPattern([8, 8])

        enabled = [item for item in anthor_node_configure if item["enable"] == 1]
        if self.display_mode == "3D":
            grid_x_min = math.floor(min(item["x"] for item in enabled)) - 4
            grid_x_max = math.ceil(max(item["x"] for item in enabled)) + 4
            grid_y_min = math.floor(min(item["y"] for item in enabled)) - 4
            grid_y_max = math.ceil(max(item["y"] for item in enabled)) + 4

            for grid_y in range(grid_y_min, grid_y_max + 1):
                x1, y1 = self.map_scene_point(grid_x_min, grid_y, 0)
                x2, y2 = self.map_scene_point(grid_x_max, grid_y, 0)
                line_item = QGraphicsLineItem(x1, y1, x2, y2)
                line_item.setPen(pen)
                self.scene.addItem(line_item)

            for grid_x in range(grid_x_min, grid_x_max + 1):
                x1, y1 = self.map_scene_point(grid_x, grid_y_min, 0)
                x2, y2 = self.map_scene_point(grid_x, grid_y_max, 0)
                line_item = QGraphicsLineItem(x1, y1, x2, y2)
                line_item.setPen(pen)
                self.scene.addItem(line_item)
        else:
            grid_x_min = math.floor((scene_rect.left() - self.plot_origin_x) / self.ratio)
            grid_x_max = math.ceil((scene_rect.right() - self.plot_origin_x) / self.ratio)
            grid_y_min = math.floor((self.plot_origin_y - scene_rect.bottom()) / self.ratio)
            grid_y_max = math.ceil((self.plot_origin_y - scene_rect.top()) / self.ratio)

            for grid_y in range(grid_y_min, grid_y_max + 1):
                _, scene_y = self.map_scene_point(0, grid_y)
                line_item = QGraphicsLineItem(scene_rect.left(), scene_y, scene_rect.right(), scene_y)
                line_item.setPen(pen)
                self.scene.addItem(line_item)

            for grid_x in range(grid_x_min, grid_x_max + 1):
                scene_x, _ = self.map_scene_point(grid_x, 0)
                line_item = QGraphicsLineItem(scene_x, scene_rect.top(), scene_x, scene_rect.bottom())
                line_item.setPen(pen)
                self.scene.addItem(line_item)
        self.graphicsView.centerOn(self.scene_center_x, self.scene_center_y)

    def show_anthor_configure(self, anthor_node_configure):
        self.table_anthor.blockSignals(True)
        for index, item in enumerate(anthor_node_configure):
            check_container = QtWidgets.QWidget()
            check_layout = QtWidgets.QHBoxLayout(check_container)
            check_layout.setContentsMargins(0, 0, 0, 0)
            check_layout.setAlignment(Qt.AlignCenter)
            check_box = QtWidgets.QCheckBox()
            check_box.setChecked(item["enable"] == 1)
            check_box.stateChanged.connect(lambda state, row=index: self.do_anchor_enable_changed(row, state))
            check_layout.addWidget(check_box)
            self.table_anthor.setCellWidget(index, 0, check_container)

            values = [
                "0x%04X" % item["short_address"],
                "%0.2f" % item["x"],
                "%0.2f" % item["y"],
                "%0.2f" % item["z"],
            ]
            for column, value in enumerate(values, start=1):
                new_item = QTableWidgetItem(value)
                new_item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table_anthor.setItem(index, column, new_item)
        self.table_anthor.blockSignals(False)

    def set_anchor_checkboxes_enabled(self, enabled):
        for row in range(self.table_anthor.rowCount()):
            widget = self.table_anthor.cellWidget(row, 0)
            if widget is None:
                continue
            checkbox = widget.findChild(QtWidgets.QCheckBox)
            if checkbox is not None:
                checkbox.setEnabled(enabled)

    def do_anchor_enable_changed(self, row, state):
        global gAnthor_Node_Configure
        if row >= len(gAnthor_Node_Configure):
            return
        gAnthor_Node_Configure[row]["enable"] = 1 if state == Qt.Checked else 0
        globalvar.set_anthor(gAnthor_Node_Configure)
        self.scene.clear()
        self.gTag_Result = []
        self.Display_Anthor(gAnthor_Node_Configure)

    def Remove_Tag_Pic(self, item):
        if item is None:
            return
        if isinstance(item, list):
            for child in item:
                self.Remove_Tag_Pic(child)
            return
        try:
            self.scene.removeItem(item)
        except RuntimeError:
            pass

    def Show_Tag_Pic(self, item, point_x, point_y, point_z, color_index):
        marker = self.tag_marker_size
        qitem = QGraphicsEllipseItem(-marker / 2, -marker / 2, marker, marker)
        ground_x, ground_y = self.map_scene_point(point_x, point_y, 0)
        scene_x, scene_y = self.map_scene_point(point_x, point_y, point_z)
        color = self.gQtColor[color_index % len(self.gQtColor)]
        qitem.setBrush(QBrush(color))
        qitem.setPen(QPen(QColor("#FFFFFF"), 1.8))
        qitem.setPos(scene_x, scene_y)
        qt_items = []
        if self.display_mode == "3D":
            stem = QGraphicsLineItem(ground_x, ground_y, scene_x, scene_y)
            stem_pen = QPen(color)
            stem_pen.setWidthF(2.0)
            stem_pen.setStyle(Qt.DashLine)
            stem.setPen(stem_pen)
            self.scene.addItem(stem)
            qt_items.append(stem)

            height_label = self.scene.addText("z:%0.2f" % point_z)
            height_label.setFont(self.scene_label_font)
            height_label.setDefaultTextColor(color)
            height_label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            height_label.setPos(scene_x + marker / 2 + 8, scene_y - marker / 2 - 10)
            qt_items.append(height_label)

        self.scene.addItem(qitem)
        qt_items.append(qitem)
        item["qt"] = qitem
        item["qt_items"] = qt_items

    def show_tag_result(self, shortaddress, avg_x, avg_y, avg_z, index):
        if index >= self.table_tag.rowCount():
            self.table_tag.setRowCount(index + 1)

        values = ["0x%04X" % shortaddress, "%0.2f" % avg_x, "%0.2f" % avg_y, "%0.2f" % avg_z]
        for column, value in enumerate(values):
            new_item = QTableWidgetItem(value)
            new_item.setTextAlignment(QtCore.Qt.AlignCenter)
            new_item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
            if column == 0:
                new_item.setForeground(QBrush(self.gQtColor[index % len(self.gQtColor)]))
            self.table_tag.setItem(index, column, new_item)

    def Insert_Tag_Result(self, short_address, coor_info):
        tag_entry = None
        tag_index = 0
        for index, item in enumerate(self.gTag_Result):
            if item["short_address"] == short_address:
                tag_entry = item
                tag_index = index
                break

        if tag_entry is None:
            tag_index = len(self.gTag_Result)
            tag_entry = {"short_address": short_address, "result": []}
            self.gTag_Result.append(tag_entry)

        history = tag_entry["result"]
        while len(history) >= self.MAX_HISTORY:
            self.Remove_Tag_Pic(history[0].get("qt_items", history[0].get("qt")))
            del history[0]

        point = {"x": coor_info["x"], "y": coor_info["y"], "z": coor_info.get("z", 0), "qt": None}
        history.append(point)

        avg_x = sum(item["x"] for item in history) / len(history)
        avg_y = sum(item["y"] for item in history) / len(history)
        avg_z = sum(item["z"] for item in history) / len(history)

        self.Show_Tag_Pic(point, avg_x, avg_y, avg_z, tag_index)
        for index, item in enumerate(history):
            opacity = (index + 1) / len(history)
            for qt_item in item.get("qt_items", [item.get("qt")]):
                if qt_item is not None:
                    qt_item.setOpacity(opacity)

        self.show_tag_result(short_address, avg_x, avg_y, avg_z, tag_index)

    def do_tcp_server_start(self):
        if self.pushButton.text() == "OPEN":
            try:
                port = int(self.lineEdit_Port.text())
                if not 1 <= port <= 65535:
                    raise ValueError
                self.tcp_server.tcp_init(port)
            except ValueError:
                QMessageBox.warning(self, "端口错误", "请输入 1 到 65535 之间的端口号。")
                return
            except OSError as exc:
                QMessageBox.warning(self, "TCP 打开失败", str(exc))
                return

            self.lineEdit_Port.setReadOnly(True)
            thread = Thread(target=self.tcp_server.accept_client, daemon=True)
            thread.start()

            self.pushButton.setText("CLOSE")
            self.label_port_state.setText("TCP Status:端口已经打开")
            self.label_port_state.setStyleSheet("color: #0F766E; font-weight: 700;")
            font = self.label_port_state.font()
            font.setBold(True)
            self.label_port_state.setFont(font)

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            return

        if self.pushButton.text() == "CLOSE":
            reply = QMessageBox.question(
                self,
                "请确认",
                "是否要关闭 TCP？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.lineEdit_Port.setReadOnly(False)
                self.tcp_server.tcp_close()
                self.pushButton.setText("OPEN")
                self.label_port_state.setText("TCP Status:端口没有打开")
                self.label_port_state.setStyleSheet("color: #B42318; font-weight: 700;")
                font = self.label_port_state.font()
                font.setBold(True)
                self.label_port_state.setFont(font)
                self.do_unlock_Table()

    def do_tcp_server_start(self):
        if self.pushButton.text() == "OPEN":
            try:
                if self.serial_server.is_open():
                    self.serial_server.serial_close()
                    self.com_open_button.setText("OPEN")
                    self.com_status_label.setText("COM Status:串口没有打开")
                    self.com_status_label.setStyleSheet("color: #B42318; font-weight: 700;")

                port = int(self.lineEdit_Port.text())
                if not 1 <= port <= 65535:
                    raise ValueError
                self.label_ip.setText(self.get_local_ip())
                self.tcp_server.tcp_init(port)
            except ValueError:
                QMessageBox.warning(self, "Port Error", "Please enter a TCP port from 1 to 65535.")
                return
            except OSError as exc:
                QMessageBox.warning(self, "TCP Open Failed", str(exc))
                return

            self.lineEdit_Port.setReadOnly(True)
            thread = Thread(target=self.tcp_server.accept_client, daemon=True)
            thread.start()

            self.pushButton.setText("CLOSE")
            self.label_port_state.setText("TCP Status:端口已经打开")
            self.label_port_state.setStyleSheet("color: #0F766E; font-weight: 700;")

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            return

        if self.pushButton.text() == "CLOSE":
            reply = QMessageBox.question(
                self,
                "Confirm",
                "Close TCP?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.lineEdit_Port.setReadOnly(False)
                self.tcp_server.tcp_close()
                self.pushButton.setText("OPEN")
                self.label_port_state.setText("TCP Status:端口没有打开")
                self.label_port_state.setStyleSheet("color: #B42318; font-weight: 700;")
                self.do_unlock_Table()

    def refresh_serial_ports(self):
        if not hasattr(self, "com_port_combo"):
            return

        current = self.com_port_combo.currentData()
        self.com_port_combo.clear()
        if serial is None or list_ports is None:
            self.com_port_combo.addItem("pyserial not installed", "")
            self.com_port_combo.setEnabled(False)
            self.com_open_button.setEnabled(False)
            self.com_status_label.setText("COM Status:pyserial 未安装")
            return

        ports = list(list_ports.comports())
        for port in ports:
            self.com_port_combo.addItem("%s - %s" % (port.device, port.description), port.device)

        if not ports:
            self.com_port_combo.addItem("未发现串口", "")
            self.com_status_label.setText("COM Status:未发现串口")
            self.com_status_label.setStyleSheet("color: #B7791F; font-weight: 700;")
        else:
            index = self.com_port_combo.findData(current)
            if index >= 0:
                self.com_port_combo.setCurrentIndex(index)
            self.com_status_label.setText("COM Status:串口没有打开")
            self.com_status_label.setStyleSheet("color: #B42318; font-weight: 700;")

        self.com_port_combo.setEnabled(True)
        self.com_open_button.setEnabled(bool(ports))

    def do_serial_start(self):
        if self.com_open_button.text() == "OPEN":
            if self.tcp_server.is_open():
                self.tcp_server.tcp_close()
                self.lineEdit_Port.setReadOnly(False)
                self.pushButton.setText("OPEN")
                self.label_port_state.setText("TCP Status:端口没有打开")
                self.label_port_state.setStyleSheet("color: #B42318; font-weight: 700;")

            port = self.com_port_combo.currentData()
            if not port:
                QMessageBox.warning(self, "COM Error", "Please select an available serial port.")
                return

            try:
                baudrate = int(self.com_baud_combo.currentText())
                self.serial_server.serial_init(port, baudrate)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "COM Open Failed", str(exc))
                return

            thread = Thread(target=self.serial_server.read_loop, daemon=True)
            thread.start()
            self.com_open_button.setText("CLOSE")
            self.com_port_combo.setEnabled(False)
            self.com_baud_combo.setEnabled(False)
            self.com_refresh_button.setEnabled(False)
            self.com_status_label.setText("COM Status:%s 已打开" % port)
            self.com_status_label.setStyleSheet("color: #0F766E; font-weight: 700;")

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            return

        self.serial_server.serial_close()
        self.com_open_button.setText("OPEN")
        self.com_port_combo.setEnabled(True)
        self.com_baud_combo.setEnabled(True)
        self.com_refresh_button.setEnabled(True)
        self.com_status_label.setText("COM Status:串口没有打开")
        self.com_status_label.setStyleSheet("color: #B42318; font-weight: 700;")
        self.do_unlock_Table()

    def set_comm_status_style(self, label, state):
        colors = {
            "open": "#0F766E",
            "closed": "#B42318",
            "warning": "#B7791F",
        }
        point_size = 18
        label.setStyleSheet(
            "color: %s; font-weight: 700;" %
            colors.get(state, colors["closed"])
        )
        font = label.font()
        font.setPointSize(point_size)
        font.setBold(True)
        label.setFont(font)
        label.setMinimumHeight(44)

    def update_comm_controls(self):
        tcp_open = self.tcp_server.is_open()
        com_open = self.serial_server.is_open()
        tcp_index = self.comm_tab_widget.indexOf(self.tcp_tab)
        com_index = self.comm_tab_widget.indexOf(self.com_tab)

        self.comm_tab_widget.setTabEnabled(tcp_index, not com_open)
        self.comm_tab_widget.setTabEnabled(com_index, not tcp_open)

        self.pushButton.setEnabled(not com_open or tcp_open)
        self.lineEdit_Port.setEnabled((not com_open) and (not tcp_open))

        has_serial_port = bool(self.com_port_combo.currentData())
        self.com_open_button.setEnabled(com_open or ((not tcp_open) and has_serial_port))
        self.com_port_combo.setEnabled((not tcp_open) and (not com_open) and has_serial_port)
        self.com_baud_combo.setEnabled((not tcp_open) and (not com_open))
        self.com_refresh_button.setEnabled((not tcp_open) and (not com_open))

        debug_enabled = tcp_open or com_open
        self.btn_start.setEnabled(debug_enabled)
        self.btn_clear.setEnabled(debug_enabled)
        self.btn_parse.setEnabled(debug_enabled)
        self.btn_save_log.setEnabled(debug_enabled)
        if not debug_enabled and self.enable_log:
            self.enable_log = False
            self.btn_start.setText("\u5f00\u59cb")
        if not debug_enabled and self.file_log_enabled:
            self.stop_file_log()

    def on_serial_status_message(self, message):
        if self.serial_server.is_open():
            self.set_comm_status_style(self.com_status_label, "open")
        else:
            self.set_comm_status_style(self.com_status_label, "closed")
            if self.com_open_button.text() == "CLOSE":
                self.com_open_button.setText("OPEN")
                self.set_connection_status("未连接")
                self.do_unlock_Table()
        self.com_status_label.setText(message)
        self.update_comm_controls()

    def do_tcp_server_start(self):
        if self.pushButton.text() == "OPEN":
            try:
                if self.serial_server.is_open():
                    self.serial_server.serial_close()
                    self.com_open_button.setText("OPEN")
                    self.com_status_label.setText("COM Status:串口没有打开")
                    self.com_status_label.setStyleSheet("color: #B42318; font-weight: 700;")

                port = int(self.lineEdit_Port.text())
                if not 1 <= port <= 65535:
                    raise ValueError
                self.label_ip.setText(self.get_local_ip())
                self.tcp_server.tcp_init(port)
            except ValueError:
                QMessageBox.warning(self, "Port Error", "Please enter a TCP port from 1 to 65535.")
                return
            except OSError as exc:
                QMessageBox.warning(self, "TCP Open Failed", str(exc))
                return

            self.lineEdit_Port.setReadOnly(True)
            thread = Thread(target=self.tcp_server.accept_client, daemon=True)
            thread.start()
            self.pushButton.setText("CLOSE")
            self.label_port_state.setText("TCP Status:端口已经打开")
            self.label_port_state.setStyleSheet("color: #0F766E; font-weight: 700;")
            self.set_connection_status("TCP 已连接 %s:%d" % (self.label_ip.text(), port))

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            return

        reply = QMessageBox.question(self, "Confirm", "Close TCP?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.lineEdit_Port.setReadOnly(False)
            self.tcp_server.tcp_close()
            self.pushButton.setText("OPEN")
            self.label_port_state.setText("TCP Status:端口没有打开")
            self.label_port_state.setStyleSheet("color: #B42318; font-weight: 700;")
            self.set_connection_status("未连接")
            self.do_unlock_Table()

    def do_serial_start(self):
        if self.com_open_button.text() == "OPEN":
            if self.tcp_server.is_open():
                self.tcp_server.tcp_close()
                self.lineEdit_Port.setReadOnly(False)
                self.pushButton.setText("OPEN")
                self.label_port_state.setText("TCP Status:端口没有打开")
                self.label_port_state.setStyleSheet("color: #B42318; font-weight: 700;")

            port = self.com_port_combo.currentData()
            if not port:
                QMessageBox.warning(self, "COM Error", "Please select an available serial port.")
                return

            try:
                baudrate = int(self.com_baud_combo.currentText())
                self.serial_server.serial_init(port, baudrate)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "COM Open Failed", str(exc))
                return

            thread = Thread(target=self.serial_server.read_loop, daemon=True)
            thread.start()
            self.com_open_button.setText("CLOSE")
            self.com_port_combo.setEnabled(False)
            self.com_baud_combo.setEnabled(False)
            self.com_refresh_button.setEnabled(False)
            self.com_status_label.setText("COM Status:%s 已打开" % port)
            self.com_status_label.setStyleSheet("color: #0F766E; font-weight: 700;")
            self.set_connection_status("COM 已连接 %s @ %d" % (port, baudrate))

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            return

        self.serial_server.serial_close()
        self.com_open_button.setText("OPEN")
        self.com_port_combo.setEnabled(True)
        self.com_baud_combo.setEnabled(True)
        self.com_refresh_button.setEnabled(True)
        self.com_status_label.setText("COM Status:串口没有打开")
        self.com_status_label.setStyleSheet("color: #B42318; font-weight: 700;")
        self.set_connection_status("未连接")
        self.do_unlock_Table()

    def setup_status_bar(self):
        self.connection_status_label = QtWidgets.QLabel("连接状态:未连接")
        self.algorithm_status_label = QtWidgets.QLabel("定位算法:等待数据")
        self.display_status_label = QtWidgets.QLabel("显示模式:2D")
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(self.connection_status_label, 1)
        self.statusBar().addPermanentWidget(self.algorithm_status_label, 1)
        self.statusBar().addPermanentWidget(self.display_status_label, 1)

    def set_connection_status(self, status):
        self.connection_status_label.setText("连接状态:%s" % status)

    def set_algorithm_status(self, status):
        self.algorithm_status_label.setText("定位算法:%s" % status)

    def set_display_status(self):
        self.display_status_label.setText("显示模式:%s" % self.display_mode)

    def do_lock_Table(self):
        self.table_anthor.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.set_anchor_checkboxes_enabled(False)

    def do_unlock_Table(self):
        self.table_anthor.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self.set_anchor_checkboxes_enabled(True)

    def do_adjust_maxhistory(self):
        self.MAX_HISTORY = self.spinBox.value()

    def do_btn_clear(self):
        self.edit_log.clear()

    def do_btn_save_log(self):
        if self.file_log_enabled:
            self.stop_file_log()
            return

        log_dir = os.path.join(os.getcwd(), "logs")
        filename = "uwb_stream_%s.txt" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.file_log_path = os.path.join(log_dir, filename)
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(self.file_log_path, "w", encoding="utf-8") as log_file:
                log_file.write("# 51UWB TCP/COM raw and parsed distance log\n")
                log_file.write("# start: %s\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                log_file.write("# raw lines are recorded after LOG is enabled; existing window text is not copied.\n\n")
        except OSError as exc:
            self.file_log_path = ""
            QMessageBox.warning(self, "Log Failed", str(exc))
            return

        self.file_log_enabled = True
        self.btn_save_log.setText("\u505c\u6b62\u65e5\u5fd7")
        self.append_file_log("LOG START %s" % self.file_log_path)

    def stop_file_log(self):
        if self.file_log_enabled:
            self.append_file_log("LOG STOP")
        self.file_log_enabled = False
        self.btn_save_log.setText("\u65e5\u5fd7")

    def append_file_log(self, text):
        if not self.file_log_enabled or not self.file_log_path:
            return

        timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
        try:
            with open(self.file_log_path, "a", encoding="utf-8") as log_file:
                for line in str(text).splitlines() or [""]:
                    log_file.write("%s %s\n" % (timestamp, line))
        except OSError as exc:
            self.file_log_enabled = False
            self.btn_save_log.setText("\u65e5\u5fd7")
            QMessageBox.warning(self, "Log Failed", str(exc))

    def format_parse_log_text(self, parse_info):
        tag = int(parse_info.get("tag", 0))
        seq = parse_info.get("seq", 0)
        motion = self.format_motion_state(parse_info.get("motion_state", ""))
        algorithm = parse_info.get("algorithm", "-")
        location_result = parse_info.get("location_result", 0)
        location_x = float(parse_info.get("location_x", 0))
        location_y = float(parse_info.get("location_y", 0))
        location_z = float(parse_info.get("location_z", 0))
        lines = [
            "PARSE TAG=0x%04X SEQ=%s STATE=%s ALGORITHM=%s LOCATION=%s (%.2f, %.2f, %.2f)" %
            (tag, seq, motion, algorithm, "OK" if location_result == 1 else "NO", location_x, location_y, location_z)
        ]
        for anchor in parse_info.get("anthor", []):
            anchor_address = int(anchor[0])
            distance = float(anchor[1])
            rssi = anchor[2]
            lines.append(
                "PARSE DIST TAG=0x%04X SEQ=%s ANCHOR=0x%04X DIST=%.2fm RSSI=%s" %
                (tag, seq, anchor_address, distance, rssi)
            )
        return "\n".join(lines)

    def do_btn_parse_log(self):
        self.debug_parse_enabled = not self.debug_parse_enabled
        self.debug_parse_panel.setVisible(self.debug_parse_enabled)
        self.btn_parse.setText("\u5173\u95ed\u89e3\u6790" if self.debug_parse_enabled else "\u89e3\u6790")
        if self.debug_parse_enabled:
            self.debug_splitter.setSizes([max(360, int(self.debug_splitter.height() * 0.48)), 520])
            self.refresh_debug_tag_combo()
            self.refresh_debug_parse_view()

    @staticmethod
    def format_motion_state(motion_state):
        if motion_state == "s":
            return "\u9759\u6b62"
        if motion_state == "m":
            return "\u8fd0\u52a8"
        return "-"

    @staticmethod
    def find_anchor_config(anchor_address):
        for item in globalvar.get_anthor():
            if item["short_address"] == anchor_address:
                return item
        return None

    def clear_measurement_overlay(self):
        for item in getattr(self, "measurement_overlay_items", []):
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self.measurement_overlay_items = []

    def add_measurement_overlay_item(self, item, z_value=None):
        if z_value is not None:
            item.setZValue(z_value)
        self.measurement_overlay_items.append(item)
        return item

    def tag_color_index(self, tag):
        for index, item in enumerate(self.gTag_Result):
            if item["short_address"] == tag:
                return index
        tags = sorted(self.debug_parse_data)
        try:
            return tags.index(tag)
        except ValueError:
            return len(tags)

    @staticmethod
    def with_alpha(color, alpha):
        new_color = QColor(color)
        new_color.setAlpha(alpha)
        return new_color

    def refresh_location_measurement_overlay_throttled(self, force=False):
        now = time.time()
        if not force and now - self.last_location_overlay_refresh < self.location_overlay_interval:
            return
        self.last_location_overlay_refresh = now
        self.refresh_location_measurement_overlay()

    def refresh_location_measurement_overlay(self):
        if not hasattr(self, "scene"):
            return

        self.clear_measurement_overlay()
        if not self.debug_parse_data:
            return

        for tag in sorted(self.debug_parse_data):
            info = self.debug_parse_data.get(tag)
            if not info or info.get("location_result") != 1:
                continue
            self.draw_location_measurement_overlay(info, self.tag_color_index(tag))

    def draw_location_measurement_overlay(self, info, tag_index):
        anchors = []
        for anchor in info.get("anthor", []):
            anchor_address = int(anchor[0])
            config = self.find_anchor_config(anchor_address)
            if config is None:
                continue
            anchors.append({
                "address": anchor_address,
                "distance": float(anchor[1]),
                "x": float(config["x"]),
                "y": float(config["y"]),
                "z": float(config["z"]),
            })
        if not anchors:
            return

        tag = int(info.get("tag", 0))
        tag_x = float(info.get("location_x", 0))
        tag_y = float(info.get("location_y", 0))
        tag_z = float(info.get("location_z", 0))
        tag_scene_x, tag_scene_y = self.map_scene_point(tag_x, tag_y, tag_z)

        tag_color = QColor(self.gQtColor[tag_index % len(self.gQtColor)])
        circle_color = self.with_alpha(tag_color, 70)
        line_color = self.with_alpha(tag_color, 220)
        label_color = QColor(tag_color)

        circle_pen = QPen(circle_color)
        circle_pen.setWidthF(1.6)
        circle_pen.setStyle(Qt.DashLine)
        line_pen = QPen(line_color)
        line_pen.setWidthF(2.0)
        tag_pen = QPen(QColor("#FFFFFF"), 1.8)

        label_offsets = [
            (12, 8),
            (12, -46),
            (-150, 8),
            (-150, -46),
            (24, 26),
            (-170, 26),
        ]
        label_dx, label_dy = label_offsets[tag_index % len(label_offsets)]

        for anchor in anchors:
            anchor_scene_x, anchor_scene_y = self.map_scene_point(anchor["x"], anchor["y"], anchor["z"])
            if self.measurement_aid_enabled:
                radius = anchor["distance"] * self.ratio
                circle = self.scene.addEllipse(
                    anchor_scene_x - radius,
                    anchor_scene_y - radius,
                    radius * 2,
                    radius * 2,
                    circle_pen,
                )
                self.add_measurement_overlay_item(circle, -6)

                line = self.scene.addLine(anchor_scene_x, anchor_scene_y, tag_scene_x, tag_scene_y, line_pen)
                self.add_measurement_overlay_item(line, 3)

                mid_x = (anchor_scene_x + tag_scene_x) / 2
                mid_y = (anchor_scene_y + tag_scene_y) / 2
                distance_label = self.scene.addText("%0.2fm" % anchor["distance"])
                distance_label.setFont(self.scene_label_font)
                distance_label.setDefaultTextColor(label_color)
                distance_label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
                distance_label.setPos(mid_x + 6, mid_y - 18)
                self.add_measurement_overlay_item(distance_label, 5)

        marker = max(18, self.tag_marker_size + 2)
        tag_marker = self.scene.addEllipse(
            tag_scene_x - marker / 2,
            tag_scene_y - marker / 2,
            marker,
            marker,
            tag_pen,
            QBrush(tag_color),
        )
        self.add_measurement_overlay_item(tag_marker, 8)

        tag_label = self.scene.addText(
            "\u6807\u7b7e 0x%04X\n(%0.2f, %0.2f, %0.2f)" %
            (tag, tag_x, tag_y, tag_z)
        )
        tag_label.setFont(self.scene_label_font)
        tag_label.setDefaultTextColor(label_color)
        tag_label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        tag_label.setPos(tag_scene_x + label_dx, tag_scene_y + label_dy)
        self.add_measurement_overlay_item(tag_label, 9)

    def update_debug_parse_result(self, parse_info):
        try:
            tag = int(parse_info.get("tag", 0))
        except (TypeError, ValueError):
            return
        if tag <= 0:
            return

        parse_info = dict(parse_info)
        parse_info["timestamp"] = time.time()
        is_new_tag = tag not in self.debug_parse_data
        self.debug_parse_data[tag] = parse_info
        self.append_file_log(self.format_parse_log_text(parse_info))
        selected_tag = self.debug_tag_combo.currentData()
        self.refresh_debug_tag_combo(preferred_tag=selected_tag if selected_tag is not None else tag)

        if self.debug_parse_enabled and self.debug_tag_combo.currentData() == tag:
            self.refresh_debug_parse_view()
        self.refresh_location_measurement_overlay_throttled(force=is_new_tag)

    def refresh_debug_tag_combo(self, preferred_tag=None):
        current = preferred_tag
        if current is None:
            current = self.debug_tag_combo.currentData()

        self.debug_tag_combo.blockSignals(True)
        self.debug_tag_combo.clear()
        for tag in sorted(self.debug_parse_data):
            self.debug_tag_combo.addItem("0x%04X" % tag, tag)

        if self.debug_tag_combo.count() > 0:
            index = self.debug_tag_combo.findData(current)
            if index < 0:
                index = 0
            self.debug_tag_combo.setCurrentIndex(index)
        self.debug_tag_combo.blockSignals(False)

    def on_debug_tag_changed(self, index=None):
        if self.debug_parse_enabled:
            self.refresh_debug_parse_view()

    def refresh_debug_parse_view(self):
        tag = self.debug_tag_combo.currentData()
        info = self.debug_parse_data.get(tag)
        if not info:
            self.debug_distance_table.setRowCount(0)
            self.debug_graphics_scene.clear()
            return

        anchors = info.get("anthor", [])
        self.debug_distance_table.setRowCount(len(anchors))
        motion_text = self.format_motion_state(info.get("motion_state", ""))
        for row, anchor in enumerate(anchors):
            anchor_address = int(anchor[0])
            distance = float(anchor[1])
            rssi = anchor[2]
            values = [
                "0x%04X" % tag,
                str(info.get("seq", 0)),
                "0x%04X" % anchor_address,
                "%0.2f" % distance,
                str(rssi),
                motion_text,
            ]
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(value)
                table_item.setTextAlignment(Qt.AlignCenter)
                table_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                self.debug_distance_table.setItem(row, column, table_item)

        self.draw_debug_distance_graph(info)

    def draw_debug_distance_graph(self, info):
        self.debug_graphics_scene.clear()
        viewport_width = max(600, self.debug_graphics_view.viewport().width())
        viewport_height = max(360, self.debug_graphics_view.viewport().height())
        self.debug_graphics_scene.setSceneRect(0, 0, viewport_width, viewport_height)

        anchors = []
        for anchor in info.get("anthor", []):
            anchor_address = int(anchor[0])
            config = self.find_anchor_config(anchor_address)
            if config is None:
                continue
            anchors.append({
                "address": anchor_address,
                "distance": float(anchor[1]),
                "rssi": anchor[2],
                "x": float(config["x"]),
                "y": float(config["y"]),
                "z": float(config["z"]),
            })

        if not anchors:
            message = self.debug_graphics_scene.addText("\u672a\u627e\u5230\u5339\u914d\u7684\u57fa\u7ad9\u914d\u7f6e")
            message.setDefaultTextColor(QColor("#B42318"))
            message.setPos(24, 24)
            return

        location_valid = info.get("location_result") == 1
        tag_x = float(info.get("location_x", 0))
        tag_y = float(info.get("location_y", 0))
        tag_z = float(info.get("location_z", 0))

        points = [(anchor["x"], anchor["y"]) for anchor in anchors]
        if location_valid:
            points.append((tag_x, tag_y))

        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        margin = 72
        scale = min((viewport_width - margin * 2) / span_x, (viewport_height - margin * 2) / span_y) * 0.74
        scale = max(20, scale)
        x_offset = (viewport_width - span_x * scale) / 2 - min_x * scale
        y_offset = (viewport_height + span_y * scale) / 2 + min_y * scale

        def map_point(point_x, point_y):
            return x_offset + point_x * scale, y_offset - point_y * scale

        grid_pen = QPen(QColor("#D4DEE9"))
        grid_pen.setStyle(Qt.DashLine)
        for grid_x in range(int(math.floor(min_x)) - 2, int(math.ceil(max_x)) + 3):
            x1, y1 = map_point(grid_x, min_y - 2)
            x2, y2 = map_point(grid_x, max_y + 2)
            self.debug_graphics_scene.addLine(x1, y1, x2, y2, grid_pen)
        for grid_y in range(int(math.floor(min_y)) - 2, int(math.ceil(max_y)) + 3):
            x1, y1 = map_point(min_x - 2, grid_y)
            x2, y2 = map_point(max_x + 2, grid_y)
            self.debug_graphics_scene.addLine(x1, y1, x2, y2, grid_pen)

        range_pen = QPen(QColor(183, 121, 31, 100))
        range_pen.setWidthF(1.6)
        range_pen.setStyle(Qt.DashLine)
        link_pen = QPen(QColor("#B7791F"))
        link_pen.setWidthF(2.0)
        anchor_pen = QPen(QColor("#FFFFFF"), 1.6)
        tag_pen = QPen(QColor("#FFFFFF"), 1.8)
        anchor_numbers = self.anchor_number_map(globalvar.get_anthor())

        tag_scene_x = tag_scene_y = None
        if location_valid:
            tag_scene_x, tag_scene_y = map_point(tag_x, tag_y)

        for anchor in anchors:
            anchor_x, anchor_y = map_point(anchor["x"], anchor["y"])
            radius = anchor["distance"] * scale
            self.debug_graphics_scene.addEllipse(anchor_x - radius, anchor_y - radius, radius * 2, radius * 2, range_pen)

            if location_valid:
                self.debug_graphics_scene.addLine(anchor_x, anchor_y, tag_scene_x, tag_scene_y, link_pen)
                mid_x = (anchor_x + tag_scene_x) / 2
                mid_y = (anchor_y + tag_scene_y) / 2
                distance_label = self.debug_graphics_scene.addText("%0.2fm" % anchor["distance"])
                distance_label.setDefaultTextColor(QColor("#92400E"))
                distance_label.setPos(mid_x + 6, mid_y - 18)

            marker = 16
            self.debug_graphics_scene.addEllipse(
                anchor_x - marker / 2,
                anchor_y - marker / 2,
                marker,
                marker,
                anchor_pen,
                QBrush(QColor("#2563EB")),
            )
            label = self.debug_graphics_scene.addText(
                "\u57fa\u7ad9%d\n(%0.2f, %0.2f, %0.2f)" %
                (anchor_numbers.get(anchor["address"], 0), anchor["x"], anchor["y"], anchor["z"])
            )
            label.setDefaultTextColor(QColor("#1F2933"))
            label.setPos(anchor_x + 10, anchor_y - 24)

        if location_valid:
            tag_marker = 18
            self.debug_graphics_scene.addEllipse(
                tag_scene_x - tag_marker / 2,
                tag_scene_y - tag_marker / 2,
                tag_marker,
                tag_marker,
                tag_pen,
                QBrush(QColor("#BE123C")),
            )
            tag_label = self.debug_graphics_scene.addText(
                "\u6807\u7b7e 0x%04X\n(%0.2f, %0.2f, %0.2f)" %
                (int(info.get("tag", 0)), tag_x, tag_y, tag_z)
            )
            tag_label.setDefaultTextColor(QColor("#BE123C"))
            tag_label.setPos(tag_scene_x + 12, tag_scene_y + 8)
        else:
            wait_label = self.debug_graphics_scene.addText("\u7b49\u5f85\u6807\u7b7e\u5b9a\u4f4d\u7ed3\u679c")
            wait_label.setDefaultTextColor(QColor("#64748B"))
            wait_label.setPos(24, 24)

    def do_insert_log(self, input_str):
        self.append_file_log("RAW %s" % str(input_str).rstrip("\r\n"))
        if self.enable_log:
            dt = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S] ")
            self.edit_log.appendPlainText(dt + input_str.rstrip("\r\n"))

    def do_btn_start_log(self):
        if self.btn_start.text() == "\u5f00\u59cb":
            self.enable_log = True
            self.btn_start.setText("\u505c\u6b62")
            return
        self.enable_log = False
        self.btn_start.setText("\u5f00\u59cb")

    def do_table_anthor_cellChanged(self, row, column):
        global gAnthor_Node_Configure
        item = self.table_anthor.item(row, column)
        if item is None or row >= len(gAnthor_Node_Configure):
            return

        try:
            if column == 0:
                gAnthor_Node_Configure[row]["enable"] = 1 if item.checkState() == Qt.Checked else 0
            elif column == 1:
                gAnthor_Node_Configure[row]["short_address"] = int(item.text().strip(), 16)
            elif column == 2:
                gAnthor_Node_Configure[row]["x"] = float(item.text().strip())
            elif column == 3:
                gAnthor_Node_Configure[row]["y"] = float(item.text().strip())
            elif column == 4:
                gAnthor_Node_Configure[row]["z"] = float(item.text().strip())
            else:
                return
        except ValueError:
            QMessageBox.warning(self, "输入错误", "基站地址请输入十六进制，坐标请输入数字。")
            self.show_anthor_configure(gAnthor_Node_Configure)
            return

        globalvar.set_anthor(gAnthor_Node_Configure)
        self.scene.clear()
        self.gTag_Result = []
        self.Display_Anthor(gAnthor_Node_Configure)

    def refresh_anthor_status(self):
        for item in gAnthor_Node_Configure:
            qitem = item.get("qt")
            if item["enable"] == 0 or not qitem:
                continue
            if time.time() - item["time"] > 3:
                qitem.setBrush(QBrush(QColor("#60A5FA")))
            else:
                qitem.setBrush(QBrush(QColor("#2563EB")))

    def insert_result(self, input_value):
        location_seq, location_addr, location_x, location_y, location_z, algorithm = input_value
        print("insert result seq=%d addr=%d algorithm=%s" % (location_seq, location_addr, algorithm))
        self.set_algorithm_status(algorithm)
        self.Insert_Tag_Result(location_addr, {"x": location_x, "y": location_y, "z": location_z})

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self,
            "本程序",
            "是否要退出程序？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.stop_file_log()
            self.tcp_server.tcp_close()
            self.serial_server.serial_close()
            event.accept()
        else:
            event.ignore()


    def refresh_serial_ports(self):
        current = self.com_port_combo.currentData()
        self.com_port_combo.clear()

        if serial is None or list_ports is None:
            self.com_port_combo.addItem("pyserial not installed", "")
            self.com_status_label.setText("COM Status:pyserial \u672a\u5b89\u88c5")
            self.set_comm_status_style(self.com_status_label, "closed")
            self.update_comm_controls()
            return

        ports = list(list_ports.comports())
        for port in ports:
            self.com_port_combo.addItem("%s  %s" % (port.device, port.description), port.device)

        if not ports:
            self.com_port_combo.addItem("\u672a\u53d1\u73b0\u4e32\u53e3", "")
            self.com_status_label.setText("COM Status:\u672a\u53d1\u73b0\u4e32\u53e3")
            self.set_comm_status_style(self.com_status_label, "warning")
        else:
            index = self.com_port_combo.findData(current)
            if index >= 0:
                self.com_port_combo.setCurrentIndex(index)
            self.com_status_label.setText("COM Status:\u4e32\u53e3\u6ca1\u6709\u6253\u5f00")
            self.set_comm_status_style(self.com_status_label, "closed")

        self.update_comm_controls()

    def do_tcp_server_start(self):
        if self.pushButton.text() == "OPEN":
            if self.serial_server.is_open():
                QMessageBox.information(self, "COM Connected", "Please close COM before opening TCP.")
                self.update_comm_controls()
                return

            try:
                port = int(self.lineEdit_Port.text())
                if not 1 <= port <= 65535:
                    raise ValueError
                self.label_ip.setText(self.get_local_ip())
                self.tcp_server.tcp_init(port)
            except ValueError:
                QMessageBox.warning(self, "Port Error", "Please enter a TCP port from 1 to 65535.")
                return
            except OSError as exc:
                QMessageBox.warning(self, "TCP Open Failed", str(exc))
                return

            self.lineEdit_Port.setReadOnly(True)
            thread = Thread(target=self.tcp_server.accept_client, daemon=True)
            thread.start()
            self.pushButton.setText("CLOSE")
            self.label_port_state.setText("TCP Status:\u7aef\u53e3\u5df2\u7ecf\u6253\u5f00")
            self.set_comm_status_style(self.label_port_state, "open")
            self.set_connection_status("TCP \u5df2\u8fde\u63a5 %s:%d" % (self.label_ip.text(), port))

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            self.update_comm_controls()
            return

        reply = QMessageBox.question(self, "Confirm", "Close TCP?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.lineEdit_Port.setReadOnly(False)
            self.tcp_server.tcp_close()
            self.pushButton.setText("OPEN")
            self.label_port_state.setText("TCP Status:\u7aef\u53e3\u6ca1\u6709\u6253\u5f00")
            self.set_comm_status_style(self.label_port_state, "closed")
            self.set_connection_status("\u672a\u8fde\u63a5")
            self.do_unlock_Table()
            self.update_comm_controls()

    def do_serial_start(self):
        if self.com_open_button.text() == "OPEN":
            if self.tcp_server.is_open():
                QMessageBox.information(self, "TCP Connected", "Please close TCP before opening COM.")
                self.update_comm_controls()
                return

            port = self.com_port_combo.currentData()
            if not port:
                QMessageBox.warning(self, "COM Error", "Please select an available serial port.")
                self.update_comm_controls()
                return

            try:
                baudrate = int(self.com_baud_combo.currentText())
                self.serial_server.serial_init(port, baudrate)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, "COM Open Failed", str(exc))
                return

            thread = Thread(target=self.serial_server.read_loop, daemon=True)
            thread.start()
            self.com_open_button.setText("CLOSE")
            self.com_status_label.setText("COM Status:%s \u5df2\u6253\u5f00" % port)
            self.set_comm_status_style(self.com_status_label, "open")
            self.set_connection_status("COM \u5df2\u8fde\u63a5 %s @ %d" % (port, baudrate))

            self.gTag_Result = []
            self.scene.clear()
            self.table_tag.clearContents()
            self.Display_Anthor(gAnthor_Node_Configure)
            self.do_lock_Table()
            self.update_comm_controls()
            return

        self.serial_server.serial_close()
        self.com_open_button.setText("OPEN")
        self.com_status_label.setText("COM Status:\u4e32\u53e3\u6ca1\u6709\u6253\u5f00")
        self.set_comm_status_style(self.com_status_label, "closed")
        self.set_connection_status("\u672a\u8fde\u63a5")
        self.do_unlock_Table()
        self.update_comm_controls()


class TCP_SERVER(QtCore.QThread):
    data_result = QtCore.pyqtSignal(object)
    data_draf = QtCore.pyqtSignal(object)
    data_parse = QtCore.pyqtSignal(object)
    algorithm_status = QtCore.pyqtSignal(object)

    def __init__(self):
        super(TCP_SERVER, self).__init__()
        self.g_socket_server = None
        self.socketClosed = True
        self.port = 0
        self.ip = "0.0.0.0"

    def tcp_init(self, port):
        self.tcp_close()
        self.g_socket_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.g_socket_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.g_socket_server.settimeout(0.5)
        self.port = port
        self.ip = HuiTu.get_local_ip()
        self.g_socket_server.bind(("", self.port))
        self.g_socket_server.listen(5)
        self.socketClosed = False
        print("server start, wait for client connecting...")

    @staticmethod
    def extract_packets(buffer):
        return extract_packets(buffer)

    @staticmethod
    def format_raw_data(data):
        if isinstance(data, str):
            return data
        try:
            text = data.decode("ascii")
            if all((ch.isprintable() or ch in "\r\n\t") for ch in text):
                return text
        except UnicodeDecodeError:
            pass
        return "HEX " + " ".join("%02X" % byte for byte in data)

    def message_handle(self, client, info):
        print("client connected: %s" % (info,))
        buffer = b""
        try:
            client.settimeout(0.5)
            client.sendall("connect server successfully!".encode("utf8"))
            while not self.socketClosed:
                try:
                    recv_bytes = client.recv(1024)
                except socket.timeout:
                    continue
                if not recv_bytes:
                    break

                self.data_draf.emit(self.format_raw_data(recv_bytes))
                buffer += recv_bytes
                packets, buffer = self.extract_packets(buffer)

                for packet in packets:
                    parse_error, parse_info = Process_String_Before_Udp(packet)
                    (
                        location_result,
                        location_seq,
                        location_addr,
                        location_x,
                        location_y,
                        location_z,
                        algorithm,
                    ) = twr_main(packet)
                    self.algorithm_status.emit(algorithm)
                    if parse_error == 0:
                        parse_info = dict(parse_info)
                        parse_info.update({
                            "location_result": location_result,
                            "location_x": location_x,
                            "location_y": location_y,
                            "location_z": location_z,
                            "algorithm": algorithm,
                        })
                        self.data_parse.emit(parse_info)
                    if location_result == 1:
                        self.data_result.emit(
                            (location_seq, location_addr, location_x, location_y, location_z, algorithm)
                        )
        except OSError as exc:
            if not self.socketClosed:
                print(exc)
        finally:
            try:
                client.close()
            except OSError:
                pass

    def accept_client(self):
        while not self.socketClosed:
            try:
                client, info = self.g_socket_server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            thread = Thread(target=self.message_handle, args=(client, info), daemon=True)
            thread.start()

    def tcp_close(self):
        self.socketClosed = True
        if self.g_socket_server is not None:
            try:
                self.g_socket_server.close()
            except OSError:
                pass
            self.g_socket_server = None

    def is_open(self):
        return not self.socketClosed and self.g_socket_server is not None


class SERIAL_SERVER(QtCore.QThread):
    data_result = QtCore.pyqtSignal(object)
    data_draf = QtCore.pyqtSignal(object)
    data_parse = QtCore.pyqtSignal(object)
    status_message = QtCore.pyqtSignal(object)
    algorithm_status = QtCore.pyqtSignal(object)

    def __init__(self):
        super(SERIAL_SERVER, self).__init__()
        self.serial_port = None
        self.running = False
        self.port = ""
        self.baudrate = 0

    def serial_init(self, port, baudrate):
        if serial is None:
            raise OSError("pyserial is not installed")

        self.serial_close()
        self.port = port
        self.baudrate = baudrate
        self.serial_port = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.2,
        )
        self.running = True

    def read_loop(self):
        buffer = b""
        self.status_message.emit("COM Status:%s 已打开" % self.port)
        try:
            while self.running and self.serial_port is not None and self.serial_port.is_open:
                try:
                    recv_bytes = self.serial_port.read(1024)
                except (OSError, serial.SerialException) as exc:
                    self.status_message.emit("COM Status:%s" % exc)
                    break

                if not recv_bytes:
                    continue

                self.data_draf.emit(TCP_SERVER.format_raw_data(recv_bytes))
                buffer += recv_bytes
                packets, buffer = extract_packets(buffer)

                for packet in packets:
                    parse_error, parse_info = Process_String_Before_Udp(packet)
                    (
                        location_result,
                        location_seq,
                        location_addr,
                        location_x,
                        location_y,
                        location_z,
                        algorithm,
                    ) = twr_main(packet)
                    self.algorithm_status.emit(algorithm)
                    if parse_error == 0:
                        parse_info = dict(parse_info)
                        parse_info.update({
                            "location_result": location_result,
                            "location_x": location_x,
                            "location_y": location_y,
                            "location_z": location_z,
                            "algorithm": algorithm,
                        })
                        self.data_parse.emit(parse_info)
                    if location_result == 1:
                        self.data_result.emit(
                            (location_seq, location_addr, location_x, location_y, location_z, algorithm)
                        )
        finally:
            self.serial_close()
            self.status_message.emit("COM Status:串口没有打开")

    def serial_close(self):
        self.running = False
        if self.serial_port is not None:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None

    def is_open(self):
        return self.running and self.serial_port is not None and self.serial_port.is_open


def main():
    prepare_windows_gui_process()
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("51UWB TWR Location Tool")
    app.setOrganizationName("51UWB")
    app.setWindowIcon(get_app_icon())
    form = HuiTu()
    form.show_anthor_configure(gAnthor_Node_Configure)
    form.show()
    app.processEvents()
    form.center_on_screen()
    form.Display_Anthor(gAnthor_Node_Configure)
    auto_tcp_port = os.environ.get("UWB_AUTO_TCP_PORT")
    if auto_tcp_port:
        form.lineEdit_Port.setText(auto_tcp_port)
        QtCore.QTimer.singleShot(500, form.do_tcp_server_start)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
