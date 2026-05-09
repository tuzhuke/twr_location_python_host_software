# -*- coding: utf-8 -*-
import sys
import os
from threading import Thread

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication

from twr_51uwb_v2 import (
    HuiTu,
    gAnthor_Node_Configure,
    get_app_icon,
    set_windows_app_id,
)


def main():
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

    def open_tcp():
        port = int(os.environ.get("UWB_TEST_TCP_PORT", "19000"))
        form.lineEdit_Port.setText(str(port))
        form.tcp_server.tcp_init(port)
        thread = Thread(target=form.tcp_server.accept_client, daemon=True)
        thread.start()
        form.lineEdit_Port.setReadOnly(True)
        form.pushButton.setText("CLOSE")
        form.label_port_state.setText("TCP Status:端口已经打开")
        form.set_comm_status_style(form.label_port_state, "open")
        form.set_connection_status("TCP 已连接 %s:%d" % (form.label_ip.text(), port))
        form.update_comm_controls()
        form.raise_()
        form.activateWindow()

    QtCore.QTimer.singleShot(800, open_tcp)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
