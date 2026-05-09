# -*- coding: utf-8 -*-
"""Debug log, packet parsing panel, and distance-graph mixin.

The debug tab has two responsibilities:

1. Show/save raw transport data for field troubleshooting.
2. Present parsed distances and a per-tag distance graph without affecting the
   main positioning pipeline.
"""
import datetime
import math
import os
import time
from collections import deque

from PyQt5 import QtCore, QtWidgets
from PyQt5.Qt import Qt
from PyQt5.QtGui import QBrush, QColor, QPainterPath, QPen
from PyQt5.QtWidgets import QAbstractItemView, QMessageBox, QTableWidgetItem

import globalvar


DEBUG_VISIBLE_LOG_MAX_LINES = 1000
DEBUG_UI_LOG_FLUSH_INTERVAL_MS = 100
DEBUG_UI_LOG_FLUSH_MAX_LINES = 80
DEBUG_UI_LOG_PENDING_MAX_LINES = 1000
DEBUG_FILE_LOG_FLUSH_BATCH = 64
DEBUG_RAW_LINE_IDLE_FLUSH_S = 0.45
DEBUG_PARSE_REFRESH_INTERVAL_MS = 100


class DebugPanelMixin:
    """Raw log controls, parse table, measurement overlay, and debug graph."""

    @staticmethod
    def current_log_timestamp():
        """Return a compact millisecond timestamp for debug logs."""
        now = datetime.datetime.now()
        return now.strftime("[%Y-%m-%d %H:%M:%S.") + ("%03d]" % (now.microsecond // 1000))

    def setup_debug_log_flush_timer(self):
        """Create the low-latency buffered log flusher used by the debug tab."""
        self.pending_visible_log_lines = []
        self.pending_file_log_lines = []
        self.visible_raw_log_fragment = ""
        self.visible_raw_log_fragment_time = 0.0
        self.debug_tag_combo_tags = ()
        self.debug_tag_combo_dirty = True
        self.debug_tag_combo_preferred = None
        self.debug_parse_view_dirty = False
        self.edit_log.setMaximumBlockCount(DEBUG_VISIBLE_LOG_MAX_LINES)
        self.debug_log_flush_timer = QtCore.QTimer(self)
        self.debug_log_flush_timer.timeout.connect(self.flush_debug_logs)
        self.debug_log_flush_timer.start(DEBUG_UI_LOG_FLUSH_INTERVAL_MS)
        self.debug_parse_refresh_timer = QtCore.QTimer(self)
        self.debug_parse_refresh_timer.timeout.connect(self.flush_debug_parse_refresh)
        self.debug_parse_refresh_timer.start(DEBUG_PARSE_REFRESH_INTERVAL_MS)

    def flush_debug_logs(self):
        """Flush visible and file logs in small batches to keep the UI responsive."""
        self.flush_stale_visible_fragment()
        self.flush_visible_log_lines()
        self.flush_file_log()

    def queue_visible_log_line(self, line):
        """Queue one visible raw-log line and prevent unbounded backlog growth."""
        if not hasattr(self, "pending_visible_log_lines"):
            self.pending_visible_log_lines = []
        self.pending_visible_log_lines.append(line)
        if len(self.pending_visible_log_lines) > DEBUG_UI_LOG_PENDING_MAX_LINES:
            del self.pending_visible_log_lines[:-DEBUG_UI_LOG_PENDING_MAX_LINES]

    def queue_timestamped_visible_line(self, line):
        """Queue one timestamped raw-log line for the visible debug window."""
        self.queue_visible_log_line("%s %s" % (self.current_log_timestamp(), line))

    def queue_text_raw_log(self, raw_text):
        """Buffer text serial/TCP fragments until a complete line is available."""
        if not hasattr(self, "visible_raw_log_fragment"):
            self.visible_raw_log_fragment = ""
            self.visible_raw_log_fragment_time = 0.0

        text = self.visible_raw_log_fragment + raw_text
        self.visible_raw_log_fragment = ""
        self.visible_raw_log_fragment_time = 0.0

        for part in text.splitlines(keepends=True):
            if part.endswith(("\r", "\n")):
                clean_line = part.rstrip("\r\n")
                if clean_line:
                    self.queue_timestamped_visible_line(clean_line)
            else:
                self.visible_raw_log_fragment = part
                self.visible_raw_log_fragment_time = time.monotonic()

    def flush_stale_visible_fragment(self, force=False):
        """Show an incomplete raw-log line after it has been idle for a short time."""
        fragment = getattr(self, "visible_raw_log_fragment", "")
        if not fragment:
            return

        fragment_time = float(getattr(self, "visible_raw_log_fragment_time", 0.0))
        if not force and time.monotonic() - fragment_time < DEBUG_RAW_LINE_IDLE_FLUSH_S:
            return

        self.queue_timestamped_visible_line(fragment)
        self.visible_raw_log_fragment = ""
        self.visible_raw_log_fragment_time = 0.0

    def flush_visible_log_lines(self):
        """Append queued raw-log lines to the QPlainTextEdit in one UI operation."""
        if not getattr(self, "pending_visible_log_lines", None):
            return

        lines = self.pending_visible_log_lines[:DEBUG_UI_LOG_FLUSH_MAX_LINES]
        del self.pending_visible_log_lines[:len(lines)]
        self.edit_log.appendPlainText("\n".join(lines))
        scroll_bar = self.edit_log.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def do_btn_clear(self):
        """Clear the visible raw log window."""
        if hasattr(self, "pending_visible_log_lines"):
            self.pending_visible_log_lines.clear()
        self.visible_raw_log_fragment = ""
        self.visible_raw_log_fragment_time = 0.0
        self.edit_log.clear()

    def do_btn_save_log(self):
        """Toggle file logging for newly received raw and parsed data."""
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
        """Close the active debug log file and reset the log button."""
        if self.file_log_enabled:
            self.append_file_log("LOG STOP")
            self.flush_file_log()
        self.file_log_enabled = False
        self.btn_save_log.setText("\u65e5\u5fd7")

    def append_file_log(self, text):
        """Queue timestamped lines for the active log file if enabled."""
        if not self.file_log_enabled or not self.file_log_path:
            return

        if not hasattr(self, "pending_file_log_lines"):
            self.pending_file_log_lines = []
        for line in str(text).splitlines() or [""]:
            self.pending_file_log_lines.append("%s %s" % (self.current_log_timestamp(), line))
        if len(self.pending_file_log_lines) >= DEBUG_FILE_LOG_FLUSH_BATCH:
            self.flush_file_log()

    def flush_file_log(self):
        """Write queued file-log lines in a batch to avoid high-frequency disk I/O."""
        if not getattr(self, "pending_file_log_lines", None) or not self.file_log_path:
            return

        lines = self.pending_file_log_lines[:]
        self.pending_file_log_lines.clear()
        try:
            with open(self.file_log_path, "a", encoding="utf-8") as log_file:
                log_file.write("\n".join(lines) + "\n")
        except OSError as exc:
            self.file_log_enabled = False
            self.btn_save_log.setText("\u65e5\u5fd7")
            QMessageBox.warning(self, "Log Failed", str(exc))

    def format_parse_log_text(self, parse_info):
        """Convert parsed distances into one human-readable log line."""
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
        """Show or hide the parsed-distance split panel."""
        self.debug_parse_enabled = not self.debug_parse_enabled
        self.debug_parse_panel.setVisible(self.debug_parse_enabled)
        self.btn_parse.setText("\u5173\u95ed\u89e3\u6790" if self.debug_parse_enabled else "\u89e3\u6790")
        if self.debug_parse_enabled:
            splitter_width = max(1, self.debug_splitter.width())
            splitter_height = max(1, self.debug_parse_splitter.height())
            self.debug_splitter.setSizes([
                max(320, int(splitter_width * 0.50)),
                max(320, int(splitter_width * 0.50)),
            ])
            self.debug_parse_splitter.setSizes([
                max(120, int(splitter_height * 0.35)),
                max(280, int(splitter_height * 0.65)),
            ])
            self.refresh_debug_tag_combo()
            self.refresh_debug_parse_view()

    def mark_debug_parse_refresh(self, preferred_tag=None, tag_set_changed=False):
        """Request one throttled refresh of debug parse widgets."""
        if tag_set_changed:
            self.debug_tag_combo_dirty = True
        if preferred_tag is not None:
            self.debug_tag_combo_preferred = preferred_tag
        self.debug_parse_view_dirty = True

    def flush_debug_parse_refresh(self):
        """Refresh debug parse widgets at a fixed UI rate during data bursts."""
        if not self.debug_parse_enabled:
            return

        if self.debug_tag_combo_dirty:
            self.refresh_debug_tag_combo(preferred_tag=self.debug_tag_combo_preferred)
            self.debug_tag_combo_dirty = False
            self.debug_tag_combo_preferred = None

        if self.debug_parse_view_dirty:
            self.debug_parse_view_dirty = False
            self.refresh_debug_parse_view()

    @staticmethod
    def format_motion_state(motion_state):
        """Return a display string for IMU motion state."""
        if motion_state == "s":
            return "\u9759\u6b62"
        if motion_state == "m":
            return "\u8fd0\u52a8"
        return "-"

    @staticmethod
    def find_anchor_config(anchor_address):
        """Find anchor configuration by address."""
        for item in globalvar.get_anthor():
            if item["short_address"] == anchor_address:
                return item
        return None

    def clear_measurement_overlay(self, tag=None):
        """Remove all measurement overlay items or only one tag group."""
        def group_items(group):
            if isinstance(group, dict):
                return list(group.values())
            return list(group)

        if tag is None:
            items = []
            for group in getattr(self, "measurement_overlay_by_tag", {}).values():
                items.extend(group_items(group))
            self.measurement_overlay_by_tag = {}
        else:
            items = group_items(getattr(self, "measurement_overlay_by_tag", {}).pop(tag, {}))

        for item in items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass

        if tag is None:
            self.measurement_overlay_items = []
        else:
            stale_ids = {id(item) for item in items}
            self.measurement_overlay_items = [
                item for item in getattr(self, "measurement_overlay_items", [])
                if id(item) not in stale_ids
            ]

    def add_measurement_overlay_item(self, item, z_value=None, tag=None):
        """Track a scene overlay item for later tag-scoped cleanup."""
        if z_value is not None:
            item.setZValue(z_value)
        self.measurement_overlay_items.append(item)
        if tag is not None and not isinstance(self.measurement_overlay_by_tag.get(tag), dict):
            self.measurement_overlay_by_tag.setdefault(tag, []).append(item)
        return item

    def get_measurement_overlay_item(self, tag, key, factory, z_value):
        """Return a persistent overlay item for ``tag`` and ``key``."""
        group = self.measurement_overlay_by_tag.setdefault(tag, {})
        if not isinstance(group, dict):
            self.clear_measurement_overlay(tag)
            group = self.measurement_overlay_by_tag.setdefault(tag, {})

        item = group.get(key)
        if item is None:
            item = factory()
            item.setZValue(z_value)
            group[key] = item
            self.measurement_overlay_items.append(item)
        item.setVisible(True)
        return item

    def hide_unused_measurement_overlay_items(self, tag, used_keys):
        """Hide stale overlay items that were not touched in the current refresh."""
        group = self.measurement_overlay_by_tag.get(tag, {})
        if not isinstance(group, dict):
            return
        for key, item in group.items():
            if key not in used_keys:
                item.setVisible(False)

    def cleanup_debug_parse_data(self, now=None):
        """Drop stale or excess tag parse snapshots and their scene overlays."""
        if not self.debug_parse_data:
            return False

        now = time.time() if now is None else now
        removed = False
        stale_tags = [
            tag for tag, info in self.debug_parse_data.items()
            if now - float(info.get("timestamp", 0.0)) > self.debug_parse_ttl_seconds
        ]
        for tag in stale_tags:
            self.debug_parse_data.pop(tag, None)
            self.debug_distance_history.pop(tag, None)
            self.clear_measurement_overlay(tag)
            removed = True

        if len(self.debug_parse_data) <= self.max_debug_tags:
            return removed

        sorted_tags = sorted(
            self.debug_parse_data,
            key=lambda tag: float(self.debug_parse_data[tag].get("timestamp", 0.0)),
        )
        for tag in sorted_tags[:len(self.debug_parse_data) - self.max_debug_tags]:
            self.debug_parse_data.pop(tag, None)
            self.debug_distance_history.pop(tag, None)
            self.clear_measurement_overlay(tag)
            removed = True
        return removed

    def append_debug_distance_history(self, tag, parse_info, now):
        """Append parsed anchor distances to the selected tag's trend history."""
        tag_history = self.debug_distance_history.setdefault(tag, {})
        cutoff = now - self.debug_distance_history_seconds

        for anchor in parse_info.get("anthor", []):
            try:
                anchor_address = int(anchor[0])
                distance = float(anchor[1])
            except (TypeError, ValueError, IndexError):
                continue
            rssi = anchor[2] if len(anchor) > 2 else 0
            series = tag_history.setdefault(anchor_address, deque())
            if not isinstance(series, deque):
                series = deque(series)
                tag_history[anchor_address] = series
            series.append({
                "time": now,
                "distance": distance,
                "rssi": rssi,
                "seq": parse_info.get("seq", 0),
            })
            while series and series[0]["time"] < cutoff:
                series.popleft()

        for anchor_address in list(tag_history):
            series = tag_history[anchor_address]
            while series and series[0]["time"] < cutoff:
                series.popleft()
            if not series:
                tag_history.pop(anchor_address, None)

    def tag_color_index(self, tag):
        """Choose a stable color index for a visible tag."""
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
        """Return a copy of ``color`` with a changed alpha channel."""
        new_color = QColor(color)
        new_color.setAlpha(alpha)
        return new_color

    def refresh_location_measurement_overlay_throttled(self, force=False):
        """Throttle full overlay refreshes during high-rate packet bursts."""
        now = time.time()
        if not force and now - self.last_location_overlay_refresh < self.location_overlay_interval:
            return
        self.last_location_overlay_refresh = now
        self.refresh_location_measurement_overlay()

    def refresh_location_measurement_overlay_for_tag(self, tag, force=False):
        """Update only one tag's main-canvas measurement overlay."""
        now = time.time()
        if not force and now - self.last_location_overlay_refresh < self.location_overlay_interval:
            return
        self.last_location_overlay_refresh = now

        info = self.debug_parse_data.get(tag)
        if info and info.get("location_result") == 1:
            self.draw_location_measurement_overlay(info, self.tag_color_index(tag))
        else:
            self.clear_measurement_overlay(tag)

    def refresh_location_measurement_overlay(self):
        """Rebuild main-canvas measurement overlays for all live tags."""
        if not hasattr(self, "scene"):
            return

        self.clear_measurement_overlay()
        self.cleanup_debug_parse_data()
        if not self.debug_parse_data:
            return

        for tag in sorted(self.debug_parse_data):
            info = self.debug_parse_data.get(tag)
            if not info or info.get("location_result") != 1:
                continue
            self.draw_location_measurement_overlay(info, self.tag_color_index(tag))

    def draw_location_measurement_overlay(self, info, tag_index):
        """Draw range circles, anchor-tag lines, and tag marker on main canvas."""
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
            tag = int(info.get("tag", 0))
            self.clear_measurement_overlay(tag)
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
        used_keys = set()

        for anchor in anchors:
            anchor_scene_x, anchor_scene_y = self.map_scene_point(anchor["x"], anchor["y"], anchor["z"])
            if self.measurement_aid_enabled:
                radius = anchor["distance"] * self.ratio
                circle_key = ("circle", anchor["address"])
                circle = self.get_measurement_overlay_item(
                    tag,
                    circle_key,
                    lambda: self.scene.addEllipse(0, 0, 0, 0),
                    -6,
                )
                circle.setRect(anchor_scene_x - radius, anchor_scene_y - radius, radius * 2, radius * 2)
                circle.setPen(circle_pen)
                circle.setBrush(QBrush(Qt.NoBrush))
                used_keys.add(circle_key)

                line_key = ("line", anchor["address"])
                line = self.get_measurement_overlay_item(
                    tag,
                    line_key,
                    lambda: self.scene.addLine(0, 0, 0, 0),
                    3,
                )
                line.setLine(anchor_scene_x, anchor_scene_y, tag_scene_x, tag_scene_y)
                line.setPen(line_pen)
                used_keys.add(line_key)

                mid_x = (anchor_scene_x + tag_scene_x) / 2
                mid_y = (anchor_scene_y + tag_scene_y) / 2
                label_key = ("distance_label", anchor["address"])
                distance_label = self.get_measurement_overlay_item(
                    tag,
                    label_key,
                    lambda: self.scene.addText(""),
                    5,
                )
                distance_label.setPlainText("%0.2fm" % anchor["distance"])
                distance_label.setFont(self.scene_label_font)
                distance_label.setDefaultTextColor(label_color)
                distance_label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
                distance_label.setPos(mid_x + 6, mid_y - 18)
                used_keys.add(label_key)

        marker = max(18, self.tag_marker_size + 2)
        tag_marker_key = ("tag_marker", tag)
        tag_marker = self.get_measurement_overlay_item(
            tag,
            tag_marker_key,
            lambda: self.scene.addEllipse(0, 0, 0, 0),
            8,
        )
        tag_marker.setRect(tag_scene_x - marker / 2, tag_scene_y - marker / 2, marker, marker)
        tag_marker.setPen(tag_pen)
        tag_marker.setBrush(QBrush(tag_color))
        used_keys.add(tag_marker_key)

        tag_label_key = ("tag_label", tag)
        tag_label = self.get_measurement_overlay_item(
            tag,
            tag_label_key,
            lambda: self.scene.addText(""),
            9,
        )
        tag_label.setPlainText(
            "\u6807\u7b7e 0x%04X\n(%0.2f, %0.2f, %0.2f)" %
            (tag, tag_x, tag_y, tag_z)
        )
        tag_label.setFont(self.scene_label_font)
        tag_label.setDefaultTextColor(label_color)
        tag_label.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        tag_label.setPos(tag_scene_x + label_dx, tag_scene_y + label_dy)
        used_keys.add(tag_label_key)
        self.hide_unused_measurement_overlay_items(tag, used_keys)

    def update_debug_parse_result(self, parse_info):
        """Receive parsed packet data from TCP/COM services and refresh debug UI."""
        if isinstance(parse_info, list):
            for item in parse_info:
                self.update_debug_parse_result(item)
            return

        try:
            tag = int(parse_info.get("tag", 0))
        except (TypeError, ValueError):
            return
        if tag <= 0:
            return

        parse_info = dict(parse_info)
        now = time.time()
        removed_tags = self.cleanup_debug_parse_data(now)
        parse_info["timestamp"] = now
        is_new_tag = tag not in self.debug_parse_data
        self.debug_parse_data[tag] = parse_info
        self.append_debug_distance_history(tag, parse_info, now)
        self.append_file_log(self.format_parse_log_text(parse_info))
        selected_tag = self.debug_tag_combo.currentData()
        tag_set_changed = bool(is_new_tag or removed_tags)
        preferred_tag = selected_tag if selected_tag is not None else tag
        self.mark_debug_parse_refresh(preferred_tag=preferred_tag, tag_set_changed=tag_set_changed)
        self.refresh_location_measurement_overlay_for_tag(tag, force=is_new_tag)

    def refresh_debug_tag_combo(self, preferred_tag=None):
        """Refresh the tag selector while preserving the preferred selection."""
        current = preferred_tag
        if current is None:
            current = self.debug_tag_combo.currentData()

        tags = tuple(sorted(self.debug_parse_data))
        if tags == getattr(self, "debug_tag_combo_tags", ()) and self.debug_tag_combo.findData(current) >= 0:
            self.debug_tag_combo.setCurrentIndex(self.debug_tag_combo.findData(current))
            return

        self.debug_tag_combo.blockSignals(True)
        self.debug_tag_combo.clear()
        for tag in tags:
            self.debug_tag_combo.addItem("0x%04X" % tag, tag)

        if self.debug_tag_combo.count() > 0:
            index = self.debug_tag_combo.findData(current)
            if index < 0:
                index = 0
            self.debug_tag_combo.setCurrentIndex(index)
        self.debug_tag_combo.blockSignals(False)
        self.debug_tag_combo_tags = tags

    def on_debug_tag_changed(self, index=None):
        """Refresh parsed views when the selected debug tag changes."""
        if self.debug_parse_enabled:
            self.debug_parse_view_dirty = False
            self.refresh_debug_parse_view()

    def refresh_debug_parse_view(self):
        """Refresh the parsed-distance table and selected-tag distance graph."""
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
        """Draw a last-20s distance trend for the selected tag.

        The left table keeps showing the latest parsed frame. The right plot is
        a rolling time-series view: every enabled anchor has one colored line,
        with x = time in the last window and y = tag-anchor distance.
        """
        self.debug_graphics_scene.clear()
        viewport_width = max(260, self.debug_graphics_view.viewport().width() - 4)
        viewport_height = max(220, self.debug_graphics_view.viewport().height() - 4)
        self.debug_graphics_scene.setSceneRect(0, 0, viewport_width, viewport_height)

        tag = int(info.get("tag", 0))
        now = float(info.get("timestamp", time.time()))
        window_seconds = float(getattr(self, "debug_distance_history_seconds", 20.0))
        start_time = now - window_seconds
        tag_history = getattr(self, "debug_distance_history", {}).get(tag, {})
        anchor_numbers = self.anchor_number_map(globalvar.get_anthor())

        current_by_anchor = {}
        for anchor in info.get("anthor", []):
            try:
                current_by_anchor[int(anchor[0])] = {
                    "distance": float(anchor[1]),
                    "rssi": anchor[2] if len(anchor) > 2 else 0,
                }
            except (TypeError, ValueError, IndexError):
                continue

        visible_series = {}
        all_distances = []
        for anchor_address in sorted(set(tag_history) | set(current_by_anchor)):
            history_series = tag_history.get(anchor_address, [])
            series = [
                point for point in history_series
                if start_time <= float(point.get("time", 0.0)) <= now
            ]
            if not series and anchor_address in current_by_anchor:
                series = [{
                    "time": now,
                    "distance": current_by_anchor[anchor_address]["distance"],
                    "rssi": current_by_anchor[anchor_address]["rssi"],
                    "seq": info.get("seq", 0),
                }]
            if not series:
                continue
            visible_series[anchor_address] = series
            all_distances.extend(float(point["distance"]) for point in series)

        if not visible_series or not all_distances:
            message = self.debug_graphics_scene.addText("等待距离趋势数据")
            message.setDefaultTextColor(QColor("#6A7890"))
            message.setPos(24, 24)
            return

        chart_left = 72
        chart_top = 82
        chart_right = viewport_width - 18
        chart_bottom = viewport_height - 48
        chart_width = max(1, chart_right - chart_left)
        chart_height = max(1, chart_bottom - chart_top)

        min_distance = min(all_distances)
        max_distance = max(all_distances)
        if math.isclose(min_distance, max_distance, abs_tol=1e-6):
            padding = max(0.05, max_distance * 0.05)
        else:
            padding = max(0.05, (max_distance - min_distance) * 0.16)
        y_min = max(0.0, min_distance - padding)
        y_max = max_distance + padding
        if math.isclose(y_min, y_max, abs_tol=1e-6):
            y_max = y_min + 0.1

        def map_x(point_time):
            """Map timestamp to the fixed last-20s chart x coordinate."""
            ratio = (float(point_time) - start_time) / window_seconds
            return chart_left + max(0.0, min(1.0, ratio)) * chart_width

        def map_y(distance):
            """Map distance in meters to the chart y coordinate."""
            ratio = (float(distance) - y_min) / (y_max - y_min)
            return chart_bottom - max(0.0, min(1.0, ratio)) * chart_height

        background_pen = QPen(QColor("#C5D1DE"))
        background_pen.setWidthF(1.2)
        self.debug_graphics_scene.addRect(
            12,
            10,
            viewport_width - 24,
            viewport_height - 22,
            QPen(QColor("#C5D1DE")),
            QBrush(QColor("#F8FAFD")),
        )
        self.debug_graphics_scene.addRect(
            chart_left,
            chart_top,
            chart_width,
            chart_height,
            background_pen,
            QBrush(QColor("#FFFFFF")),
        )

        title = self.debug_graphics_scene.addText("距离趋势")
        title.setDefaultTextColor(QColor("#17324D"))
        title.setPos(18, 14)

        grid_pen = QPen(QColor("#D8E1EC"))
        grid_pen.setWidthF(1.0)
        axis_pen = QPen(QColor("#C5D1DE"))
        axis_pen.setWidthF(1.2)
        label_color = QColor("#5F6F82")

        for index in range(4):
            ratio = index / 3.0
            distance = y_max - ratio * (y_max - y_min)
            y_value = chart_top + ratio * chart_height
            self.debug_graphics_scene.addLine(chart_left, y_value, chart_right, y_value, grid_pen)
            axis_label = self.debug_graphics_scene.addText("%.2fm" % distance)
            axis_label.setDefaultTextColor(label_color)
            axis_label.setPos(14, y_value - 12)

        for seconds_ago in (20, 15, 10, 5, 0):
            x_value = chart_left + (window_seconds - seconds_ago) / window_seconds * chart_width
            self.debug_graphics_scene.addLine(x_value, chart_top, x_value, chart_bottom, grid_pen)
            text = "now" if seconds_ago == 0 else "-%ds" % seconds_ago
            x_label = self.debug_graphics_scene.addText(text)
            x_label.setDefaultTextColor(label_color)
            x_label.setPos(x_value - 14, chart_bottom + 12)

        self.debug_graphics_scene.addLine(chart_left, chart_bottom, chart_right, chart_bottom, axis_pen)
        self.debug_graphics_scene.addLine(chart_left, chart_top, chart_left, chart_bottom, axis_pen)
        y_title = self.debug_graphics_scene.addText("Distance")
        y_title.setDefaultTextColor(QColor("#162033"))
        y_title.setPos(18, 50)
        x_title = self.debug_graphics_scene.addText("Last 20s")
        x_title.setDefaultTextColor(label_color)
        x_title.setPos(chart_left, chart_bottom + 32)

        legend_x = chart_left
        legend_y = 58
        legend_columns = min(4, max(1, len(visible_series)))
        legend_col_width = max(1, int((chart_width - 8) / legend_columns))
        for index, anchor_address in enumerate(sorted(visible_series)):
            color = QColor(self.gQtColor[index % len(self.gQtColor)])
            color.setAlpha(235)
            pen = QPen(color)
            pen.setWidthF(2.4)

            path = QPainterPath()
            first_point = True
            for point in visible_series[anchor_address]:
                point_x = map_x(point["time"])
                point_y = map_y(point["distance"])
                if first_point:
                    path.moveTo(point_x, point_y)
                    first_point = False
                else:
                    path.lineTo(point_x, point_y)
            self.debug_graphics_scene.addPath(path, pen)

            last_point = visible_series[anchor_address][-1]
            end_x = map_x(last_point["time"])
            end_y = map_y(last_point["distance"])
            self.debug_graphics_scene.addEllipse(
                end_x - 4,
                end_y - 4,
                8,
                8,
                QPen(QColor("#FFFFFF"), 1.2),
                QBrush(color),
            )

            legend_col = index % legend_columns
            legend_row = index // legend_columns
            item_x = legend_x + legend_col * legend_col_width
            item_y = legend_y + legend_row * 18
            self.debug_graphics_scene.addLine(item_x, item_y + 8, item_x + 22, item_y + 8, pen)
            legend_text = self.debug_graphics_scene.addText(
                "基站%d %.2fm" %
                (anchor_numbers.get(anchor_address, anchor_address), last_point["distance"])
            )
            legend_text.setDefaultTextColor(QColor("#162033"))
            legend_text.setPos(item_x + 28, item_y - 2)

    def do_insert_log(self, input_str):
        """Append raw transport text to file/visible log according to switches."""
        raw_text = str(input_str)
        self.append_file_log("RAW %s" % raw_text.rstrip("\r\n"))
        if self.enable_log:
            if raw_text.startswith("HEX "):
                self.queue_timestamped_visible_line(raw_text.rstrip("\r\n"))
            else:
                self.queue_text_raw_log(raw_text)

    def do_btn_start_log(self):
        """Start or stop appending raw data to the visible debug window."""
        if self.btn_start.text() == "\u5f00\u59cb":
            self.enable_log = True
            self.btn_start.setText("\u505c\u6b62")
            return
        self.flush_stale_visible_fragment(force=True)
        self.flush_visible_log_lines()
        self.enable_log = False
        self.btn_start.setText("\u5f00\u59cb")

