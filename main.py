import csv
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import can
from can import CanError
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

PROFILE_PATH = Path(__file__).with_name("profiles.json")
LOG_DIR = Path(__file__).with_name("logs")

PID_LABELS = {
    0x04: "Engine Load",
    0x05: "Coolant Temp",
    0x0C: "Engine RPM",
    0x0D: "Vehicle Speed",
    0x0F: "Intake Air Temp",
    0x11: "Throttle Position",
    0x2F: "Fuel Level",
}


@dataclass
class AppSettings:
    com_port: str
    bustype: str
    bitrate: int
    request_id: int
    response_min: int
    response_max: int
    timeout_sec: float
    poll_interval_sec: float
    mode: int
    pids: List[int]
    auto_reconnect: bool
    reconnect_delay_sec: float
    simulate_mode: bool
    alarm_enabled: bool
    alarm_rpm: int
    alarm_temp: int


def parse_hex_or_decimal(text: str) -> Optional[int]:
    text = text.strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text, 10)
    except ValueError:
        return None


def parse_pids(text: str) -> List[int]:
    pids: List[int] = []
    for item in [x.strip() for x in text.split(",") if x.strip()]:
        val = parse_hex_or_decimal(item)
        if val is None or not 0 <= val <= 0xFF:
            raise ValueError(f"Invalid PID: {item}")
        pids.append(val)
    if not pids:
        raise ValueError("At least one PID is required.")
    return pids


def decode_pid(pid: int, data: List[int]) -> Tuple[str, str]:
    if len(data) < 4:
        return PID_LABELS.get(pid, f"PID 0x{pid:02X}"), "Not enough data"
    if pid == 0x0C and len(data) >= 5:
        return "Engine RPM", f"{((data[3] * 256) + data[4]) / 4.0:.0f} rpm"
    if pid == 0x0D:
        return "Vehicle Speed", f"{data[3]} km/h"
    if pid == 0x05:
        return "Coolant Temp", f"{data[3] - 40} C"
    if pid == 0x0F:
        return "Intake Air Temp", f"{data[3] - 40} C"
    if pid == 0x11:
        return "Throttle Position", f"{(data[3] * 100.0) / 255.0:.1f} %"
    if pid == 0x04:
        return "Engine Load", f"{(data[3] * 100.0) / 255.0:.1f} %"
    if pid == 0x2F:
        return "Fuel Level", f"{(data[3] * 100.0) / 255.0:.1f} %"
    return PID_LABELS.get(pid, f"PID 0x{pid:02X}"), " ".join(f"{x:02X}" for x in data)


class PollWorker(QObject):
    frame_received = pyqtSignal(int, str)
    measurement = pyqtSignal(int, str, str, float)
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    alert = pyqtSignal(str)
    stat = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self._running = True
        self._paused = False
        self._bus: Optional[can.Bus] = None
        self._rng = random.Random()
        self.total_frames = 0
        self.total_requests = 0

    def stop(self):
        self._running = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    @staticmethod
    def _build_request(can_id: int, mode: int, pid: int) -> can.Message:
        return can.Message(arbitration_id=can_id, data=[0x02, mode, pid, 0, 0, 0, 0, 0], is_extended_id=False)

    def _connect_bus(self):
        self.status.emit("Connecting to CAN bus...")
        self._bus = can.interface.Bus(
            bustype=self.settings.bustype,
            channel=self.settings.com_port,
            bitrate=self.settings.bitrate,
        )
        self.status.emit("Connected")

    def _shutdown_bus(self):
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                pass
        self._bus = None

    def _check_alarms(self, pid: int, value: str):
        if not self.settings.alarm_enabled:
            return
        try:
            n = float(value.split()[0])
        except Exception:
            return
        if pid == 0x0C and n > self.settings.alarm_rpm:
            self.alert.emit(f"High RPM alert: {n:.0f} > {self.settings.alarm_rpm}")
        if pid == 0x05 and n > self.settings.alarm_temp:
            self.alert.emit(f"High coolant alert: {n:.0f} > {self.settings.alarm_temp}")

    def _emit_stat(self):
        self.stat.emit("Frames", str(self.total_frames))
        self.stat.emit("Requests", str(self.total_requests))

    def _run_simulated_cycle(self):
        for pid in self.settings.pids:
            if not self._running:
                break
            if self._paused:
                time.sleep(0.05)
                continue
            now = time.time()
            if pid == 0x0C:
                value = f"{self._rng.randint(700, 8500)} rpm"
                label = "Engine RPM"
            elif pid == 0x0D:
                value = f"{self._rng.randint(0, 399)} km/h"
                label = "Vehicle Speed"
            elif pid == 0x05:
                value = f"{self._rng.randint(70, 130)} C"
                label = "Coolant Temp"
            elif pid == 0x11:
                value = f"{self._rng.randint(0, 100)}.0 %"
                label = "Throttle Position"
            else:
                value = f"{self._rng.randint(0, 255)}"
                label = PID_LABELS.get(pid, f"PID 0x{pid:02X}")
            self.measurement.emit(pid, label, value, now)
            self.frame_received.emit(0x7E8, f"SIM RX PID 0x{pid:02X} => {value}")
            self._check_alarms(pid, value)
            self.total_frames += 1
            self.total_requests += 1
            self._emit_stat()
            time.sleep(self.settings.poll_interval_sec)

    def _run_real_cycle(self):
        if self._bus is None:
            self._connect_bus()
        for pid in self.settings.pids:
            if not self._running:
                return
            while self._paused and self._running:
                time.sleep(0.05)
            req = self._build_request(self.settings.request_id, self.settings.mode & 0xFF, pid)
            self.total_requests += 1
            try:
                self._bus.send(req)
                self.frame_received.emit(req.arbitration_id, "TX " + " ".join(f"{b:02X}" for b in req.data))
            except CanError as exc:
                raise RuntimeError(f"Failed to send PID 0x{pid:02X}: {exc}") from exc

            start = time.time()
            got_response = False
            while self._running and (time.time() - start) <= self.settings.timeout_sec:
                msg = self._bus.recv(timeout=0.05)
                if msg is None:
                    continue
                self.total_frames += 1
                self.frame_received.emit(msg.arbitration_id, "RX " + " ".join(f"{b:02X}" for b in msg.data))
                if not (self.settings.response_min <= msg.arbitration_id <= self.settings.response_max):
                    continue
                if len(msg.data) < 3:
                    continue
                expected_mode = (self.settings.mode + 0x40) & 0xFF
                if msg.data[1] == expected_mode and msg.data[2] == pid:
                    label, value = decode_pid(pid, list(msg.data))
                    self.measurement.emit(pid, label, value, time.time())
                    self._check_alarms(pid, value)
                    got_response = True
                    break
            if not got_response:
                label = PID_LABELS.get(pid, f"PID 0x{pid:02X}")
                self.measurement.emit(pid, label, "No response", time.time())
            self._emit_stat()
            time.sleep(self.settings.poll_interval_sec)

    def run(self):
        try:
            while self._running:
                try:
                    if self.settings.simulate_mode:
                        self.status.emit("Simulated mode active")
                        self._run_simulated_cycle()
                    else:
                        self._run_real_cycle()
                except Exception as exc:
                    self.error.emit(str(exc))
                    self._shutdown_bus()
                    if self.settings.auto_reconnect and self._running and not self.settings.simulate_mode:
                        self.status.emit(f"Reconnect in {self.settings.reconnect_delay_sec:.1f}s")
                        time.sleep(self.settings.reconnect_delay_sec)
                        continue
                    break
        finally:
            self._shutdown_bus()
            self.status.emit("Stopped")
            self.finished.emit()


class TelemetryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("E-Motion BMW OBD Pro Dashboard")
        self.resize(1450, 860)
        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[PollWorker] = None
        self.latest_values: Dict[int, Tuple[str, str]] = {}
        self.theme_name = "dark"
        self.csv_file = None
        self.csv_writer = None
        self.csv_path: Optional[Path] = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)
        root.addWidget(self._create_left_panel(), 0)
        root.addWidget(self._create_right_panel(), 1)

        self._apply_theme()
        self.refresh_ports()
        self._seed_pid_presets()
        self._load_profiles_from_disk()

    def _create_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("controlPanel")
        panel.setMinimumWidth(430)
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)

        title = QLabel("Connection + Controls")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        bus_group = QGroupBox("Bus / OBD")
        form = QFormLayout(bus_group)
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(self.refresh_btn)
        port_widget = QWidget()
        port_widget.setLayout(port_row)
        form.addRow("COM Port:", port_widget)

        self.bustype_combo = QComboBox()
        self.bustype_combo.addItems(["slcan", "socketcan", "pcan", "vector"])
        form.addRow("Bus Type:", self.bustype_combo)
        self.bitrate_spin = QSpinBox()
        self.bitrate_spin.setRange(10000, 1000000)
        self.bitrate_spin.setSingleStep(10000)
        self.bitrate_spin.setValue(500000)
        form.addRow("Bitrate:", self.bitrate_spin)
        self.request_id_input = QLineEdit("0x7DF")
        self.response_min_input = QLineEdit("0x7E8")
        self.response_max_input = QLineEdit("0x7EF")
        self.mode_input = QLineEdit("0x01")
        form.addRow("Request ID:", self.request_id_input)
        form.addRow("Response ID Min:", self.response_min_input)
        form.addRow("Response ID Max:", self.response_max_input)
        form.addRow("OBD Mode:", self.mode_input)
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.05, 10.0)
        self.timeout_spin.setDecimals(2)
        self.timeout_spin.setValue(1.0)
        self.poll_spin = QDoubleSpinBox()
        self.poll_spin.setRange(0.01, 3.0)
        self.poll_spin.setDecimals(2)
        self.poll_spin.setValue(0.2)
        form.addRow("Response Timeout (s):", self.timeout_spin)
        form.addRow("Poll Interval (s):", self.poll_spin)
        layout.addWidget(bus_group)

        pid_group = QGroupBox("PID Manager")
        pid_box = QVBoxLayout(pid_group)
        self.pid_presets = QListWidget()
        pid_box.addWidget(self.pid_presets)
        self.custom_pid_input = QLineEdit("0x0C,0x0D,0x05,0x11,0x2F")
        pid_box.addWidget(QLabel("Custom PIDs (comma separated):"))
        pid_box.addWidget(self.custom_pid_input)
        self.quick_pid_input = QLineEdit("0x00")
        self.add_pid_btn = QPushButton("Quick Add PID to List")
        self.add_pid_btn.clicked.connect(self.quick_add_pid)
        pid_box.addWidget(self.quick_pid_input)
        pid_box.addWidget(self.add_pid_btn)
        self.use_presets_check = QCheckBox("Use checked presets")
        pid_box.addWidget(self.use_presets_check)
        layout.addWidget(pid_group)

        advanced_group = QGroupBox("Advanced")
        adv_form = QFormLayout(advanced_group)
        self.simulate_check = QCheckBox("Simulated mode (no hardware)")
        self.auto_reconnect_check = QCheckBox("Auto reconnect")
        self.auto_reconnect_check.setChecked(True)
        self.reconnect_spin = QDoubleSpinBox()
        self.reconnect_spin.setRange(0.2, 10.0)
        self.reconnect_spin.setDecimals(1)
        self.reconnect_spin.setValue(1.5)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light"])
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
        self.csv_check = QCheckBox("Session CSV logging")
        self.alarm_enable = QCheckBox("Enable alarms")
        self.alarm_rpm_spin = QSpinBox()
        self.alarm_rpm_spin.setRange(1000, 9000)
        self.alarm_rpm_spin.setValue(3500)
        self.alarm_temp_spin = QSpinBox()
        self.alarm_temp_spin.setRange(50, 180)
        self.alarm_temp_spin.setValue(105)
        adv_form.addRow(self.simulate_check)
        adv_form.addRow(self.auto_reconnect_check)
        adv_form.addRow("Reconnect Delay (s):", self.reconnect_spin)
        adv_form.addRow(self.csv_check)
        adv_form.addRow(self.alarm_enable)
        adv_form.addRow("RPM Alarm >", self.alarm_rpm_spin)
        adv_form.addRow("Coolant Alarm >", self.alarm_temp_spin)
        adv_form.addRow("Theme:", self.theme_combo)
        layout.addWidget(advanced_group)

        profile_group = QGroupBox("Profiles")
        profile_layout = QVBoxLayout(profile_group)
        self.profile_combo = QComboBox()
        profile_layout.addWidget(self.profile_combo)
        p1 = QHBoxLayout()
        self.save_profile_btn = QPushButton("Save")
        self.load_profile_btn = QPushButton("Load")
        self.delete_profile_btn = QPushButton("Delete")
        p1.addWidget(self.save_profile_btn)
        p1.addWidget(self.load_profile_btn)
        p1.addWidget(self.delete_profile_btn)
        profile_layout.addLayout(p1)
        p2 = QHBoxLayout()
        self.export_profiles_btn = QPushButton("Export JSON")
        self.import_profiles_btn = QPushButton("Import JSON")
        p2.addWidget(self.export_profiles_btn)
        p2.addWidget(self.import_profiles_btn)
        profile_layout.addLayout(p2)
        self.save_profile_btn.clicked.connect(self.save_profile)
        self.load_profile_btn.clicked.connect(self.load_profile)
        self.delete_profile_btn.clicked.connect(self.delete_profile)
        self.export_profiles_btn.clicked.connect(self.export_profiles)
        self.import_profiles_btn.clicked.connect(self.import_profiles)
        layout.addWidget(profile_group)

        diag_group = QGroupBox("Diagnostics")
        diag_row = QHBoxLayout(diag_group)
        self.read_dtc_btn = QPushButton("Read DTC (03)")
        self.clear_dtc_btn = QPushButton("Clear DTC (04)")
        self.snapshot_btn = QPushButton("Snapshot")
        self.read_dtc_btn.clicked.connect(self.read_dtc_codes)
        self.clear_dtc_btn.clicked.connect(self.clear_dtc_codes)
        self.snapshot_btn.clicked.connect(self.save_snapshot)
        diag_row.addWidget(self.read_dtc_btn)
        diag_row.addWidget(self.clear_dtc_btn)
        diag_row.addWidget(self.snapshot_btn)
        layout.addWidget(diag_group)

        run_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")
        self.pause_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_polling)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn.clicked.connect(self.stop_polling)
        run_row.addWidget(self.start_btn, 1)
        run_row.addWidget(self.pause_btn, 1)
        run_row.addWidget(self.stop_btn, 1)
        layout.addLayout(run_row)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)
        layout.addStretch()
        return panel

    def _create_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("livePanel")
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)
        title = QLabel("Live Telemetry + Analytics")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        cards = QGridLayout()
        self.rpm_card = self._build_metric_card("RPM", "--")
        self.speed_card = self._build_metric_card("Speed", "--")
        self.temp_card = self._build_metric_card("Coolant", "--")
        self.throttle_card = self._build_metric_card("Throttle", "--")
        self.fuel_card = self._build_metric_card("Fuel", "--")
        self.frames_card = self._build_metric_card("Frames", "0")
        cards.addWidget(self.rpm_card[0], 0, 0)
        cards.addWidget(self.speed_card[0], 0, 1)
        cards.addWidget(self.temp_card[0], 0, 2)
        cards.addWidget(self.throttle_card[0], 1, 0)
        cards.addWidget(self.fuel_card[0], 1, 1)
        cards.addWidget(self.frames_card[0], 1, 2)
        layout.addLayout(cards)

        gauge_grid = QGridLayout()
        self.rpm_gauge = self._build_gauge("RPM Gauge", 0, 8000)
        self.speed_gauge = self._build_gauge("Speed Gauge", 0, 260)
        self.temp_gauge = self._build_gauge("Coolant Gauge", -40, 140)
        self.throttle_gauge = self._build_gauge("Throttle Gauge", 0, 100)
        gauge_grid.addWidget(self.rpm_gauge[0], 0, 0)
        gauge_grid.addWidget(self.speed_gauge[0], 0, 1)
        gauge_grid.addWidget(self.temp_gauge[0], 1, 0)
        gauge_grid.addWidget(self.throttle_gauge[0], 1, 1)
        layout.addLayout(gauge_grid)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["PID", "Signal", "Value", "Updated"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        logs_row = QHBoxLayout()
        left_log = QVBoxLayout()
        left_log.addWidget(QLabel("Raw CAN Frames"))
        self.raw_log = QPlainTextEdit()
        self.raw_log.setReadOnly(True)
        self.raw_log.setMaximumBlockCount(800)
        left_log.addWidget(self.raw_log)
        right_log = QVBoxLayout()
        right_log.addWidget(QLabel("Events / Alerts"))
        self.event_log = QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(400)
        right_log.addWidget(self.event_log)
        logs_row.addLayout(left_log, 1)
        logs_row.addLayout(right_log, 1)
        layout.addLayout(logs_row, 1)
        return panel

    @staticmethod
    def _build_metric_card(title: str, value: str) -> Tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("metricCard")
        box = QVBoxLayout(card)
        name = QLabel(title)
        name.setObjectName("metricName")
        val = QLabel(value)
        val.setObjectName("metricValue")
        box.addWidget(name)
        box.addWidget(val)
        return card, val

    @staticmethod
    def _build_gauge(title: str, min_v: int, max_v: int) -> Tuple[QFrame, QProgressBar, QLabel]:
        card = QFrame()
        card.setObjectName("metricCard")
        box = QVBoxLayout(card)
        name = QLabel(title)
        name.setObjectName("metricName")
        bar = QProgressBar()
        bar.setRange(min_v, max_v)
        bar.setValue(min_v)
        bar.setFormat("%v")
        value = QLabel("--")
        value.setObjectName("metricName")
        box.addWidget(name)
        box.addWidget(bar)
        box.addWidget(value)
        return card, bar, value

    def _apply_theme(self):
        dark = self.theme_name == "dark"
        if dark:
            bg, panel, input_bg, text, border, accent, soft = (
                "#0f172a",
                "#111827",
                "#0b1220",
                "#e2e8f0",
                "#334155",
                "#2563eb",
                "#93c5fd",
            )
        else:
            bg, panel, input_bg, text, border, accent, soft = (
                "#f8fafc",
                "#ffffff",
                "#f1f5f9",
                "#0f172a",
                "#cbd5e1",
                "#2563eb",
                "#1d4ed8",
            )
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {bg}; color: {text}; }}
            QFrame#controlPanel, QFrame#livePanel {{ background: {panel}; border: 1px solid {border}; border-radius: 12px; }}
            QLabel#panelTitle {{ font-size: 18px; font-weight: 700; color: {text}; }}
            QLabel#statusLabel {{ color: {soft}; font-size: 13px; }}
            QGroupBox {{ border: 1px solid {border}; border-radius: 8px; margin-top: 6px; font-weight: 600; color: {text}; padding: 8px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
            QPushButton {{ background: {accent}; border: none; border-radius: 8px; min-height: 30px; color: #ffffff; font-weight: 600; padding: 4px 10px; }}
            QPushButton:disabled {{ background: #94a3b8; color: #e2e8f0; }}
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget, QPlainTextEdit, QProgressBar {{
                background: {input_bg}; border: 1px solid {border}; border-radius: 6px; color: {text};
            }}
            QFrame#metricCard {{ background: {input_bg}; border: 1px solid {border}; border-radius: 10px; padding: 8px; }}
            QLabel#metricName {{ color: {soft}; font-size: 12px; }}
            QLabel#metricValue {{ color: {text}; font-size: 22px; font-weight: 700; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
            """
        )

    def _seed_pid_presets(self):
        for pid, label in PID_LABELS.items():
            item = QListWidgetItem(f"0x{pid:02X} - {label}")
            item.setData(256, pid)
            item.setCheckState(Qt.CheckState.Checked if pid in [0x0C, 0x0D, 0x05, 0x11, 0x2F] else Qt.CheckState.Unchecked)
            self.pid_presets.addItem(item)

    def _log_event(self, text: str):
        self.event_log.appendPlainText(f"{time.strftime('%H:%M:%S')} | {text}")

    def on_theme_changed(self, theme: str):
        self.theme_name = theme
        self._apply_theme()

    def quick_add_pid(self):
        pid = parse_hex_or_decimal(self.quick_pid_input.text())
        if pid is None or not 0 <= pid <= 0xFF:
            QMessageBox.warning(self, "Invalid PID", "Use 0x00-0xFF or 0-255.")
            return
        current = [x.strip() for x in self.custom_pid_input.text().split(",") if x.strip()]
        token = f"0x{pid:02X}"
        if token not in [c.lower().replace("0x", "0x").upper().replace("X", "x") for c in current]:
            current.append(token)
            self.custom_pid_input.setText(",".join(current))
        self._log_event(f"Added PID {token} to custom list")

    def refresh_ports(self):
        self.port_combo.clear()
        try:
            ports = can.interfaces.serial.serial_can.detect_available_configs()
        except Exception:
            ports = []
        if ports:
            for cfg in ports:
                ch = str(cfg.get("channel", ""))
                if ch:
                    self.port_combo.addItem(ch)
        if self.port_combo.count() == 0:
            self.port_combo.addItem("COM3")

    def _get_checked_pids(self) -> List[int]:
        pids: List[int] = []
        for i in range(self.pid_presets.count()):
            item = self.pid_presets.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                pids.append(int(item.data(256)))
        return pids

    def _read_settings(self) -> AppSettings:
        request_id = parse_hex_or_decimal(self.request_id_input.text())
        response_min = parse_hex_or_decimal(self.response_min_input.text())
        response_max = parse_hex_or_decimal(self.response_max_input.text())
        mode = parse_hex_or_decimal(self.mode_input.text())
        if None in (request_id, response_min, response_max, mode):
            raise ValueError("IDs and mode must be valid numbers.")
        pids = self._get_checked_pids() if self.use_presets_check.isChecked() else parse_pids(self.custom_pid_input.text())
        if not pids:
            raise ValueError("Select at least one PID.")
        return AppSettings(
            com_port=self.port_combo.currentText().strip() or "COM3",
            bustype=self.bustype_combo.currentText(),
            bitrate=int(self.bitrate_spin.value()),
            request_id=request_id,
            response_min=response_min,
            response_max=response_max,
            timeout_sec=float(self.timeout_spin.value()),
            poll_interval_sec=float(self.poll_spin.value()),
            mode=mode,
            pids=pids,
            auto_reconnect=self.auto_reconnect_check.isChecked(),
            reconnect_delay_sec=float(self.reconnect_spin.value()),
            simulate_mode=self.simulate_check.isChecked(),
            alarm_enabled=self.alarm_enable.isChecked(),
            alarm_rpm=int(self.alarm_rpm_spin.value()),
            alarm_temp=int(self.alarm_temp_spin.value()),
        )

    def _serialize_settings(self) -> Dict:
        return asdict(self._read_settings())

    def _apply_settings(self, d: Dict):
        self.port_combo.setCurrentText(str(d.get("com_port", "COM3")))
        self.bustype_combo.setCurrentText(str(d.get("bustype", "slcan")))
        self.bitrate_spin.setValue(int(d.get("bitrate", 500000)))
        self.request_id_input.setText(hex(int(d.get("request_id", 0x7DF))))
        self.response_min_input.setText(hex(int(d.get("response_min", 0x7E8))))
        self.response_max_input.setText(hex(int(d.get("response_max", 0x7EF))))
        self.mode_input.setText(hex(int(d.get("mode", 0x01))))
        self.timeout_spin.setValue(float(d.get("timeout_sec", 1.0)))
        self.poll_spin.setValue(float(d.get("poll_interval_sec", 0.2)))
        self.custom_pid_input.setText(",".join(f"0x{int(x):02X}" for x in d.get("pids", [0x0C, 0x0D])))
        self.auto_reconnect_check.setChecked(bool(d.get("auto_reconnect", True)))
        self.reconnect_spin.setValue(float(d.get("reconnect_delay_sec", 1.5)))
        self.simulate_check.setChecked(bool(d.get("simulate_mode", False)))
        self.alarm_enable.setChecked(bool(d.get("alarm_enabled", False)))
        self.alarm_rpm_spin.setValue(int(d.get("alarm_rpm", 3500)))
        self.alarm_temp_spin.setValue(int(d.get("alarm_temp", 105)))

    def _load_profiles_from_disk(self):
        if PROFILE_PATH.exists():
            try:
                profiles = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            except Exception:
                profiles = {}
        else:
            profiles = {
                "BMW Default": {
                    "com_port": "COM3",
                    "bustype": "slcan",
                    "bitrate": 500000,
                    "request_id": 0x7DF,
                    "response_min": 0x7E8,
                    "response_max": 0x7EF,
                    "timeout_sec": 1.0,
                    "poll_interval_sec": 0.2,
                    "mode": 0x01,
                    "pids": [0x0C, 0x0D, 0x05, 0x11, 0x2F],
                    "auto_reconnect": True,
                    "reconnect_delay_sec": 1.5,
                    "simulate_mode": False,
                    "alarm_enabled": False,
                    "alarm_rpm": 3500,
                    "alarm_temp": 105,
                }
            }
            PROFILE_PATH.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        self.profile_combo.clear()
        for name in sorted(profiles.keys()):
            self.profile_combo.addItem(name, profiles[name])

    def _write_profiles(self):
        data = {self.profile_combo.itemText(i): self.profile_combo.itemData(i) for i in range(self.profile_combo.count())}
        PROFILE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save_profile(self):
        name = self.profile_combo.currentText().strip() or f"Profile {self.profile_combo.count() + 1}"
        try:
            payload = self._serialize_settings()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        idx = self.profile_combo.findText(name)
        if idx >= 0:
            self.profile_combo.setItemData(idx, payload)
        else:
            self.profile_combo.addItem(name, payload)
        self.profile_combo.setCurrentText(name)
        self._write_profiles()
        self._log_event(f"Profile saved: {name}")

    def load_profile(self):
        payload = self.profile_combo.currentData()
        if payload:
            self._apply_settings(payload)
            self._log_event(f"Profile loaded: {self.profile_combo.currentText()}")

    def delete_profile(self):
        idx = self.profile_combo.currentIndex()
        if idx < 0:
            return
        name = self.profile_combo.currentText()
        self.profile_combo.removeItem(idx)
        self._write_profiles()
        self._log_event(f"Profile deleted: {name}")

    def export_profiles(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Profiles", str(PROFILE_PATH), "JSON Files (*.json)")
        if not path:
            return
        self._write_profiles()
        Path(path).write_text(PROFILE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        self._log_event(f"Profiles exported to {path}")

    def import_profiles(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Profiles", str(PROFILE_PATH), "JSON Files (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self.profile_combo.clear()
        for k in sorted(data.keys()):
            self.profile_combo.addItem(k, data[k])
        self._write_profiles()
        self._log_event(f"Profiles imported from {path}")

    def _open_session_csv(self):
        LOG_DIR.mkdir(exist_ok=True)
        filename = datetime.now().strftime("telemetry_%Y%m%d_%H%M%S.csv")
        self.csv_path = LOG_DIR / filename
        self.csv_file = open(self.csv_path, "a", newline="", encoding="utf-8")
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow(["timestamp", "pid", "signal", "value"])
        self._log_event(f"CSV session started: {self.csv_path.name}")

    def _close_session_csv(self):
        if self.csv_file:
            self.csv_file.close()
        if self.csv_path:
            self._log_event(f"CSV session closed: {self.csv_path.name}")
        self.csv_file = None
        self.csv_writer = None
        self.csv_path = None

    def _send_single_mode_request(self, mode: int) -> List[can.Message]:
        s = self._read_settings()
        req = can.Message(arbitration_id=s.request_id, data=[0x01, mode & 0xFF, 0, 0, 0, 0, 0, 0], is_extended_id=False)
        bus = can.interface.Bus(bustype=s.bustype, channel=s.com_port, bitrate=s.bitrate)
        out: List[can.Message] = []
        try:
            bus.send(req)
            start = time.time()
            while time.time() - start <= s.timeout_sec:
                msg = bus.recv(timeout=0.05)
                if msg is not None:
                    out.append(msg)
        finally:
            bus.shutdown()
        return out

    @staticmethod
    def _parse_dtc(msgs: List[can.Message]) -> List[str]:
        dtcs: List[str] = []
        for msg in msgs:
            d = list(msg.data)
            if len(d) < 3 or d[1] != 0x43:
                continue
            for i in range(3, len(d) - 1, 2):
                a, b = d[i], d[i + 1]
                if a == 0 and b == 0:
                    continue
                prefix = ["P", "C", "B", "U"][(a & 0xC0) >> 6]
                dtcs.append(f"{prefix}{(a & 0x30) >> 4:X}{a & 0x0F:X}{(b & 0xF0) >> 4:X}{b & 0x0F:X}")
        return dtcs

    def read_dtc_codes(self):
        try:
            msgs = self._send_single_mode_request(0x03)
            codes = self._parse_dtc(msgs)
        except Exception as exc:
            QMessageBox.critical(self, "DTC Error", str(exc))
            return
        if codes:
            QMessageBox.information(self, "DTC Codes", "\n".join(codes))
            self._log_event(f"DTC read: {len(codes)} code(s)")
        else:
            QMessageBox.information(self, "DTC Codes", "No DTC codes found.")
            self._log_event("DTC read: none")

    def clear_dtc_codes(self):
        try:
            self._send_single_mode_request(0x04)
            QMessageBox.information(self, "DTC Clear", "Clear DTC command sent.")
            self._log_event("DTC clear command sent")
        except Exception as exc:
            QMessageBox.critical(self, "DTC Error", str(exc))

    def save_snapshot(self):
        if not self.latest_values:
            QMessageBox.information(self, "Snapshot", "No live data yet.")
            return
        LOG_DIR.mkdir(exist_ok=True)
        path = LOG_DIR / f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        payload = {
            "timestamp": datetime.now().isoformat(),
            "signals": {f"0x{pid:02X}": {"name": name, "value": value} for pid, (name, value) in self.latest_values.items()},
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._log_event(f"Snapshot saved: {path.name}")

    def start_polling(self):
        try:
            settings = self._read_settings()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid input", str(exc))
            return
        self.raw_log.clear()
        self.table.setRowCount(0)
        self.latest_values.clear()
        if self.csv_check.isChecked():
            self._open_session_csv()
        else:
            self._close_session_csv()
        self._set_run_state(True)
        self.worker_thread = QThread(self)
        self.worker = PollWorker(settings)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.frame_received.connect(self.on_frame_received)
        self.worker.measurement.connect(self.on_measurement)
        self.worker.status.connect(self.on_status)
        self.worker.error.connect(self.on_error)
        self.worker.alert.connect(self.on_alert)
        self.worker.stat.connect(self.on_stat)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(lambda: self._set_run_state(False))
        self.worker_thread.start()
        self._log_event("Polling started")

    def stop_polling(self):
        if self.worker:
            self.worker.stop()
        self._close_session_csv()
        self._log_event("Polling stop requested")

    def toggle_pause(self):
        if not self.worker:
            return
        if self.pause_btn.text() == "Pause":
            self.worker.pause()
            self.pause_btn.setText("Resume")
            self.on_status("Paused")
            self._log_event("Polling paused")
        else:
            self.worker.resume()
            self.pause_btn.setText("Pause")
            self.on_status("Resumed")
            self._log_event("Polling resumed")

    def on_frame_received(self, can_id: int, payload: str):
        self.raw_log.appendPlainText(f"{time.strftime('%H:%M:%S')} | ID 0x{can_id:03X} | {payload}")

    def on_measurement(self, pid: int, label: str, value: str, ts: float):
        self.latest_values[pid] = (label, value)
        if self.csv_writer and self.csv_file:
            self.csv_writer.writerow([datetime.now().isoformat(), f"0x{pid:02X}", label, value])
            self.csv_file.flush()
        self._refresh_table(ts)
        self._refresh_cards()

    def on_stat(self, key: str, value: str):
        if key == "Frames":
            self.frames_card[1].setText(value)

    def _refresh_table(self, ts: float):
        self.table.setRowCount(len(self.latest_values))
        for row, (pid, (name, value)) in enumerate(self.latest_values.items()):
            pid_item = QTableWidgetItem(f"0x{pid:02X}")
            name_item = QTableWidgetItem(name)
            value_item = QTableWidgetItem(value)
            ts_item = QTableWidgetItem(time.strftime("%H:%M:%S", time.localtime(ts)))
            value_item.setForeground(QColor("#fda4af" if "No response" in value else "#86efac"))
            self.table.setItem(row, 0, pid_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, value_item)
            self.table.setItem(row, 3, ts_item)

    def _refresh_cards(self):
        rpm = self.latest_values.get(0x0C, ("", "--"))[1]
        speed = self.latest_values.get(0x0D, ("", "--"))[1]
        temp = self.latest_values.get(0x05, ("", "--"))[1]
        throttle = self.latest_values.get(0x11, ("", "--"))[1]
        fuel = self.latest_values.get(0x2F, ("", "--"))[1]
        self.rpm_card[1].setText(rpm)
        self.speed_card[1].setText(speed)
        self.temp_card[1].setText(temp)
        self.throttle_card[1].setText(throttle)
        self.fuel_card[1].setText(fuel)
        self._update_gauge(self.rpm_gauge, rpm)
        self._update_gauge(self.speed_gauge, speed)
        self._update_gauge(self.temp_gauge, temp)
        self._update_gauge(self.throttle_gauge, throttle)

    @staticmethod
    def _update_gauge(gauge: Tuple[QFrame, QProgressBar, QLabel], text: str):
        _, bar, label = gauge
        label.setText(text)
        try:
            bar.setValue(int(float(text.split()[0])))
        except Exception:
            pass

    def on_status(self, text: str):
        self.status_label.setText(text)

    def on_error(self, text: str):
        self.status_label.setText(f"Error: {text}")
        self._log_event(f"Error: {text}")

    def on_alert(self, text: str):
        self._log_event(text)

    def _set_run_state(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.pause_btn.setEnabled(running)
        self.pause_btn.setText("Pause")
        self.refresh_btn.setEnabled(not running)
        self.read_dtc_btn.setEnabled(not running)
        self.clear_dtc_btn.setEnabled(not running)
        self.snapshot_btn.setEnabled(not running)
        self.save_profile_btn.setEnabled(not running)
        self.load_profile_btn.setEnabled(not running)
        self.delete_profile_btn.setEnabled(not running)
        self.export_profiles_btn.setEnabled(not running)
        self.import_profiles_btn.setEnabled(not running)

    def closeEvent(self, event):  # noqa: N802
        self.stop_polling()
        if self.worker_thread and self.worker_thread.isRunning():
            self.worker_thread.quit()
            self.worker_thread.wait(1200)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = TelemetryWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
