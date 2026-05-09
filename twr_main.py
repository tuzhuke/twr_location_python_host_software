# -*- coding: utf-8 -*-
"""
UWB TWR packet parsing and 2D/3D positioning.

Expected packet format:
&&&:LEN$TAG:SEQ$ANCHOR_ID:DIST_CM:RSSI#...$CRC####
"""
import re
import time

import numpy as np

import globalvar
from Coordinate_process import BP_Process_String


ALGORITHM_IDLE = "等待数据"
ALGORITHM_2D_3_ANCHOR = "3基站二维定位"
ALGORITHM_2D_4_ANCHOR = "4基站二维定位"
ALGORITHM_3D_4_ANCHOR = "4基站三维定位"
ALGORITHM_IMU_HOLD = "IMU静止:保持上次定位结果"
ALGORITHM_IMU_NO_LAST = "IMU静止:无历史定位结果"
BINARY_PACKET_LEN = 16
BINARY_HEADER = b"mr\x02"
IMU_BINARY_PACKET_LEN = 18
IMU_BINARY_HEADER = b"mri\x02"
LAST_LOCATION_RESULTS = {}

# Stable display labels. The original file has historical mojibake strings; keep
# the public variable names but rewrite them with Unicode escapes.
ALGORITHM_IDLE = "\u7b49\u5f85\u6570\u636e"
ALGORITHM_2D_3_ANCHOR = "3\u57fa\u7ad9\u4e8c\u7ef4\u5b9a\u4f4d"
ALGORITHM_2D_4_ANCHOR = "4\u57fa\u7ad9\u4e8c\u7ef4\u5b9a\u4f4d"
ALGORITHM_3D_4_ANCHOR = "4\u57fa\u7ad9\u4e09\u7ef4\u5b9a\u4f4d"
ALGORITHM_2D_MULTI_ANCHOR = "\u591a\u57fa\u7ad9\u4e8c\u7ef4\u5b9a\u4f4d"
ALGORITHM_3D_MULTI_ANCHOR = "\u591a\u57fa\u7ad9\u4e09\u7ef4\u5b9a\u4f4d"
ALGORITHM_IMU_HOLD = "IMU\u9759\u6b62:\u4fdd\u6301\u4e0a\u6b21EKF\u5b9a\u4f4d\u7ed3\u679c"
ALGORITHM_IMU_NO_LAST = "IMU\u9759\u6b62:\u65e0\u5386\u53f2\u5b9a\u4f4d\u7ed3\u679c"

NLLS_MAX_ITERATIONS = 15
NLLS_INITIAL_DAMPING = 1e-3
NLLS_STEP_EPS = 1e-5
NLLS_HUBER_THRESHOLD_M = 0.35

EKF_DEFAULT_DT = 0.10
EKF_MIN_DT = 0.02
EKF_MAX_DT = 0.50
EKF_PROCESS_NOISE_MPS2 = 1.40
EKF_MIN_RANGE_SIGMA_M = 0.06
EKF_MAX_RANGE_SIGMA_M = 0.90
EKF_STATES = {}


class TagRangeEKF:
    """Per-tag constant-velocity EKF with nonlinear UWB range updates."""

    def __init__(self, tag, dimensions):
        self.tag = tag
        self.dimensions = int(dimensions)
        self.state_size = self.dimensions * 2
        self.state = np.zeros(self.state_size, dtype=float)
        self.cov = np.eye(self.state_size, dtype=float)
        self.last_time = None
        self.initialized = False

    def initialize(self, position, now=None):
        now = time.monotonic() if now is None else now
        position = np.asarray(position, dtype=float)
        self.state[:] = 0.0
        self.state[:self.dimensions] = position[:self.dimensions]
        self.cov = np.eye(self.state_size, dtype=float)
        self.cov[:self.dimensions, :self.dimensions] *= 0.18 ** 2
        self.cov[self.dimensions:, self.dimensions:] *= 1.20 ** 2
        self.last_time = now
        self.initialized = True

    def predict(self, now=None):
        if not self.initialized:
            return

        now = time.monotonic() if now is None else now
        if self.last_time is None:
            dt = EKF_DEFAULT_DT
        else:
            dt = now - self.last_time
            dt = max(EKF_MIN_DT, min(EKF_MAX_DT, dt))

        f_matrix = np.eye(self.state_size, dtype=float)
        for axis in range(self.dimensions):
            f_matrix[axis, axis + self.dimensions] = dt

        accel_var = EKF_PROCESS_NOISE_MPS2 ** 2
        q_matrix = np.zeros((self.state_size, self.state_size), dtype=float)
        for axis in range(self.dimensions):
            pos_index = axis
            vel_index = axis + self.dimensions
            q_matrix[pos_index, pos_index] = 0.25 * dt ** 4 * accel_var
            q_matrix[pos_index, vel_index] = 0.5 * dt ** 3 * accel_var
            q_matrix[vel_index, pos_index] = 0.5 * dt ** 3 * accel_var
            q_matrix[vel_index, vel_index] = dt ** 2 * accel_var

        self.state = f_matrix.dot(self.state)
        self.cov = f_matrix.dot(self.cov).dot(f_matrix.T) + q_matrix
        self.last_time = now

    def update_ranges(self, anchors, distances, measurement_sigma):
        anchors = np.asarray(anchors, dtype=float)[:, :self.dimensions]
        distances = np.asarray(distances, dtype=float)
        if len(anchors) != len(distances) or len(distances) < self.dimensions + 1:
            raise ValueError("invalid EKF range update")

        pos = self.state[:self.dimensions]
        diffs = pos - anchors
        predicted = np.linalg.norm(diffs, axis=1)
        predicted = np.maximum(predicted, 1e-6)
        innovation = distances - predicted

        h_matrix = np.zeros((len(distances), self.state_size), dtype=float)
        h_matrix[:, :self.dimensions] = diffs / predicted[:, None]

        sigma = max(EKF_MIN_RANGE_SIGMA_M, min(EKF_MAX_RANGE_SIGMA_M, float(measurement_sigma)))
        r_matrix = np.eye(len(distances), dtype=float) * (sigma ** 2)
        s_matrix = h_matrix.dot(self.cov).dot(h_matrix.T) + r_matrix
        k_gain = self.cov.dot(h_matrix.T).dot(np.linalg.pinv(s_matrix))

        self.state = self.state + k_gain.dot(innovation)
        identity = np.eye(self.state_size, dtype=float)
        kh_matrix = k_gain.dot(h_matrix)
        self.cov = (
            (identity - kh_matrix)
            .dot(self.cov)
            .dot((identity - kh_matrix).T)
            + k_gain.dot(r_matrix).dot(k_gain.T)
        )
        self.cov = 0.5 * (self.cov + self.cov.T)

    def position_3d(self):
        result = np.zeros(3, dtype=float)
        result[:self.dimensions] = self.state[:self.dimensions]
        return result


class Trilateration:
    def __init__(self):
        self.position = np.empty((0, 3), dtype=float)
        self.distances = []
        self.result = np.array([0.0, 0.0, 0.0], dtype=float)

    def _validate(self, dimensions):
        positions = np.asarray(self.position, dtype=float)
        distances = np.asarray(self.distances, dtype=float)

        if positions.ndim != 2 or positions.shape[1] < dimensions:
            raise ValueError("anchor coordinates must have at least %d dimensions" % dimensions)
        if len(positions) != len(distances):
            raise ValueError("anchor count and distance count do not match")
        if len(distances) < dimensions + 1:
            raise ValueError("at least %d anchors are required" % (dimensions + 1))
        return positions[:, :dimensions], distances

    def _solve(self, dimensions):
        positions, distances = self._validate(dimensions)
        ref = positions[0]
        ref_distance = distances[0]
        a_matrix = []
        b_vector = []

        for idx in range(1, len(distances)):
            anchor = positions[idx]
            a_matrix.append(2 * (anchor - ref))
            b_vector.append(
                ref_distance ** 2
                - distances[idx] ** 2
                + np.dot(anchor, anchor)
                - np.dot(ref, ref)
            )

        a_matrix = np.asarray(a_matrix, dtype=float)
        b_vector = np.asarray(b_vector, dtype=float)
        if np.linalg.matrix_rank(a_matrix) < dimensions:
            if dimensions == 2:
                raise ValueError("anchor coordinates are collinear")
            raise ValueError("anchor coordinates cannot resolve 3D position")

        self.result, _, _, _ = np.linalg.lstsq(a_matrix, b_vector, rcond=None)
        return self.result

    def trilaterate2D(self):
        result = self._solve(2)
        return float(result[0]), float(result[1]), 0.0

    def trilaterate3D(self):
        result = self._solve(3)
        return float(result[0]), float(result[1]), float(result[2])

    def setDistances(self, distances):
        self.distances = distances

    def setAnthorCoor(self, Anthor_Node_Configure):
        self.position = np.asarray(Anthor_Node_Configure, dtype=float)

    def setAnchorCoor(self, Anchor_Node_Configure):
        self.setAnthorCoor(Anchor_Node_Configure)


def bphero_dispose(string):
    result_dict = {'tag': 0, 'seq': 0, 'time': 0, 'anthor_count': 0, 'anthor': []}
    if not string:
        return 1, result_dict

    packet = string.strip()
    end_index = packet.find("####")
    if end_index != -1:
        packet = packet[:end_index + 4]

    if not packet.startswith("&&&:"):
        return 1, result_dict

    try:
        fields = packet.split("$")
        if len(fields) < 4:
            return 1, result_dict

        header = fields[0]
        tag_info = fields[1]
        anchor_info = fields[2]
        crc_info = fields[3]

        if not re.fullmatch(r"&&&:[0-9A-Fa-f]+", header):
            return 1, result_dict
        int(header.split(":")[1], 16)

        tag_parts = tag_info.split(":")
        if len(tag_parts) != 2:
            return 1, result_dict
        result_dict['tag'] = int(tag_parts[0], 16)
        result_dict['seq'] = int(tag_parts[1], 16)

        if not crc_info.endswith("####"):
            return 1, result_dict

        anchors = [item for item in anchor_info.split("#") if item]
        if not anchors:
            return 1, result_dict

        for index, anthor_info in enumerate(anchors):
            anchor_parts = anthor_info.split(":")
            if len(anchor_parts) != 3:
                return 1, result_dict

            anthor_id = int(anchor_parts[0], 16)
            anthor_dist = 0.01 * int(anchor_parts[1], 16)
            anthor_rssi = int(anchor_parts[2], 16)
            print("Anthor%d Distance = %0.2f m" % (index + 1, anthor_dist))
            result_dict['anthor'].append([anthor_id, anthor_dist, anthor_rssi])

        result_dict['anthor_count'] = len(result_dict['anthor'])
        return 0, result_dict
    except (TypeError, ValueError, IndexError) as exc:
        print("Parse packet failed: %s" % exc)
        return 1, result_dict


def get_binary_anchor_ids(anchor_count):
    enabled = [item for item in globalvar.get_anthor() if item.get("enable") == 1]
    source = enabled if len(enabled) >= anchor_count else globalvar.get_anthor()
    anchor_ids = [item["short_address"] for item in source[:anchor_count]]
    while len(anchor_ids) < anchor_count:
        anchor_ids.append(len(anchor_ids) + 1)
    return anchor_ids


def binary_dispose(packet):
    result_dict = {'tag': 0, 'seq': 0, 'time': 0, 'anthor_count': 0, 'anthor': []}
    if isinstance(packet, str):
        packet = packet.encode("latin1", errors="ignore")
    if len(packet) != BINARY_PACKET_LEN:
        return 1, result_dict
    if not packet.startswith(BINARY_HEADER) or packet[-2:] != b"\r\n":
        return 1, result_dict

    tag_id = packet[3]
    frame_seq = packet[4] | (packet[5] << 8)
    raw_distances = []
    for offset in (6, 8, 10, 12):
        raw_distances.append(packet[offset] | (packet[offset + 1] << 8))

    anchor_count = 3 if raw_distances[3] == raw_distances[0] else 4
    anchor_ids = get_binary_anchor_ids(anchor_count)

    result_dict['tag'] = tag_id
    result_dict['seq'] = frame_seq
    result_dict['anthor_count'] = anchor_count
    for index in range(anchor_count):
        distance_m = raw_distances[index] * 0.01
        result_dict['anthor'].append([anchor_ids[index], distance_m, 0])
        print("Binary Anthor%d Distance = %0.2f m" % (index + 1, distance_m))

    return 0, result_dict


def imu_binary_dispose(packet):
    result_dict = {
        'tag': 0,
        'seq': 0,
        'time': 0,
        'anthor_count': 0,
        'anthor': [],
        'motion_state': '',
    }
    if isinstance(packet, str):
        packet = packet.encode("latin1", errors="ignore")
    if len(packet) != IMU_BINARY_PACKET_LEN:
        return 1, result_dict
    if not packet.startswith(IMU_BINARY_HEADER) or packet[-2:] != b"\n\r":
        return 1, result_dict

    tag_id = packet[4]
    frame_seq = packet[5] | (packet[6] << 8)
    raw_distances = []
    for offset in (7, 9, 11, 13):
        raw_distances.append(packet[offset] | (packet[offset + 1] << 8))

    motion_byte = packet[15]
    if motion_byte not in (ord("s"), ord("m")):
        return 1, result_dict

    anchor_count = 3 if raw_distances[3] == raw_distances[0] else 4
    anchor_ids = get_binary_anchor_ids(anchor_count)

    result_dict['tag'] = tag_id
    result_dict['seq'] = frame_seq
    result_dict['anthor_count'] = anchor_count
    result_dict['motion_state'] = chr(motion_byte)
    for index in range(anchor_count):
        distance_m = raw_distances[index] * 0.01
        result_dict['anthor'].append([anchor_ids[index], distance_m, 0])
        print("IMU Binary Anthor%d Distance = %0.2f m" % (index + 1, distance_m))

    return 0, result_dict


def extract_packets(buffer):
    if isinstance(buffer, str):
        buffer = buffer.encode("latin1", errors="ignore")

    packets = []
    while buffer:
        text_start = buffer.find(b"&&&:")
        binary_start = buffer.find(BINARY_HEADER)
        imu_start = buffer.find(IMU_BINARY_HEADER)
        starts = [idx for idx in (text_start, binary_start, imu_start) if idx >= 0]
        if not starts:
            return packets, b""

        start = min(starts)
        if start > 0:
            buffer = buffer[start:]
            text_start = buffer.find(b"&&&:")
            binary_start = buffer.find(BINARY_HEADER)
            imu_start = buffer.find(IMU_BINARY_HEADER)

        if imu_start == 0:
            if len(buffer) < IMU_BINARY_PACKET_LEN:
                return packets, buffer
            packet = buffer[:IMU_BINARY_PACKET_LEN]
            if packet[-2:] == b"\n\r":
                packets.append(packet)
                buffer = buffer[IMU_BINARY_PACKET_LEN:]
            else:
                buffer = buffer[1:]
            continue

        if binary_start == 0 and (text_start != 0):
            if len(buffer) < BINARY_PACKET_LEN:
                return packets, buffer
            packet = buffer[:BINARY_PACKET_LEN]
            if packet[-2:] == b"\r\n":
                packets.append(packet)
                buffer = buffer[BINARY_PACKET_LEN:]
            else:
                buffer = buffer[1:]
            continue

        if text_start == 0:
            end = buffer.find(b"####")
            if end < 0:
                return packets, buffer
            packet = buffer[:end + 4]
            packets.append(packet)
            buffer = buffer[end + 4:]
            continue

        buffer = buffer[1:]

    return packets, b""


def select_location_algorithm(info):
    count = info['count']
    anchors = np.asarray(info['anthor'], dtype=float)

    if count == 3:
        return ALGORITHM_2D_3_ANCHOR
    if count == 4:
        if np.allclose(anchors[:, 2], anchors[0, 2]):
            return ALGORITHM_2D_4_ANCHOR
        return ALGORITHM_3D_4_ANCHOR
    if count > 4:
        if np.allclose(anchors[:, 2], anchors[0, 2]):
            return "多基站二维定位"
        return "多基站三维定位"
    return ALGORITHM_IDLE


def Compute_Location(Input_Data):
    motion_state = Input_Data.get('motion_state') if isinstance(Input_Data, dict) else None
    if motion_state == 's':
        tag = Input_Data.get('tag', 0)
        seq = Input_Data.get('seq', 0)
        last_result = LAST_LOCATION_RESULTS.get(tag)
        if last_result is None:
            return 0, seq, tag, 0, 0, 0, ALGORITHM_IMU_NO_LAST
        result_x, result_y, result_z, _ = last_result
        print("%s: tag=%d x = %0.2f, y = %0.2f, z = %0.2f" % (
            ALGORITHM_IMU_HOLD, tag, result_x, result_y, result_z
        ))
        return 1, seq, tag, result_x, result_y, result_z, ALGORITHM_IMU_HOLD

    info = BP_Process_String(Input_Data)
    print(info)
    if info['count'] < 3:
        return 0, info['seq'], info['tag'], 0, 0, 0, ALGORITHM_IDLE

    algorithm = select_location_algorithm(info)

    try:
        tril = Trilateration()
        tril.setDistances(info['distance'])
        tril.setAnthorCoor(info['anthor'])
        if algorithm in (ALGORITHM_3D_4_ANCHOR, "多基站三维定位"):
            result_x, result_y, result_z = tril.trilaterate3D()
        else:
            result_x, result_y, result_z = tril.trilaterate2D()

        print("%s: x = %0.2f, y = %0.2f, z = %0.2f" % (algorithm, result_x, result_y, result_z))
        LAST_LOCATION_RESULTS[info['tag']] = (result_x, result_y, result_z, algorithm)
        return 1, info['seq'], info['tag'], result_x, result_y, result_z, algorithm
    except ValueError as exc:
        print("Compute location failed: %s" % exc)
        return 0, info['seq'], info['tag'], 0, 0, 0, algorithm + "失败"


def _location_mode(info):
    count = int(info.get('count', 0))
    anchors = np.asarray(info.get('anthor', []), dtype=float)
    if count < 3 or anchors.ndim != 2 or anchors.shape[1] < 3:
        return ALGORITHM_IDLE, 0

    if count == 3:
        return ALGORITHM_2D_3_ANCHOR, 2

    z_equal = np.allclose(anchors[:, 2], anchors[0, 2], atol=1e-6)
    if z_equal:
        if count == 4:
            return ALGORITHM_2D_4_ANCHOR, 2
        return ALGORITHM_2D_MULTI_ANCHOR, 2

    if count == 4:
        return ALGORITHM_3D_4_ANCHOR, 3
    return ALGORITHM_3D_MULTI_ANCHOR, 3


def _solver_arrays(info, dimensions):
    anchors = np.asarray(info.get('anthor', []), dtype=float)
    distances = np.asarray(info.get('distance', []), dtype=float)
    if dimensions not in (2, 3):
        raise ValueError("invalid location dimensions")
    if anchors.ndim != 2 or anchors.shape[1] < dimensions:
        raise ValueError("anchor coordinates do not match location dimensions")
    if len(anchors) != len(distances):
        raise ValueError("anchor count and distance count do not match")
    if len(distances) < dimensions + 1:
        raise ValueError("at least %d anchors are required" % (dimensions + 1))
    if np.any(distances < 0):
        raise ValueError("distance must be non-negative")
    return anchors[:, :dimensions], distances


def _linear_position(anchors, distances, dimensions):
    anchors = np.asarray(anchors, dtype=float)[:, :dimensions]
    distances = np.asarray(distances, dtype=float)
    ref = anchors[0]
    ref_distance = distances[0]
    a_matrix = []
    b_vector = []

    for idx in range(1, len(distances)):
        anchor = anchors[idx]
        a_matrix.append(2 * (anchor - ref))
        b_vector.append(
            ref_distance ** 2
            - distances[idx] ** 2
            + np.dot(anchor, anchor)
            - np.dot(ref, ref)
        )

    a_matrix = np.asarray(a_matrix, dtype=float)
    b_vector = np.asarray(b_vector, dtype=float)
    if np.linalg.matrix_rank(a_matrix) < dimensions:
        if dimensions == 2:
            raise ValueError("anchor coordinates are collinear")
        raise ValueError("anchor coordinates cannot resolve 3D position")

    result, _, _, _ = np.linalg.lstsq(a_matrix, b_vector, rcond=None)
    return np.asarray(result, dtype=float)


def _range_residuals(position, anchors, distances, dimensions):
    position = np.asarray(position, dtype=float)[:dimensions]
    anchors = np.asarray(anchors, dtype=float)[:, :dimensions]
    distances = np.asarray(distances, dtype=float)
    predicted = np.linalg.norm(anchors - position, axis=1)
    return predicted - distances


def _residual_metrics(position, anchors, distances, dimensions):
    residual = _range_residuals(position, anchors, distances, dimensions)
    if len(residual) == 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(residual ** 2)))
    max_abs = float(np.max(np.abs(residual)))
    return rms, max_abs


def _nlls_position(anchors, distances, dimensions, initial=None):
    anchors = np.asarray(anchors, dtype=float)[:, :dimensions]
    distances = np.asarray(distances, dtype=float)
    if initial is None:
        initial = _linear_position(anchors, distances, dimensions)

    x_value = np.asarray(initial, dtype=float)[:dimensions].copy()
    if not np.all(np.isfinite(x_value)):
        x_value = np.mean(anchors, axis=0)

    damping = NLLS_INITIAL_DAMPING
    robust_enabled = len(distances) > dimensions + 1

    for _ in range(NLLS_MAX_ITERATIONS):
        diffs = x_value - anchors
        predicted = np.linalg.norm(diffs, axis=1)
        predicted = np.maximum(predicted, 1e-6)
        residual = predicted - distances
        jacobian = diffs / predicted[:, None]

        if robust_enabled:
            abs_residual = np.abs(residual)
            weights = np.ones(len(residual), dtype=float)
            large = abs_residual > NLLS_HUBER_THRESHOLD_M
            weights[large] = NLLS_HUBER_THRESHOLD_M / abs_residual[large]
            weight_root = np.sqrt(weights)
            jacobian_w = jacobian * weight_root[:, None]
            residual_w = residual * weight_root
        else:
            jacobian_w = jacobian
            residual_w = residual

        normal_matrix = jacobian_w.T.dot(jacobian_w) + damping * np.eye(dimensions)
        gradient = jacobian_w.T.dot(residual_w)
        try:
            step = np.linalg.solve(normal_matrix, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(normal_matrix).dot(gradient)

        candidate = x_value - step
        old_cost = float(np.dot(residual_w, residual_w))
        new_residual = _range_residuals(candidate, anchors, distances, dimensions)
        if robust_enabled:
            new_residual_w = new_residual * weight_root
        else:
            new_residual_w = new_residual
        new_cost = float(np.dot(new_residual_w, new_residual_w))

        if new_cost <= old_cost:
            x_value = candidate
            damping = max(damping * 0.5, 1e-8)
            if np.linalg.norm(step) < NLLS_STEP_EPS:
                break
        else:
            damping = min(damping * 5.0, 1.0)

    return x_value


def _quality_label(rms):
    if rms <= 0.10:
        return "\u4f18"
    if rms <= 0.30:
        return "\u826f"
    if rms <= 0.60:
        return "\u8b66\u544a"
    return "\u5dee"


def _measurement_sigma_from_rms(rms):
    return max(EKF_MIN_RANGE_SIGMA_M, min(EKF_MAX_RANGE_SIGMA_M, 0.08 + 0.75 * float(rms)))


def _get_tag_filter(tag, dimensions, initial_position, now):
    ekf = EKF_STATES.get(tag)
    if ekf is None or ekf.dimensions != dimensions:
        ekf = TagRangeEKF(tag, dimensions)
        ekf.initialize(initial_position, now)
        EKF_STATES[tag] = ekf
        return ekf, True

    if not ekf.initialized:
        ekf.initialize(initial_position, now)
        return ekf, True
    return ekf, False


def _status_text(algorithm, rms, suffix="NLLS+EKF"):
    return "%s %s RMS:%0.2fm \u8d28\u91cf:%s" % (
        algorithm,
        suffix,
        rms,
        _quality_label(rms),
    )


def Compute_Location(Input_Data):
    motion_state = Input_Data.get('motion_state') if isinstance(Input_Data, dict) else None
    tag = Input_Data.get('tag', 0) if isinstance(Input_Data, dict) else 0
    seq = Input_Data.get('seq', 0) if isinstance(Input_Data, dict) else 0

    if motion_state == 's':
        ekf = EKF_STATES.get(tag)
        last_result = LAST_LOCATION_RESULTS.get(tag)
        if ekf is not None and ekf.initialized:
            position = ekf.position_3d()
            result_x, result_y, result_z = position
        elif last_result is not None:
            result_x, result_y, result_z, _ = last_result
        else:
            return 0, seq, tag, 0, 0, 0, ALGORITHM_IMU_NO_LAST

        status = "%s" % ALGORITHM_IMU_HOLD
        LAST_LOCATION_RESULTS[tag] = (result_x, result_y, result_z, status)
        print("%s: tag=%d x = %0.2f, y = %0.2f, z = %0.2f" % (
            status, tag, result_x, result_y, result_z
        ))
        return 1, seq, tag, result_x, result_y, result_z, status

    info = BP_Process_String(Input_Data)
    print(info)
    if info['count'] < 3:
        return 0, info['seq'], info['tag'], 0, 0, 0, ALGORITHM_IDLE

    algorithm, dimensions = _location_mode(info)
    if dimensions == 0:
        return 0, info['seq'], info['tag'], 0, 0, 0, ALGORITHM_IDLE

    try:
        anchors, distances = _solver_arrays(info, dimensions)
        linear_position = _linear_position(anchors, distances, dimensions)
        nlls_position = _nlls_position(anchors, distances, dimensions, linear_position)
        nlls_rms, _ = _residual_metrics(nlls_position, anchors, distances, dimensions)

        now = time.monotonic()
        ekf, initialized = _get_tag_filter(info['tag'], dimensions, nlls_position, now)
        if not initialized:
            ekf.predict(now)
        ekf.update_ranges(anchors, distances, _measurement_sigma_from_rms(nlls_rms))
        filtered_position = ekf.position_3d()
        if dimensions == 2:
            filtered_position[2] = 0.0

        result_x, result_y, result_z = [float(value) for value in filtered_position]
        status = _status_text(algorithm, nlls_rms)
        LAST_LOCATION_RESULTS[info['tag']] = (result_x, result_y, result_z, status)
        print("%s: x = %0.2f, y = %0.2f, z = %0.2f" % (status, result_x, result_y, result_z))
        return 1, info['seq'], info['tag'], result_x, result_y, result_z, status
    except ValueError as exc:
        print("Compute location failed: %s" % exc)
        return 0, info['seq'], info['tag'], 0, 0, 0, algorithm + "\u5931\u8d25"


def Process_String_Before_Udp(NewString):
    if isinstance(NewString, (bytes, bytearray)):
        data = bytes(NewString)
        if data.startswith(IMU_BINARY_HEADER):
            return imu_binary_dispose(data)
        if data.startswith(BINARY_HEADER):
            return binary_dispose(data)
        try:
            NewString = data.decode("ascii", errors="ignore")
        except UnicodeDecodeError:
            return 1, {'tag': 0, 'seq': 0, 'time': 0, 'anthor_count': 0, 'anthor': []}
    return bphero_dispose(NewString)


def twr_main(input_string):
    print(input_string)
    error_flag, result_dic = Process_String_Before_Udp(input_string)
    if error_flag == 0:
        return Compute_Location(result_dic)
    return 0, 0, 0, 0, 0, 0, ALGORITHM_IDLE
