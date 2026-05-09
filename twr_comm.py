# -*- coding: utf-8 -*-
"""TCP and serial communication services for the UWB location tool.

Both services run blocking I/O outside the GUI thread. They emit raw data,
parsed packet details, location results, and algorithm status through Qt
signals; UI widgets must not be touched directly from these worker threads.
"""
import socket
import time
from threading import Thread

from PyQt5 import QtCore

from twr_main import Compute_Location, Process_String_Before_Udp, extract_packets
from uwb_logging import get_logger

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


logger = get_logger(__name__)

SERIAL_READ_TIMEOUT_S = 0.05
SERIAL_READ_MAX_BYTES = 4096
SERIAL_RX_BUFFER_SIZE = 8192
SERIAL_TX_BUFFER_SIZE = 2048
COMM_EMIT_INTERVAL_S = 0.03
COMM_MAX_BATCH_PACKETS = 64
COMM_MAX_RAW_CHARS = 32768


class SignalBatcher:
    """Coalesce high-rate transport events before crossing into the GUI thread."""

    def __init__(self, owner):
        self.owner = owner
        self.raw_parts = []
        self.raw_chars = 0
        self.parse_infos = []
        self.results_by_tag = {}
        self.latest_algorithm = None
        self.last_flush = time.monotonic()

    def add_raw(self, text):
        """Queue raw text/hex output for one receive chunk."""
        if not text:
            return
        self.raw_parts.append(text)
        self.raw_chars += len(text)

    def add_packet(self, parse_info, location_tuple, algorithm):
        """Queue parsed packet info and the latest located point per tag."""
        self.latest_algorithm = algorithm
        if parse_info is not None:
            self.parse_infos.append(parse_info)

        (
            location_result,
            location_seq,
            location_addr,
            location_x,
            location_y,
            location_z,
            _algorithm,
        ) = location_tuple
        if location_result == 1:
            self.results_by_tag[location_addr] = (
                location_seq,
                location_addr,
                location_x,
                location_y,
                location_z,
                algorithm,
            )

    def add_algorithm(self, algorithm):
        """Queue a status-only algorithm update."""
        self.latest_algorithm = algorithm

    def should_flush(self, force=False):
        """Return True when pending events should be emitted now."""
        if force:
            return True
        pending_packets = len(self.parse_infos) + len(self.results_by_tag)
        if pending_packets >= COMM_MAX_BATCH_PACKETS:
            return True
        if self.raw_chars >= COMM_MAX_RAW_CHARS:
            return True
        return time.monotonic() - self.last_flush >= COMM_EMIT_INTERVAL_S

    def flush(self, force=False):
        """Emit one compact batch of pending raw, parse, result, and status data."""
        if not self.should_flush(force=force):
            return

        if self.raw_parts:
            if any(part.startswith("HEX ") for part in self.raw_parts):
                raw_text = "\n".join(self.raw_parts)
            else:
                raw_text = "".join(self.raw_parts)
            self.owner.data_draf.emit(raw_text)

        if self.latest_algorithm is not None:
            self.owner.algorithm_status.emit(self.latest_algorithm)

        if self.parse_infos:
            self.owner.data_parse.emit(self.parse_infos[:] if len(self.parse_infos) > 1 else self.parse_infos[0])

        if self.results_by_tag:
            results = list(self.results_by_tag.values())
            self.owner.data_result.emit(results if len(results) > 1 else results[0])

        self.raw_parts = []
        self.raw_chars = 0
        self.parse_infos = []
        self.results_by_tag = {}
        self.latest_algorithm = None
        self.last_flush = time.monotonic()


class TCP_SERVER(QtCore.QThread):
    """TCP server transport.

    The application is the server: it binds the configured local port, accepts
    clients, accumulates bytes in a stream buffer, extracts complete protocol
    packets, then forwards each packet to the shared parser/location pipeline.
    """

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
        """Bind and listen on ``port``. Accepting clients is started separately."""
        self.tcp_close()
        self.g_socket_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.g_socket_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.g_socket_server.settimeout(0.5)
        self.port = port
        self.ip = "0.0.0.0"
        self.g_socket_server.bind(("", self.port))
        self.g_socket_server.listen(5)
        self.socketClosed = False
        logger.info("server start, wait for client connecting...")

    @staticmethod
    def extract_packets(buffer):
        """Expose shared packet extraction for tests and service loops."""
        return extract_packets(buffer)

    @staticmethod
    def format_raw_data(data):
        """Convert raw bytes to readable ASCII or HEX for the debug log."""
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
        """Read one TCP client until disconnect or server close.

        Packet-level exceptions are isolated so a single malformed frame cannot
        stop the whole client receiver.
        """
        logger.info("client connected: %s", info)
        buffer = bytearray()
        batcher = SignalBatcher(self)
        try:
            client.settimeout(0.5)
            client.sendall("connect server successfully!".encode("utf8"))
            while not self.socketClosed:
                try:
                    recv_bytes = client.recv(1024)
                except socket.timeout:
                    batcher.flush()
                    continue
                if not recv_bytes:
                    break

                batcher.add_raw(self.format_raw_data(recv_bytes))
                buffer.extend(recv_bytes)
                packets, buffer = self.extract_packets(buffer)
                buffer = bytearray(buffer)

                for packet in packets:
                    try:
                        parse_error, parse_info = Process_String_Before_Udp(packet)
                        location_tuple = (
                            Compute_Location(parse_info)
                            if parse_error == 0
                            else (0, 0, 0, 0, 0, 0, "\u7b49\u5f85\u6570\u636e")
                        )
                        location_result, _, _, location_x, location_y, location_z, algorithm = location_tuple
                        if parse_error == 0:
                            parse_info = dict(parse_info)
                            parse_info.update({
                                "location_result": location_result,
                                "location_x": location_x,
                                "location_y": location_y,
                                "location_z": location_z,
                                "algorithm": algorithm,
                            })
                            batcher.add_packet(parse_info, location_tuple, algorithm)
                        else:
                            batcher.add_algorithm(algorithm)
                    except Exception as exc:
                        logger.warning("Process TCP packet failed: %s", exc)
                        batcher.add_algorithm("\u6570\u636e\u5e27\u5904\u7406\u5931\u8d25")
                        continue
                batcher.flush()
        except OSError as exc:
            if not self.socketClosed:
                logger.warning("%s", exc)
        finally:
            batcher.flush(force=True)
            try:
                client.close()
            except OSError:
                pass

    def accept_client(self):
        """Accept clients in a loop and give each client its own worker thread."""
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
        """Close the listening socket and request all receive loops to stop."""
        self.socketClosed = True
        if self.g_socket_server is not None:
            try:
                self.g_socket_server.close()
            except OSError:
                pass
            self.g_socket_server = None

    def is_open(self):
        """Return True when the TCP listening socket is active."""
        return not self.socketClosed and self.g_socket_server is not None


class SERIAL_SERVER(QtCore.QThread):
    """Serial-port transport with the same signal contract as TCP_SERVER."""

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
        """Open the configured serial port with 8N1 framing."""
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
            timeout=SERIAL_READ_TIMEOUT_S,
        )
        if hasattr(self.serial_port, "set_buffer_size"):
            try:
                self.serial_port.set_buffer_size(
                    rx_size=SERIAL_RX_BUFFER_SIZE,
                    tx_size=SERIAL_TX_BUFFER_SIZE,
                )
            except (AttributeError, ValueError, serial.SerialException):
                pass
        self.running = True

    def read_loop(self):
        """Read serial bytes, split packets, parse, locate, and emit UI signals."""
        buffer = bytearray()
        batcher = SignalBatcher(self)
        self.status_message.emit("COM: %s 已打开" % self.port)
        try:
            while self.running and self.serial_port is not None and self.serial_port.is_open:
                try:
                    waiting = self.serial_port.in_waiting
                    read_size = min(max(1, waiting), SERIAL_READ_MAX_BYTES)
                    recv_bytes = self.serial_port.read(read_size)
                except (OSError, serial.SerialException) as exc:
                    self.status_message.emit("COM: %s" % exc)
                    break

                if not recv_bytes:
                    batcher.flush()
                    continue

                batcher.add_raw(TCP_SERVER.format_raw_data(recv_bytes))
                buffer.extend(recv_bytes)
                packets, buffer = extract_packets(buffer)
                buffer = bytearray(buffer)

                for packet in packets:
                    try:
                        parse_error, parse_info = Process_String_Before_Udp(packet)
                        location_tuple = (
                            Compute_Location(parse_info)
                            if parse_error == 0
                            else (0, 0, 0, 0, 0, 0, "\u7b49\u5f85\u6570\u636e")
                        )
                        location_result, _, _, location_x, location_y, location_z, algorithm = location_tuple
                        if parse_error == 0:
                            parse_info = dict(parse_info)
                            parse_info.update({
                                "location_result": location_result,
                                "location_x": location_x,
                                "location_y": location_y,
                                "location_z": location_z,
                                "algorithm": algorithm,
                            })
                            batcher.add_packet(parse_info, location_tuple, algorithm)
                        else:
                            batcher.add_algorithm(algorithm)
                    except Exception as exc:
                        logger.warning("Process SERIAL packet failed: %s", exc)
                        batcher.add_algorithm("\u6570\u636e\u5e27\u5904\u7406\u5931\u8d25")
                        continue
                batcher.flush()
        finally:
            batcher.flush(force=True)
            self.serial_close()
            self.status_message.emit("COM: 未打开")

    def serial_close(self):
        """Stop the serial loop and close the port if it is open."""
        self.running = False
        if self.serial_port is not None:
            try:
                if self.serial_port.is_open:
                    self.serial_port.close()
            except (OSError, serial.SerialException):
                pass
            self.serial_port = None

    def is_open(self):
        """Return True when the serial port is open and the read loop is active."""
        return self.running and self.serial_port is not None and self.serial_port.is_open
