# -*- coding: utf-8 -*-
"""Main positioning canvas and anchor/tag drawing mixin.

The plot is rendered with QGraphicsScene. Model coordinates are meters in the
UWB coordinate system; ``map_scene_point`` projects them into scene pixels.
Labels ignore view transforms so zooming changes geometry but keeps text
readable.
"""
import math
import time

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.Qt import Qt
from PyQt5.QtGui import QBrush, QColor, QPen, QPixmap
from PyQt5.QtWidgets import QGraphicsEllipseItem, QGraphicsLineItem, QMessageBox, QTableWidgetItem

import globalvar
from uwb_logging import get_logger


gAnthor_Node_Configure = globalvar.get_anthor()
logger = get_logger(__name__)


class PlotMixin:
    """Anchor/tag drawing, 2D/3D projection, zoom, pan, and resize redraw."""

    def resizeEvent(self, event):
        """Debounce expensive scene redraws when the user resizes the window."""
        super().resizeEvent(event)
        if not hasattr(self, "scene") or not hasattr(self, "graphicsView"):
            return

        if not hasattr(self, "_resize_redraw_timer"):
            self._resize_redraw_timer = QtCore.QTimer(self)
            self._resize_redraw_timer.setSingleShot(True)
            self._resize_redraw_timer.timeout.connect(self.redraw_scene_after_resize)
        self._resize_redraw_timer.start(120)

    def redraw_scene_after_resize(self):
        """Recompute grid and item positions after the viewport size changes."""
        if not hasattr(self, "gTag_Result") or not hasattr(self, "table_tag"):
            return
        self.redraw_scene_keep_tags()

    def eventFilter(self, source, event):
        """Handle mouse-wheel zoom on the main graphics viewport."""
        if source is self.graphicsView.viewport() and event.type() == QtCore.QEvent.Wheel:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            return True
        return super().eventFilter(source, event)

    def update_zoom_label(self):
        """Refresh the toolbar zoom percentage label."""
        if hasattr(self, "zoom_label"):
            self.zoom_label.setText("%d%%" % round(self.zoom_factor * 100))

    def set_zoom(self, target_zoom):
        """Apply bounded view zoom while keeping scene text size stable."""
        target_zoom = max(self.zoom_min, min(self.zoom_max, target_zoom))
        if abs(target_zoom - self.zoom_factor) < 0.001:
            return
        scale_factor = target_zoom / self.zoom_factor
        self.zoom_factor = target_zoom
        self.graphicsView.scale(scale_factor, scale_factor)
        self.update_zoom_label()

    def zoom_in(self):
        """Increase canvas zoom by one configured step."""
        self.set_zoom(self.zoom_factor * self.zoom_step)

    def zoom_out(self):
        """Decrease canvas zoom by one configured step."""
        self.set_zoom(self.zoom_factor / self.zoom_step)

    def zoom_reset(self):
        """Reset zoom transform and recenter the scene."""
        self.graphicsView.resetTransform()
        self.zoom_factor = 1.0
        self.graphicsView.centerOn(self.scene_center_x, self.scene_center_y)
        self.update_zoom_label()

    def on_view_angle_changed(self, value):
        """Apply a changed 3D view angle and redraw if 3D is active."""
        self.view_angle_deg = value
        if self.display_mode != "3D":
            self.view_angle_label.setText("2D")
            return
        self.view_angle_label.setText("%d deg" % value)
        self.redraw_scene_keep_tags()

    def reset_view_angle(self):
        """Restore the 3D view angle slider to its default value."""
        if self.display_mode != "3D":
            return
        self.view_angle_slider.setValue(35)

    def update_view_angle_controls(self):
        """Enable 3D angle controls only when the canvas is in 3D mode."""
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
        """Toggle range circles and anchor-tag distance helper lines."""
        self.measurement_aid_enabled = not self.measurement_aid_enabled
        if hasattr(self, "measurement_toggle_button"):
            self.measurement_toggle_button.setText(
                "\u9690\u85cf\u6d4b\u8ddd" if self.measurement_aid_enabled else "\u663e\u793a\u6d4b\u8ddd"
            )
        self.refresh_location_measurement_overlay_throttled(force=True)

    def redraw_scene_keep_tags(self):
        """Redraw anchors/grid while preserving the latest visible tag positions."""
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

    def compute_ratio(self, width, height, anthor_node_configure):
        """Calculate meters-to-pixels ratio for the current viewport and anchors."""
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
        fill_ratio = 0.82
        ratio = max(1, int(min(width * fill_ratio / span_x, height * fill_ratio / span_y)))

        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        self.plot_origin_x = width / 2.0 - center_x * ratio
        self.plot_origin_y = height / 2.0 + center_y * ratio
        return ratio

    def is_3d_anchor_config(self, anthor_node_configure):
        """Return True when at least four enabled anchors have non-coplanar z."""
        enabled = [item for item in anthor_node_configure if item["enable"] == 1]
        if len(enabled) < 4:
            return False
        first_z = enabled[0]["z"]
        return any(abs(item["z"] - first_z) > 1e-6 for item in enabled[1:])

    def project_model_point(self, point_x, point_y, point_z=0):
        """Project model coordinates to 2D drawing coordinates."""
        if self.display_mode == "3D":
            angle = math.radians(self.view_angle_deg)
            return point_x + point_y * math.cos(angle), point_y * math.sin(angle) + point_z * self.z_scale
        return point_x, point_y

    def map_scene_point(self, point_x, point_y, point_z=0):
        """Map projected model coordinates into QGraphicsScene pixel space."""
        projected_x, projected_y = self.project_model_point(point_x, point_y, point_z)
        return (
            int(self.plot_origin_x + projected_x * self.ratio),
            int(self.plot_origin_y - projected_y * self.ratio),
        )

    @staticmethod
    def anchor_number_map(anthor_node_configure):
        """Map enabled anchor addresses to stable user-facing base-station numbers."""
        enabled = [item for item in anthor_node_configure if item["enable"] == 1]
        sorted_anchors = sorted(enabled, key=lambda item: item["short_address"])
        return {item["short_address"]: index + 1 for index, item in enumerate(sorted_anchors)}

    def Display_Anthor(self, anthor_node_configure):
        """Redraw anchors, grid, and optional 3D helper stems."""
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
                qitem.setBrush(QBrush(QColor("#4F88B5")))
            else:
                qitem.setBrush(QBrush(QColor("#155F8C")))
            qitem.setPen(QPen(QColor("#FFFFFF"), 1.5))

            ground_x, ground_y = self.map_scene_point(item["x"], item["y"], 0)
            point_x, point_y = self.map_scene_point(item["x"], item["y"], item["z"])
            if self.display_mode == "3D":
                stem = QGraphicsLineItem(ground_x, ground_y, point_x, point_y)
                stem_pen = QPen(QColor("#6B7D90"))
                stem_pen.setWidthF(1.8)
                stem_pen.setStyle(Qt.DashLine)
                stem.setPen(stem_pen)
                self.scene.addItem(stem)

            qitem.setPos(point_x, point_y)
            self.scene.addItem(qitem)

            anchor_name = "基站%d" % anchor_numbers.get(item["short_address"], 0)
            label_html = (
                '<span style="color:#142033;">%s</span>'
                '<span style="color:#2D6F9F;">（</span>'
                '<span style="color:#142033;">%s</span>'
                '<span style="color:#2D6F9F;">, </span>'
                '<span style="color:#142033;">%s</span>'
                '<span style="color:#2D6F9F;">, </span>'
                '<span style="color:#142033;">%s</span>'
                '<span style="color:#2D6F9F;">）</span>'
            ) % (anchor_name, item["x"], item["y"], item["z"])
            label = self.scene.addText("")
            label.setFont(self.scene_label_font)
            label.setHtml(label_html)
            label.setDefaultTextColor(QColor("#142033"))
            label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
            label.setPos(point_x + marker / 2 + 8, point_y - marker / 2 - 10)
            item["qt"] = qitem

        pen = QPen()
        pen.setColor(QColor("#8FA1B3"))
        pen.setWidthF(1.45)
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
        """Render anchor configuration rows into the anchor table."""
        self.table_anthor.blockSignals(True)
        for index, item in enumerate(anthor_node_configure):
            if index < 3:
                item["enable"] = 1
            check_container = QtWidgets.QWidget()
            check_layout = QtWidgets.QHBoxLayout(check_container)
            check_layout.setContentsMargins(0, 0, 0, 0)
            check_layout.setAlignment(Qt.AlignCenter)
            check_box = QtWidgets.QCheckBox()
            check_box.setChecked(item["enable"] == 1)
            check_box.setEnabled(index >= 3)
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
        globalvar.set_anthor(anthor_node_configure)
        self.table_anthor.blockSignals(False)

    def set_anchor_checkboxes_enabled(self, enabled):
        """Enable or disable anchor checkboxes without changing their values."""
        for row in range(self.table_anthor.rowCount()):
            widget = self.table_anthor.cellWidget(row, 0)
            if widget is None:
                continue
            checkbox = widget.findChild(QtWidgets.QCheckBox)
            if checkbox is not None:
                checkbox.setEnabled(enabled and row >= 3)

    def do_anchor_enable_changed(self, row, state):
        """Persist one anchor enable checkbox change into global configuration."""
        global gAnthor_Node_Configure
        if row >= len(gAnthor_Node_Configure):
            return
        if row < 3:
            gAnthor_Node_Configure[row]["enable"] = 1
            widget = self.table_anthor.cellWidget(row, 0)
            checkbox = widget.findChild(QtWidgets.QCheckBox) if widget is not None else None
            if checkbox is not None and checkbox.checkState() != Qt.Checked:
                checkbox.blockSignals(True)
                checkbox.setChecked(True)
                checkbox.blockSignals(False)
            globalvar.set_anthor(gAnthor_Node_Configure)
            return
        gAnthor_Node_Configure[row]["enable"] = 1 if state == Qt.Checked else 0
        globalvar.set_anthor(gAnthor_Node_Configure)
        self.scene.clear()
        self.gTag_Result = []
        self.Display_Anthor(gAnthor_Node_Configure)

    def Remove_Tag_Pic(self, item):
        """Remove all scene items belonging to one tracked tag."""
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
        """Update or create a tag marker and its trail on the main canvas."""
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
        """Update one row in the location result table."""
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
        """Insert one positioning result into history, table, and canvas."""
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

    def do_table_anthor_cellChanged(self, row, column):
        """Validate and save edits made in the anchor configuration table."""
        global gAnthor_Node_Configure
        item = self.table_anthor.item(row, column)
        if item is None or row >= len(gAnthor_Node_Configure):
            return

        try:
            if column == 0:
                gAnthor_Node_Configure[row]["enable"] = 1 if item.checkState() == Qt.Checked else 0
            elif column == 1:
                gAnthor_Node_Configure[row]["short_address"] = int(item.text().strip(), 16)
                self.table_anthor.blockSignals(True)
                item.setText("0x%04X" % gAnthor_Node_Configure[row]["short_address"])
                self.table_anthor.blockSignals(False)
            elif column == 2:
                gAnthor_Node_Configure[row]["x"] = float(item.text().strip())
                self.table_anthor.blockSignals(True)
                item.setText("%0.2f" % gAnthor_Node_Configure[row]["x"])
                self.table_anthor.blockSignals(False)
            elif column == 3:
                gAnthor_Node_Configure[row]["y"] = float(item.text().strip())
                self.table_anthor.blockSignals(True)
                item.setText("%0.2f" % gAnthor_Node_Configure[row]["y"])
                self.table_anthor.blockSignals(False)
            elif column == 4:
                gAnthor_Node_Configure[row]["z"] = float(item.text().strip())
                self.table_anthor.blockSignals(True)
                item.setText("%0.2f" % gAnthor_Node_Configure[row]["z"])
                self.table_anthor.blockSignals(False)
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
        """Refresh anchor colors according to recent packet activity."""
        for item in gAnthor_Node_Configure:
            qitem = item.get("qt")
            if item["enable"] == 0 or not qitem:
                continue
            if time.time() - item["time"] > 3:
                qitem.setBrush(QBrush(QColor("#4F88B5")))
            else:
                qitem.setBrush(QBrush(QColor("#155F8C")))

    def insert_result(self, input_value):
        """Receive one located tag result from TCP/COM services."""
        location_seq, location_addr, location_x, location_y, location_z, algorithm = input_value
        logger.debug("insert result seq=%d addr=%d algorithm=%s", location_seq, location_addr, algorithm)
        self.set_algorithm_status(algorithm)
        self.Insert_Tag_Result(location_addr, {"x": location_x, "y": location_y, "z": location_z})

