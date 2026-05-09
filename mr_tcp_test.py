# -*- coding: utf-8 -*-
"""
Generate noisy mr binary UWB frames, send them to the GUI TCP server, and
compute positioning error statistics through the same twr_main algorithm path.
"""
import argparse
import contextlib
import csv
import io
import math
import os
import random
import socket
import struct
import time

import numpy as np

import globalvar
import twr_main


DEFAULT_ANCHORS = [
    {'enable': 1, 'short_address': 0x0001, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'time': 0, 'qt': 0},
    {'enable': 1, 'short_address': 0x0002, 'x': 1.6, 'y': 0.0, 'z': 0.0, 'time': 0, 'qt': 0},
    {'enable': 1, 'short_address': 0x0003, 'x': 1.6, 'y': 1.6, 'z': 0.0, 'time': 0, 'qt': 0},
    {'enable': 1, 'short_address': 0x0004, 'x': 0.0, 'y': 1.6, 'z': 0.0, 'time': 0, 'qt': 0},
]


def true_position(index, total):
    phase = 2.0 * math.pi * index / max(1, total - 1)
    x_value = 0.80 + 0.55 * math.sin(phase * 2.0)
    y_value = 0.80 + 0.48 * math.cos(phase * 3.0 + 0.35)
    x_value += 0.08 * math.sin(phase * 11.0)
    y_value += 0.06 * math.cos(phase * 7.0)
    x_value = min(1.45, max(0.15, x_value))
    y_value = min(1.45, max(0.15, y_value))
    return x_value, y_value, 0.0


def noisy_distances(position, anchors, rng, sigma, nlos_rate, nlos_bias_min, nlos_bias_max):
    distances = []
    nlos_count = 0
    for anchor in anchors:
        dx = position[0] - anchor['x']
        dy = position[1] - anchor['y']
        dz = position[2] - anchor['z']
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        distance += rng.gauss(0.0, sigma)
        if rng.random() < nlos_rate:
            distance += rng.uniform(nlos_bias_min, nlos_bias_max)
            nlos_count += 1
        distances.append(max(0.0, distance))
    return distances, nlos_count


def build_mr_packet(tag, seq, distances):
    packet = bytearray()
    packet.extend(b"mr")
    packet.append(0x02)
    packet.append(tag & 0xFF)
    packet.extend(struct.pack("<H", seq & 0xFFFF))
    for distance in distances[:4]:
        distance_cm = int(round(distance * 100.0))
        distance_cm = max(0, min(65535, distance_cm))
        packet.extend(struct.pack("<H", distance_cm))
    packet.extend(b"\r\n")
    return bytes(packet)


def percentile(values, ratio):
    if not values:
        return 0.0
    values = sorted(values)
    index = int(round((len(values) - 1) * ratio))
    return values[index]


def summarize(errors, x_errors, y_errors, failures, nlos_frames):
    if errors:
        mean_error = sum(errors) / len(errors)
        rmse = math.sqrt(sum(err * err for err in errors) / len(errors))
        max_error = max(errors)
    else:
        mean_error = rmse = max_error = 0.0

    return {
        "success": len(errors),
        "failures": failures,
        "nlos_frames": nlos_frames,
        "mean_error_m": mean_error,
        "median_error_m": percentile(errors, 0.50),
        "p90_error_m": percentile(errors, 0.90),
        "p95_error_m": percentile(errors, 0.95),
        "p99_error_m": percentile(errors, 0.99),
        "rmse_m": rmse,
        "max_error_m": max_error,
        "mean_abs_x_m": sum(abs(value) for value in x_errors) / len(x_errors) if x_errors else 0.0,
        "mean_abs_y_m": sum(abs(value) for value in y_errors) / len(y_errors) if y_errors else 0.0,
    }


def run(args):
    rng = random.Random(args.seed)
    globalvar.set_anthor([dict(item) for item in DEFAULT_ANCHORS])
    twr_main.LAST_LOCATION_RESULTS.clear()
    twr_main.EKF_STATES.clear()

    packets = []
    rows = []
    errors = []
    x_errors = []
    y_errors = []
    failures = 0
    nlos_frames = 0

    for index in range(args.count):
        position = true_position(index, args.count)
        distances, nlos_count = noisy_distances(
            position,
            DEFAULT_ANCHORS,
            rng,
            args.sigma,
            args.nlos_rate,
            args.nlos_bias_min,
            args.nlos_bias_max,
        )
        if nlos_count:
            nlos_frames += 1
        packet = build_mr_packet(args.tag, index, distances)
        packets.append(packet)

        if args.verbose:
            result = twr_main.twr_main(packet)
        else:
            with contextlib.redirect_stdout(io.StringIO()):
                result = twr_main.twr_main(packet)
        ok, seq, tag, calc_x, calc_y, calc_z, algorithm = result
        if ok == 1:
            dx = float(calc_x) - position[0]
            dy = float(calc_y) - position[1]
            dz = float(calc_z) - position[2]
            error = math.sqrt(dx * dx + dy * dy + dz * dz)
            errors.append(error)
            x_errors.append(dx)
            y_errors.append(dy)
        else:
            failures += 1
            dx = dy = dz = error = float("nan")

        rows.append({
            "index": index,
            "tag": tag,
            "seq": seq,
            "true_x": position[0],
            "true_y": position[1],
            "true_z": position[2],
            "distance0": distances[0],
            "distance1": distances[1],
            "distance2": distances[2],
            "distance3": distances[3],
            "calc_x": calc_x,
            "calc_y": calc_y,
            "calc_z": calc_z,
            "error_m": error,
            "nlos_anchor_count": nlos_count,
            "algorithm": algorithm,
        })

    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(args.output_dir, "mr_tcp_test_%s.csv" % timestamp)
    with open(csv_path, "w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    banner = b""
    send_started = time.time()
    with socket.create_connection((args.host, args.port), timeout=args.timeout) as sock:
        sock.settimeout(1.0)
        try:
            banner = sock.recv(256)
        except socket.timeout:
            banner = b""

        for start in range(0, len(packets), args.chunk_size):
            sock.sendall(b"".join(packets[start:start + args.chunk_size]))
            if args.chunk_delay > 0:
                time.sleep(args.chunk_delay)
    send_elapsed = time.time() - send_started

    stats = summarize(errors, x_errors, y_errors, failures, nlos_frames)
    stats.update({
        "count": args.count,
        "host": args.host,
        "port": args.port,
        "noise_sigma_m": args.sigma,
        "nlos_rate": args.nlos_rate,
        "seed": args.seed,
        "csv_path": os.path.abspath(csv_path),
        "tcp_banner": banner.decode("utf-8", errors="ignore"),
        "send_elapsed_s": send_elapsed,
    })
    return stats


def main():
    parser = argparse.ArgumentParser(description="mr TCP positioning test client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--tag", type=int, default=0x33)
    parser.add_argument("--seed", type=int, default=20260509)
    parser.add_argument("--sigma", type=float, default=0.05)
    parser.add_argument("--nlos-rate", type=float, default=0.03)
    parser.add_argument("--nlos-bias-min", type=float, default=0.15)
    parser.add_argument("--nlos-bias-max", type=float, default=0.45)
    parser.add_argument("--chunk-size", type=int, default=20)
    parser.add_argument("--chunk-delay", type=float, default=0.002)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--output-dir", default="logs")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    stats = run(args)
    for key in sorted(stats.keys()):
        value = stats[key]
        if isinstance(value, float):
            print("%s=%.6f" % (key, value))
        else:
            print("%s=%s" % (key, value))


if __name__ == "__main__":
    main()
