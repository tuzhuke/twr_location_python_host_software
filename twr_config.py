# -*- coding: utf-8 -*-
"""Persistent application configuration for the UWB-TWR host tool."""
import copy
import json
import os
import sys
from pathlib import Path


CONFIG_VERSION = 1
APP_CONFIG_ENV = "UWB_TWR_CONFIG_FILE"
MAX_ANCHOR_COUNT = 16

DEFAULT_ANCHORS = [
    {"enable": 1, "short_address": 0x0001, "x": 0.0, "y": 0.0, "z": 0.0, "time": 0, "qt": 0},
    {"enable": 1, "short_address": 0x0002, "x": 1.6, "y": 0.0, "z": 0.0, "time": 0, "qt": 0},
    {"enable": 1, "short_address": 0x0003, "x": 1.6, "y": 1.6, "z": 0.0, "time": 0, "qt": 0},
    {"enable": 1, "short_address": 0x0004, "x": 0.0, "y": 1.6, "z": 0.0, "time": 0, "qt": 0},
]

DEFAULT_CONFIG = {
    "version": CONFIG_VERSION,
    "anchor_count": 4,
    "anchors": DEFAULT_ANCHORS,
    "communication": {
        "tcp_port": 8888,
        "com_port": "",
        "baudrate": 115200,
        "default_tab": "COM",
    },
    "display": {
        "history_count": 5,
        "zoom_factor": 1.0,
        "view_angle_deg": 35,
        "measurement_aid_enabled": True,
    },
}


def _clamp(value, lower, upper):
    return max(lower, min(upper, value))


def _to_int(value, default=0):
    try:
        if isinstance(value, str):
            return int(value.strip(), 0)
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
    return default


def get_config_path():
    """Return the per-user JSON config path, overridable for tests."""
    env_path = os.environ.get(APP_CONFIG_ENV)
    if env_path:
        return Path(env_path)

    if sys.platform == "win32":
        root = os.environ.get("APPDATA")
        if not root:
            root = str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "LandianUWB" / "TWRLocationTool" / "config.json"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "LandianUWB" / "TWRLocationTool" / "config.json"

    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root) / "landian_uwb_twr" / "config.json"
    return Path.home() / ".config" / "landian_uwb_twr" / "config.json"


def sanitize_anchors(value):
    """Normalize anchor configuration for runtime use."""
    source = value if isinstance(value, list) and value else DEFAULT_ANCHORS
    anchors = []
    for index, item in enumerate(source[:MAX_ANCHOR_COUNT]):
        if not isinstance(item, dict):
            continue
        fallback = DEFAULT_ANCHORS[index] if index < len(DEFAULT_ANCHORS) else DEFAULT_ANCHORS[-1]
        anchor = {
            "enable": 1 if _to_bool(item.get("enable"), bool(fallback["enable"])) else 0,
            "short_address": _to_int(item.get("short_address"), fallback["short_address"]),
            "x": _to_float(item.get("x"), fallback["x"]),
            "y": _to_float(item.get("y"), fallback["y"]),
            "z": _to_float(item.get("z"), fallback["z"]),
            "time": 0,
            "qt": 0,
        }
        anchors.append(anchor)

    while len(anchors) < 3:
        anchors.append(copy.deepcopy(DEFAULT_ANCHORS[len(anchors)]))

    for index in range(min(3, len(anchors))):
        anchors[index]["enable"] = 1
    return anchors


def serialize_anchors(anchors):
    """Convert runtime anchor dictionaries to readable JSON values."""
    serialized = []
    for item in sanitize_anchors(anchors):
        serialized.append(
            {
                "enable": 1 if item.get("enable") else 0,
                "short_address": "0x%04X" % _to_int(item.get("short_address"), 0),
                "x": round(_to_float(item.get("x"), 0.0), 6),
                "y": round(_to_float(item.get("y"), 0.0), 6),
                "z": round(_to_float(item.get("z"), 0.0), 6),
            }
        )
    return serialized


def normalize_config(raw_config):
    """Merge a loaded config with defaults and validate user-editable values."""
    raw = raw_config if isinstance(raw_config, dict) else {}
    config = copy.deepcopy(DEFAULT_CONFIG)

    anchors = sanitize_anchors(raw.get("anchors", config["anchors"]))
    config["anchors"] = anchors
    config["anchor_count"] = len(anchors)

    raw_comm = raw.get("communication", {})
    if not isinstance(raw_comm, dict):
        raw_comm = {}
    tcp_port = _to_int(raw_comm.get("tcp_port"), config["communication"]["tcp_port"])
    config["communication"]["tcp_port"] = _clamp(tcp_port, 1, 65535)
    config["communication"]["com_port"] = str(raw_comm.get("com_port", "") or "").strip()
    baudrate = _to_int(raw_comm.get("baudrate"), config["communication"]["baudrate"])
    config["communication"]["baudrate"] = _clamp(baudrate, 1200, 3000000)
    default_tab = str(raw_comm.get("default_tab", "COM") or "COM").upper()
    config["communication"]["default_tab"] = "TCP" if default_tab == "TCP" else "COM"

    raw_display = raw.get("display", {})
    if not isinstance(raw_display, dict):
        raw_display = {}
    history = _to_int(raw_display.get("history_count"), config["display"]["history_count"])
    config["display"]["history_count"] = _clamp(history, 1, 100)
    zoom = _to_float(raw_display.get("zoom_factor"), config["display"]["zoom_factor"])
    config["display"]["zoom_factor"] = _clamp(zoom, 0.35, 4.0)
    angle = _to_int(raw_display.get("view_angle_deg"), config["display"]["view_angle_deg"])
    config["display"]["view_angle_deg"] = _clamp(angle, 15, 75)
    config["display"]["measurement_aid_enabled"] = _to_bool(
        raw_display.get("measurement_aid_enabled"),
        config["display"]["measurement_aid_enabled"],
    )

    return config


def load_config():
    """Load and normalize persistent config; return defaults when absent/broken."""
    path = get_config_path()
    try:
        if not path.exists():
            return normalize_config(None)
        with path.open("r", encoding="utf-8") as file_obj:
            return normalize_config(json.load(file_obj))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return normalize_config(None)


def save_config(config):
    """Persist config atomically to the per-user JSON file."""
    normalized = normalize_config(config)
    output = {
        "version": CONFIG_VERSION,
        "anchor_count": normalized["anchor_count"],
        "anchors": serialize_anchors(normalized["anchors"]),
        "communication": normalized["communication"],
        "display": normalized["display"],
    }
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file_obj:
        json.dump(output, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")
    os.replace(str(tmp_path), str(path))
    return path
