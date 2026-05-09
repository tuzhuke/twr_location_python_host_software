# -*- coding: utf-8 -*-
"""
Anchor coordinate lookup and packet-to-position input conversion.
"""
import time

import globalvar
from uwb_logging import get_logger


logger = get_logger(__name__)


def _parse_address(address):
    """Accept integer, decimal string, or hex string anchor addresses."""
    if isinstance(address, str):
        text = address.strip()
        try:
            return int(text, 0)
        except ValueError:
            return int(text, 16)
    return int(address)


def Find_Anthor_Coor(Athor_Addr):
    """Find enabled anchor coordinates and refresh its last-seen timestamp."""
    try:
        anchor_address = _parse_address(Athor_Addr)
    except (TypeError, ValueError):
        return [0, 0, 0, 0]

    for item in globalvar.get_anthor():
        if item["short_address"] == anchor_address and item["enable"] == 1:
            item["time"] = time.time()
            return [1, item["x"], item["y"], item["z"]]
    return [0, 0, 0, 0]


def Anthor_Coordinate_Process(Anthor_info):
    """Convert parsed anchor ranges to coordinate, distance, and RSSI lists."""
    coordinate_list = []
    distance_list = []
    rssi_list = []

    for item in Anthor_info:
        if len(item) < 3:
            continue

        anthor_address = item[0]
        flag, x, y, z = Find_Anthor_Coor(anthor_address)
        if flag == 1:
            coordinate_list.append([x, y, z])
            distance_list.append(item[1])
            rssi_list.append(item[2])

    find_anthor_flag = 1 if coordinate_list else 0
    return find_anthor_flag, coordinate_list, distance_list, rssi_list


def BP_Process_String(Input_String):
    """Build the normalized solver input consumed by ``twr_main.Compute_Location``."""
    new_dict = {"tag": 0, "seq": 0, "count": 0, "anthor": [], "distance": [], "Rssi": []}
    if not isinstance(Input_String, dict):
        return new_dict

    new_dict["tag"] = Input_String.get("tag", 0)
    new_dict["seq"] = Input_String.get("seq", 0)

    anthor_info = Input_String.get("anthor", [])
    anthor_flag, coor_address, dist_stamp, rssi_list = Anthor_Coordinate_Process(anthor_info)
    if anthor_flag == 0:
        logger.warning("Could not find anchor node address")
        return new_dict

    for index, coordinate in enumerate(coor_address):
        new_dict["anthor"].append(coordinate)
        new_dict["distance"].append(dist_stamp[index])
        new_dict["Rssi"].append(rssi_list[index])

    new_dict["count"] = len(coor_address)
    return new_dict
