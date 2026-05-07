"""
BambuLab Cloud API Client
Provides authentication, device management, and real-time MQTT monitoring
for BambuLab 3D printers via the Cloud API.

Requires: pip install bambu-lab-cloud-api

Usage:
    from garage_worker.bambulab import BambuAuthenticator, BambuClient, MQTTClient, PrinterState

    auth = BambuAuthenticator()
    token = auth.get_or_create_token(username="email@example.com", password="password")
    client = BambuClient(token=token)

    devices = client.get_devices()
    device_id = devices[0]['dev_id']
    uid = client.get_user_info()['uid']

    def on_message(device_id, data):
        state = PrinterState.from_mqtt_data(data)
        print(state)

    mqtt = MQTTClient(str(uid), token, device_id, on_message=on_message)
    mqtt.connect(blocking=False)
"""

import io
import logging
import os
import platform
import sys
import select
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

# Re-export from bambu-lab-cloud-api package
try:
    from bambulab import BambuAuthenticator, BambuClient, MQTTClient
except ImportError as e:
    raise ImportError(
        "bambu-lab-cloud-api package is required. Install with: pip install bambu-lab-cloud-api"
    ) from e


DEFAULT_TIMEZONE = os.getenv("BAMBULAB_TIMEZONE", "Australia/Melbourne")

logger = logging.getLogger(__name__)


@contextmanager
def suppress_stdout():
    """Context manager to suppress stdout (for silencing library print statements)"""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old_stdout


def timed_input(prompt: str, timeout_sec: int = 300) -> str:
    """
    Get user input with a timeout.

    Args:
        prompt: The prompt to display
        timeout_sec: Timeout in seconds (default 300 = 5 minutes)

    Returns:
        User input string

    Raises:
        TimeoutError: If no input received within timeout
    """


    print(prompt, end='', flush=True)

    if platform.system() == 'Windows':
        # Windows doesn't support select on stdin, use threading
        import threading
        result = {'value': None, 'done': False}

        def get_input():
            try:
                result['value'] = input()
            except EOFError:
                result['value'] = None
            result['done'] = True

        thread = threading.Thread(target=get_input, daemon=True)
        thread.start()
        thread.join(timeout=timeout_sec)

        if not result['done']:
            print()  # newline
            raise TimeoutError(f"No input received within {timeout_sec} seconds")
        return result['value'] or ""
    else:
        # Unix/Mac - use select
        ready, _, _ = select.select([sys.stdin], [], [], timeout_sec)
        if ready:
            return sys.stdin.readline().strip()
        else:
            print()  # newline
            raise TimeoutError(f"No input received within {timeout_sec} seconds")


@dataclass
class FilamentTray:
    """Represents a single filament tray in an AMS unit"""
    tray_id: str = ""
    tray_id_name: str = ""  # e.g., "A00-W1"
    tray_type: str = ""  # e.g., "PLA", "ABS", "PETG"
    tray_sub_brands: str = ""  # e.g., "PLA Basic", "PLA Matte"
    tray_color: str = ""  # hex color e.g., "FFFFFFFF"
    remain_percent: int = -1  # -1 if unknown
    tray_weight: int = 0  # grams (usually 1000)
    tray_diameter: float = 1.75  # mm
    tray_temp: int = 0  # recommended bed temp
    nozzle_temp_min: int = 0
    nozzle_temp_max: int = 0
    state: int = 0  # tray state code
    tag_uid: str = ""
    tray_uuid: str = ""
    # Flow calibration
    k: float = 0.0  # flow calibration K value
    n: float = 0.0  # calibration N value
    cali_idx: int = -1  # calibration index
    # Filament capacity
    total_len: int = 0  # total filament length in mm
    # Filament profile
    tray_info_idx: str = ""  # tray info index (e.g., "GFA00")
    tray_time: int = 0  # drying time recommendation
    tray_bed_temp: int = 0  # recommended bed temp for this filament
    bed_temp_type: int = 0  # bed temp type flag
    # Multi-color
    cols: List[str] = field(default_factory=list)  # color array for multi-color filaments

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FilamentTray":
        """Create FilamentTray from MQTT tray data"""
        return cls(
            tray_id=str(data.get("id", "")),
            tray_id_name=data.get("tray_id_name", ""),
            tray_type=data.get("tray_type", ""),
            tray_sub_brands=data.get("tray_sub_brands", ""),
            tray_color=data.get("tray_color", ""),
            remain_percent=data.get("remain", -1),
            tray_weight=int(data.get("tray_weight", 0)),
            tray_diameter=float(data.get("tray_diameter", 1.75)),
            tray_temp=int(data.get("tray_temp", 0)),
            nozzle_temp_min=int(data.get("nozzle_temp_min", 0)),
            nozzle_temp_max=int(data.get("nozzle_temp_max", 0)),
            state=data.get("state", 0),
            tag_uid=data.get("tag_uid", ""),
            tray_uuid=data.get("tray_uuid", ""),
            k=float(data.get("k", 0.0)),
            n=float(data.get("n", 0.0)),
            cali_idx=int(data.get("cali_idx", -1)),
            total_len=int(data.get("total_len", 0)),
            tray_info_idx=data.get("tray_info_idx", ""),
            tray_time=int(data.get("tray_time", 0)),
            tray_bed_temp=int(data.get("bed_temp", 0)),
            bed_temp_type=int(data.get("bed_temp_type", 0)),
            cols=data.get("cols", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            "tray_id": self.tray_id,
            "tray_id_name": self.tray_id_name,
            "tray_type": self.tray_type,
            "tray_sub_brands": self.tray_sub_brands,
            "tray_color": self.tray_color,
            "remain_percent": self.remain_percent,
            "tray_weight": self.tray_weight,
            "tray_diameter": self.tray_diameter,
            "tray_temp": self.tray_temp,
            "nozzle_temp_min": self.nozzle_temp_min,
            "nozzle_temp_max": self.nozzle_temp_max,
            "state": self.state,
            "tag_uid": self.tag_uid,
            "tray_uuid": self.tray_uuid,
            "k": self.k,
            "n": self.n,
            "cali_idx": self.cali_idx,
            "total_len": self.total_len,
            "tray_info_idx": self.tray_info_idx,
            "tray_time": self.tray_time,
            "tray_bed_temp": self.tray_bed_temp,
            "bed_temp_type": self.bed_temp_type,
            "cols": self.cols,
        }


@dataclass
class AMSUnit:
    """Represents a single AMS (Automatic Material System) unit"""
    ams_id: str = ""
    unit_id: str = ""  # 0, 1, 2, 3
    humidity: int = -1  # humidity level 1-5 (lower is better)
    humidity_raw: int = -1  # raw humidity percentage
    temp: float = 0.0  # internal temperature
    dry_time: int = 0  # remaining dry time
    chip_id: str = ""  # AMS hardware chip identifier
    info: str = ""  # AMS info string (e.g., "1001")
    trays: List[FilamentTray] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AMSUnit":
        """Create AMSUnit from MQTT ams data"""
        trays = [FilamentTray.from_dict(t) for t in data.get("tray", [])]
        return cls(
            ams_id=data.get("ams_id", ""),
            unit_id=str(data.get("id", "")),
            humidity=int(data.get("humidity", -1)),
            humidity_raw=int(data.get("humidity_raw", -1)),
            temp=float(data.get("temp", 0.0)),
            dry_time=data.get("dry_time", 0),
            chip_id=data.get("chip_id", ""),
            info=data.get("info", ""),
            trays=trays,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            "ams_id": self.ams_id,
            "unit_id": self.unit_id,
            "humidity": self.humidity,
            "humidity_raw": self.humidity_raw,
            "temp": self.temp,
            "dry_time": self.dry_time,
            "chip_id": self.chip_id,
            "info": self.info,
            "trays": [t.to_dict() for t in self.trays],
        }


@dataclass
class AMSState:
    """Complete AMS system state including all units"""
    ams_exist_bits: str = ""
    tray_exist_bits: str = ""
    tray_now: str = ""  # currently selected tray
    tray_pre: str = ""  # previous tray
    tray_tar: str = ""  # target tray
    ams_status: int = 0
    ams_rfid_status: int = 0
    tray_is_bbl_bits: str = ""  # which trays have Bambu Lab (OEM) filament
    tray_read_done_bits: str = ""  # RFID read completion status
    version: int = 0  # AMS firmware version
    insert_flag: bool = False  # filament insertion flag
    power_on_flag: bool = False  # AMS power on flag
    units: List[AMSUnit] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AMSState":
        """Create AMSState from MQTT ams data"""
        units = [AMSUnit.from_dict(u) for u in data.get("ams", [])]
        return cls(
            ams_exist_bits=data.get("ams_exist_bits", ""),
            tray_exist_bits=data.get("tray_exist_bits", ""),
            tray_now=data.get("tray_now", ""),
            tray_pre=data.get("tray_pre", ""),
            tray_tar=data.get("tray_tar", ""),
            ams_status=data.get("ams_status", 0),
            ams_rfid_status=data.get("ams_rfid_status", 0),
            tray_is_bbl_bits=data.get("tray_is_bbl_bits", ""),
            tray_read_done_bits=data.get("tray_read_done_bits", ""),
            version=int(data.get("version", 0)),
            insert_flag=bool(data.get("insert_flag", False)),
            power_on_flag=bool(data.get("power_on_flag", False)),
            units=units,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            "ams_exist_bits": self.ams_exist_bits,
            "tray_exist_bits": self.tray_exist_bits,
            "tray_now": self.tray_now,
            "tray_pre": self.tray_pre,
            "tray_tar": self.tray_tar,
            "ams_status": self.ams_status,
            "ams_rfid_status": self.ams_rfid_status,
            "tray_is_bbl_bits": self.tray_is_bbl_bits,
            "tray_read_done_bits": self.tray_read_done_bits,
            "version": self.version,
            "insert_flag": self.insert_flag,
            "power_on_flag": self.power_on_flag,
            "units": [u.to_dict() for u in self.units],
        }

    @property
    def total_trays(self) -> int:
        """Total number of trays across all units"""
        return sum(len(u.trays) for u in self.units)

    @property
    def loaded_trays(self) -> List[FilamentTray]:
        """Get all trays that have filament loaded"""
        loaded = []
        for unit in self.units:
            for tray in unit.trays:
                if tray.tray_type:  # has filament type means loaded
                    loaded.append(tray)
        return loaded


@dataclass
class PrinterState:
    """Complete printer state parsed from MQTT data"""
    # Timestamp
    timestamp: str = ""
    sequence_id: str = ""

    # Temperature info
    nozzle_temp: float = 0.0
    nozzle_target_temp: float = 0.0
    bed_temp: float = 0.0
    bed_target_temp: float = 0.0
    chamber_temp: float = 0.0

    # Print progress
    gcode_state: str = ""  # "IDLE", "RUNNING", "PAUSE", "FINISH", etc.
    print_percent: int = 0  # mc_percent (0-100)
    remaining_time_min: int = 0  # mc_remaining_time in minutes
    layer_num: int = 0
    total_layer_num: int = 0
    print_line_number: int = 0

    # Current job info
    gcode_file: str = ""
    subtask_name: str = ""
    subtask_id: str = ""
    task_id: str = ""
    project_id: str = ""
    profile_id: str = ""
    print_type: str = ""

    # Fan speeds
    fan_gear: int = 0
    cooling_fan_speed: int = 0  # part cooling fan (percentage)
    heatbreak_fan_speed: int = 0  # heatbreak fan (percentage)

    # WiFi / Network
    wifi_signal: str = ""  # e.g., "-34dBm"
    wifi_signal_dbm: int = 0

    # Nozzle info — single-nozzle / right-side back-compat fields.
    nozzle_diameter: float = 0.4
    nozzle_type: str = ""

    # H2C dual-nozzle: left-side fields (None on single-nozzle printers).
    nozzle_temp_left: Optional[float] = None
    nozzle_target_temp_left: Optional[float] = None
    nozzle_diameter_left: Optional[float] = None
    nozzle_type_left: Optional[str] = None

    # System status
    home_flag: int = 0
    hw_switch_state: int = 0
    mc_print_stage: str = ""
    mc_print_sub_stage: int = 0
    print_error: int = 0
    stg_cur: int = 0

    # AMS state
    ams: Optional[AMSState] = None

    # Upgrade state
    upgrade_state: Dict[str, Any] = field(default_factory=dict)

    # Version info
    version: Dict[str, Any] = field(default_factory=dict)

    # Camera / Timelapse
    ipcam: Dict[str, Any] = field(default_factory=dict)
    timelapse: Dict[str, Any] = field(default_factory=dict)

    # Lights
    lights_report: List[Dict[str, Any]] = field(default_factory=list)

    # HMS (Health Management System) messages
    hms: List[Dict[str, Any]] = field(default_factory=list)

    # Speed settings
    spd_lvl: int = 0
    spd_mag: int = 0

    # Auxiliary fans
    big_fan1_speed: int = 0  # auxiliary/chamber fan 1 speed
    big_fan2_speed: int = 0  # auxiliary/chamber fan 2 speed

    # System info
    sdcard: bool = False  # SD card present
    gcode_file_prepare_percent: str = ""  # file preparation progress
    lifecycle: str = ""  # product lifecycle state (e.g., "product")

    # External spool (virtual tray) - used when not using AMS
    vt_tray: Optional[Dict[str, Any]] = None

    # Raw data for any additional fields
    _raw_data: Dict[str, Any] = field(default_factory=dict, repr=False)

    @staticmethod
    def _parse_wifi_signal(signal_str: str) -> int:
        """Parse WiFi signal string (e.g., '-34dBm') to integer dBm"""
        if not signal_str:
            return 0
        try:
            return int(signal_str.replace("dBm", ""))
        except (ValueError, AttributeError):
            return 0

    @classmethod
    def from_mqtt_data(cls, data: Dict[str, Any], timestamp: Optional[str] = None) -> "PrinterState":
        """
        Create PrinterState from MQTT push_status data.

        Note: MQTT sends incremental updates. Fields not present in data
        will be set to their default values. Use PrinterStateAccumulator
        to maintain a complete state across multiple updates.
        """
        if timestamp is None:
            timestamp = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()

        print_data = data.get("print", {})

        # Parse AMS data if present
        ams = None
        if "ams" in print_data:
            ams = AMSState.from_dict(print_data["ams"])

        wifi_signal = print_data.get("wifi_signal", "")

        # H2C dual-nozzle decoding. The H2C reports per-extruder temperatures
        # under `print.device.extruder.info[]` as a 2-element array (index 0 =
        # right, index 1 = left). The `temp` field is bit-packed:
        # `temp_raw = (target << 16) | current`, both °C as ints.
        nozzle_temp_left = None
        nozzle_target_temp_left = None
        device_payload = print_data.get("device") or {}
        extruders = (device_payload.get("extruder") or {}).get("info") or []
        if len(extruders) >= 2:
            left = extruders[1]
            t = left.get("temp")
            if isinstance(t, int):
                nozzle_target_temp_left = float((t >> 16) & 0xFFFF)
                nozzle_temp_left = float(t & 0xFFFF)

        return cls(
            timestamp=timestamp,
            sequence_id=str(print_data.get("sequence_id", "")),

            # Temperatures
            nozzle_temp=float(print_data.get("nozzle_temper", 0.0)),
            nozzle_target_temp=float(print_data.get("nozzle_target_temper", 0.0)),
            bed_temp=float(print_data.get("bed_temper", 0.0)),
            bed_target_temp=float(print_data.get("bed_target_temper", 0.0)),
            chamber_temp=float(print_data.get("chamber_temper", 0.0)),

            # Print progress
            gcode_state=print_data.get("gcode_state", ""),
            print_percent=int(print_data.get("mc_percent", 0)),
            remaining_time_min=int(print_data.get("mc_remaining_time", 0)),
            layer_num=int(print_data.get("layer_num", 0)),
            total_layer_num=int(print_data.get("total_layer_num", 0)),
            print_line_number=int(print_data.get("mc_print_line_number", 0)),

            # Job info
            gcode_file=print_data.get("gcode_file", ""),
            subtask_name=print_data.get("subtask_name", ""),
            subtask_id=print_data.get("subtask_id", ""),
            task_id=print_data.get("task_id", ""),
            project_id=print_data.get("project_id", ""),
            profile_id=print_data.get("profile_id", ""),
            print_type=print_data.get("print_type", ""),

            # Fans
            fan_gear=int(print_data.get("fan_gear", 0)),
            cooling_fan_speed=int(print_data.get("cooling_fan_speed", 0)),
            heatbreak_fan_speed=int(print_data.get("heatbreak_fan_speed", 0)),

            # WiFi
            wifi_signal=wifi_signal,
            wifi_signal_dbm=cls._parse_wifi_signal(wifi_signal),

            # Nozzle (right / single-nozzle back-compat)
            nozzle_diameter=float(print_data.get("nozzle_diameter", 0.4)),
            nozzle_type=print_data.get("nozzle_type", ""),

            # Nozzle (left, H2C-class dual-nozzle). Diameter/type for both sides
            # currently mirror the top-level (uniform install). Plumb from
            # `device.nozzle.info[]` per-side if a future probe needs it.
            nozzle_temp_left=nozzle_temp_left,
            nozzle_target_temp_left=nozzle_target_temp_left,
            nozzle_diameter_left=float(print_data.get("nozzle_diameter", 0.4)) if nozzle_temp_left is not None else None,
            nozzle_type_left=print_data.get("nozzle_type", "") if nozzle_temp_left is not None else None,

            # System
            home_flag=int(print_data.get("home_flag", 0)),
            hw_switch_state=int(print_data.get("hw_switch_state", 0)),
            mc_print_stage=str(print_data.get("mc_print_stage", "")),
            mc_print_sub_stage=int(print_data.get("mc_print_sub_stage", 0)),
            print_error=int(print_data.get("print_error", 0)),
            stg_cur=int(print_data.get("stg_cur", 0)),

            # AMS
            ams=ams,

            # Additional data
            upgrade_state=print_data.get("upgrade_state", {}),
            version=print_data.get("version", {}),
            ipcam=print_data.get("ipcam", {}),
            timelapse=print_data.get("timelapse", {}),
            lights_report=print_data.get("lights_report", []),
            hms=print_data.get("hms", []),
            spd_lvl=int(print_data.get("spd_lvl", 0)),
            spd_mag=int(print_data.get("spd_mag", 0)),

            # Auxiliary fans
            big_fan1_speed=int(print_data.get("big_fan1_speed", 0)),
            big_fan2_speed=int(print_data.get("big_fan2_speed", 0)),

            # System info
            sdcard=bool(print_data.get("sdcard", False)),
            gcode_file_prepare_percent=str(print_data.get("gcode_file_prepare_percent", "")),
            lifecycle=print_data.get("lifecycle", ""),

            # External spool (virtual tray)
            vt_tray=print_data.get("vt_tray"),

            _raw_data=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage"""
        return {
            "timestamp": self.timestamp,
            "sequence_id": self.sequence_id,

            # Temperatures
            "nozzle_temp": self.nozzle_temp,
            "nozzle_target_temp": self.nozzle_target_temp,
            "bed_temp": self.bed_temp,
            "bed_target_temp": self.bed_target_temp,
            "chamber_temp": self.chamber_temp,

            # Print progress
            "gcode_state": self.gcode_state,
            "print_percent": self.print_percent,
            "remaining_time_min": self.remaining_time_min,
            "layer_num": self.layer_num,
            "total_layer_num": self.total_layer_num,
            "print_line_number": self.print_line_number,

            # Job info
            "gcode_file": self.gcode_file,
            "subtask_name": self.subtask_name,
            "subtask_id": self.subtask_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "profile_id": self.profile_id,
            "print_type": self.print_type,

            # Fans
            "fan_gear": self.fan_gear,
            "cooling_fan_speed": self.cooling_fan_speed,
            "heatbreak_fan_speed": self.heatbreak_fan_speed,

            # WiFi
            "wifi_signal": self.wifi_signal,
            "wifi_signal_dbm": self.wifi_signal_dbm,

            # Nozzle
            "nozzle_diameter": self.nozzle_diameter,
            "nozzle_type": self.nozzle_type,

            # System status
            "home_flag": self.home_flag,
            "hw_switch_state": self.hw_switch_state,
            "mc_print_stage": self.mc_print_stage,
            "mc_print_sub_stage": self.mc_print_sub_stage,
            "print_error": self.print_error,
            "stg_cur": self.stg_cur,

            # AMS
            "ams": self.ams.to_dict() if self.ams else None,

            # Speed
            "spd_lvl": self.spd_lvl,
            "spd_mag": self.spd_mag,

            # Additional
            "upgrade_state": self.upgrade_state,
            "version": self.version,
            "ipcam": self.ipcam,
            "timelapse": self.timelapse,
            "lights_report": self.lights_report,
            "hms": self.hms,
        }

    def get_snapshot(self) -> Dict[str, Any]:
        """
        Get a simplified snapshot for database logging.
        Similar to SynologySampler.get_system_snapshot().
        Returns only the most important metrics for time-series data.
        """
        snapshot = {
            "timestamp": self.timestamp,

            # Key temperatures
            "nozzle_temp": round(self.nozzle_temp, 2),
            "nozzle_target_temp": round(self.nozzle_target_temp, 2),
            "bed_temp": round(self.bed_temp, 2),
            "bed_target_temp": round(self.bed_target_temp, 2),
            "chamber_temp": round(self.chamber_temp, 2),

            # Nozzle info — single-nozzle / right-side back-compat
            "nozzle_diameter": self.nozzle_diameter,
            "nozzle_type": self.nozzle_type,

            # H2C dual-nozzle: left-side fields (None on single-nozzle printers)
            "nozzle_temp_left": (
                round(self.nozzle_temp_left, 2) if self.nozzle_temp_left is not None else None
            ),
            "nozzle_target_temp_left": (
                round(self.nozzle_target_temp_left, 2) if self.nozzle_target_temp_left is not None else None
            ),
            "nozzle_diameter_left": self.nozzle_diameter_left,
            "nozzle_type_left": self.nozzle_type_left,

            # Print state
            "gcode_state": self.gcode_state,
            "print_type": self.print_type,
            "print_percent": self.print_percent,
            "remaining_time_min": self.remaining_time_min,
            "layer_num": self.layer_num,
            "total_layer_num": self.total_layer_num,
            "print_line_number": self.print_line_number,

            # Current job
            "subtask_name": self.subtask_name,
            "gcode_file": self.gcode_file,

            # Fans
            "cooling_fan_speed": self.cooling_fan_speed,
            "heatbreak_fan_speed": self.heatbreak_fan_speed,
            "big_fan1_speed": self.big_fan1_speed,
            "big_fan2_speed": self.big_fan2_speed,

            # Speed settings
            "spd_lvl": self.spd_lvl,
            "spd_mag": self.spd_mag,

            # Network
            "wifi_signal_dbm": self.wifi_signal_dbm,

            # Errors
            "print_error": self.print_error,
            "has_errors": self.print_error != 0,
            "hms": self.hms,
            "stg_cur": self.stg_cur,

            # Lights
            "lights_report": self.lights_report,
            "chamber_light": self._get_chamber_light_status(),

            # IP Camera
            "ipcam_record": self.ipcam.get("ipcam_record", ""),
            "timelapse": self.ipcam.get("timelapse", ""),

            # System info
            "sdcard": self.sdcard,
            "gcode_file_prepare_percent": self.gcode_file_prepare_percent,
            "lifecycle": self.lifecycle,
        }

        # Add AMS summary if available
        if self.ams:
            snapshot["ams_unit_count"] = len(self.ams.units)
            snapshot["ams_status"] = self.ams.ams_status
            snapshot["ams_rfid_status"] = self.ams.ams_rfid_status
            snapshot["ams_exist_bits"] = self.ams.ams_exist_bits
            snapshot["tray_exist_bits"] = self.ams.tray_exist_bits
            snapshot["tray_is_bbl_bits"] = self.ams.tray_is_bbl_bits
            snapshot["tray_read_done_bits"] = self.ams.tray_read_done_bits
            snapshot["tray_now"] = self.ams.tray_now
            snapshot["ams_version"] = self.ams.version

            # Resolve AMS unit type from MQTT model code (info field)
            try:
                from bambu_run.models import ams_type_from_info  # type: ignore
            except Exception:
                # Standalone use without bambu_run installed — fallback inline.
                _ams_info_to_type = {"1001": "AMS", "1003": "AMS 2 Pro", "2104": "AMS HT"}
                def ams_type_from_info(info_code):  # noqa: E306
                    return _ams_info_to_type.get(str(info_code), "") if info_code is not None else ""

            # Summarize filament info with all available details
            filaments = []
            for unit in self.ams.units:
                # Resolve unit_id once. AMS HT reports id with the 0x80 bit set
                # (e.g. 128) — don't compute tray_id // 4 on the receiving side.
                try:
                    unit_id_int = int(unit.unit_id)
                except (TypeError, ValueError):
                    unit_id_int = None
                ams_type_label = ams_type_from_info(unit.info)
                for tray in unit.trays:
                    if tray.tray_type:  # has filament
                        filaments.append({
                            "tray_id": tray.tray_id,
                            "slot": tray.tray_id_name,
                            "type": tray.tray_type,
                            "sub_type": tray.tray_sub_brands,
                            "color": tray.tray_color,
                            "remain_percent": tray.remain_percent,
                            "tray_weight": tray.tray_weight,
                            "tray_diameter": tray.tray_diameter,
                            "nozzle_temp_min": tray.nozzle_temp_min,
                            "nozzle_temp_max": tray.nozzle_temp_max,
                            "tag_uid": tray.tag_uid,
                            "state": tray.state,
                            "tray_uuid": tray.tray_uuid,
                            # Flow calibration
                            "k": tray.k,
                            "n": tray.n,
                            "cali_idx": tray.cali_idx,
                            # Filament capacity & profile
                            "total_len": tray.total_len,
                            "tray_info_idx": tray.tray_info_idx,
                            "tray_time": tray.tray_time,
                            "tray_bed_temp": tray.tray_bed_temp,
                            "bed_temp_type": tray.bed_temp_type,
                            "cols": tray.cols,
                            # H2C / multi-AMS metadata
                            "ams_unit_id": unit_id_int,
                            "ams_info": unit.info,
                            "ams_type": ams_type_label,
                        })
            snapshot["filaments"] = filaments

            # AMS units info (chip_id, humidity per unit, etc.)
            ams_units = []
            for unit in self.ams.units:
                ams_units.append({
                    "unit_id": unit.unit_id,
                    "ams_id": unit.ams_id,
                    "chip_id": unit.chip_id,
                    "info": unit.info,
                    "ams_type": ams_type_from_info(unit.info),
                    "humidity": unit.humidity,
                    "humidity_raw": unit.humidity_raw,
                    "temp": unit.temp,
                    "dry_time": unit.dry_time,
                })
            snapshot["ams_units"] = ams_units

            # AMS environment (first unit for backwards compat)
            if self.ams.units:
                snapshot["ams_humidity"] = self.ams.units[0].humidity
                snapshot["ams_humidity_raw"] = self.ams.units[0].humidity_raw
                snapshot["ams_temp"] = self.ams.units[0].temp

        # External spool (virtual tray) - when not using AMS
        if self.vt_tray:
            snapshot["external_spool"] = {
                "type": self.vt_tray.get("tray_type", ""),
                "color": self.vt_tray.get("tray_color", ""),
                "remain": self.vt_tray.get("remain", 0),
            }

        return snapshot

    def _get_chamber_light_status(self) -> str:
        """Extract chamber light status from lights_report"""
        for light in self.lights_report:
            if light.get("node") == "chamber_light":
                return light.get("mode", "unknown")
        return "unknown"

    @property
    def is_printing(self) -> bool:
        """Check if printer is currently printing"""
        return self.gcode_state.upper() in ("RUNNING", "PRINTING")

    @property
    def is_idle(self) -> bool:
        """Check if printer is idle"""
        return self.gcode_state.upper() in ("IDLE", "FINISH", "")

    @property
    def is_paused(self) -> bool:
        """Check if print is paused"""
        return self.gcode_state.upper() == "PAUSE"


class PrinterStateAccumulator:
    """
    Accumulates MQTT updates into a complete printer state.

    BambuLab MQTT sends incremental updates - each message may only contain
    a subset of fields that have changed. This class maintains the complete
    state by merging updates.

    Usage:
        accumulator = PrinterStateAccumulator()

        def on_message(device_id, data):
            state = accumulator.update(data)
            print(f"Print progress: {state.print_percent}%")
    """

    def __init__(self):
        self._state_data: Dict[str, Any] = {"print": {}}
        self._last_update: Optional[str] = None
        self._update_count: int = 0

    def update(self, data: Dict[str, Any]) -> PrinterState:
        """
        Merge new MQTT data into accumulated state and return complete PrinterState.

        Args:
            data: Raw MQTT message data

        Returns:
            Complete PrinterState with all accumulated values
        """
        timestamp = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()
        self._last_update = timestamp
        self._update_count += 1

        # Deep merge the print data
        if "print" in data:
            self._deep_merge(self._state_data["print"], data["print"])

        return PrinterState.from_mqtt_data(self._state_data, timestamp)

    def _deep_merge(self, base: Dict, update: Dict) -> None:
        """Recursively merge update into base dict"""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def get_state(self) -> PrinterState:
        """Get current accumulated state without updating"""
        timestamp = self._last_update or datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).isoformat()
        return PrinterState.from_mqtt_data(self._state_data, timestamp)

    def reset(self) -> None:
        """Reset accumulated state"""
        self._state_data = {"print": {}}
        self._last_update = None
        self._update_count = 0

    @property
    def update_count(self) -> int:
        """Number of updates received"""
        return self._update_count

    @property
    def last_update(self) -> Optional[str]:
        """Timestamp of last update"""
        return self._last_update


class BambuPrinter:
    """
    High-level interface for BambuLab printer monitoring.
    Combines authentication, client, and MQTT into a single interface.

    Features:
    - Automatic token refresh when expired
    - Suppresses stdout prints from underlying library (safe for Django/background)
    - Auto-reconnect on connection errors

    Usage:
        # For Django background runner (fully automated):
        printer = BambuPrinter(
            username="email",
            password="pass",
            silent=True  # Suppresses all library prints
        )
        printer.connect()

        # Get current state
        state = printer.get_state()
        snapshot = printer.get_snapshot()  # For database

        printer.disconnect()

    Environment variables (alternative to constructor args):
        BAMBU_USERNAME, BAMBU_PASSWORD, BAMBU_TOKEN, BAMBU_DEVICE_ID
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
        device_id: Optional[str] = None,
        on_update: Optional[Callable[[PrinterState], None]] = None,
        silent: bool = True,
        verification_timeout: int = 300,
    ):
        """
        Initialize BambuPrinter.

        Args:
            username: BambuLab account email (or BAMBU_USERNAME env var)
            password: BambuLab account password (or BAMBU_PASSWORD env var)
            token: Pre-obtained token (optional, skips initial authentication)
            device_id: Specific device ID to monitor (optional, uses first device)
            on_update: Callback function called on each MQTT update
            silent: If True, suppress stdout prints from library (default: True)
            verification_timeout: Seconds to wait for 2FA code input (default: 300)
        """
        self.username = username or os.getenv("BAMBU_USERNAME")
        self.password = password or os.getenv("BAMBU_PASSWORD")
        self._token = token or os.getenv("BAMBU_TOKEN")
        self._device_id = device_id or os.getenv("BAMBU_DEVICE_ID")
        self._uid: Optional[str] = None
        self._on_update = on_update
        self._silent = silent
        self._verification_timeout = verification_timeout

        self._client: Optional[BambuClient] = None
        self._mqtt: Optional[MQTTClient] = None
        self._accumulator = PrinterStateAccumulator()
        self._connected = False
        self._devices: List[Dict[str, Any]] = []

    def _get_fresh_token(self, verification_code_timeout: int = 300) -> str:
        """
        Get a fresh token using credentials.

        For first-time login, 2FA verification code may be required.
        User will be prompted to enter the code sent to their email.

        Args:
            verification_code_timeout: Seconds to wait for verification code input (default 300)

        Raises:
            ValueError: If credentials not provided
            TimeoutError: If verification code not entered within timeout
        """
        if not self.username or not self.password:
            raise ValueError(
                "Username and password required for token refresh. Provide as arguments "
                "or set BAMBU_USERNAME and BAMBU_PASSWORD environment variables."
            )

        print("\n" + "=" * 60)
        print("BambuLab Authentication")
        print("=" * 60)
        print(f"Authenticating as: {self.username}")
        print("This may require email verification (2FA)...")
        print()

        auth = BambuAuthenticator()

        try:
            # First attempt - try to get existing/cached token
            if self._silent:
                with suppress_stdout():
                    token = auth.get_or_create_token(
                        username=self.username,
                        password=self.password
                    )
            else:
                token = auth.get_or_create_token(
                    username=self.username,
                    password=self.password
                )

            self._token = token
            print("Authentication successful!")
            print(f"Token: {token[:20]}...{token[-10:]}")
            print("=" * 60 + "\n")
            logger.info("BambuLab token obtained successfully")
            return token

        except Exception as e:
            error_msg = str(e).lower()

            # Check if it's a verification code request
            if "verification" in error_msg or "code" in error_msg or "2fa" in error_msg:
                print("\n" + "-" * 60)
                print("EMAIL VERIFICATION REQUIRED")
                print("-" * 60)
                print("A verification code has been sent to your email.")
                print(f"You have {verification_code_timeout} seconds to enter it.")
                print()

                try:
                    code = timed_input(
                        "Enter verification code: ",
                        timeout_sec=verification_code_timeout
                    )

                    if not code:
                        raise ValueError("No verification code entered")

                    # Try login with verification code
                    print("Verifying code...")
                    token = auth.login(
                        self.username,
                        self.password,
                        verification_code=code
                    )

                    self._token = token
                    print("\nAuthentication successful!")
                    print(f"Token: {token[:20]}...{token[-10:]}")
                    print("=" * 60 + "\n")
                    print("TIP: Save this token to BAMBU_TOKEN env var to skip login next time")
                    logger.info("BambuLab token obtained with 2FA verification")
                    return token

                except TimeoutError:
                    print("\nVerification timed out!")
                    print("Please try again or check your email for the code.")
                    raise TimeoutError(
                        f"Verification code not entered within {verification_code_timeout} seconds"
                    )
            else:
                # Re-raise other errors
                print(f"\nAuthentication failed: {e}")
                raise

    def _ensure_token(self) -> str:
        """Ensure we have a valid token, refreshing if needed"""
        if self._token:
            logger.debug("Using existing token")
            return self._token

        # No token available - need to authenticate
        print("\n" + "!" * 60)
        print("NO TOKEN FOUND")
        print("!" * 60)
        print("Checked:")
        print("  - Constructor 'token' parameter: Not provided")
        print("  - Environment variable 'BAMBU_TOKEN': Not set")
        print()
        print("Will attempt to authenticate with username/password...")
        print("!" * 60 + "\n")

        return self._get_fresh_token(verification_code_timeout=self._verification_timeout)

    def _validate_token(self) -> bool:
        """
        Validate current token by making an API call.
        Returns True if token is valid, False if expired/invalid.
        """
        if not self._token:
            return False

        try:
            client = BambuClient(token=self._token)
            client.get_user_info()
            return True
        except Exception as e:
            logger.debug(f"Token validation failed: {e}")
            return False

    def _on_mqtt_message(self, device_id: str, data: Dict[str, Any]) -> None:
        """Internal MQTT message handler"""
        if not data:  # Skip empty messages
            return

        state = self._accumulator.update(data)

        if self._on_update:
            self._on_update(state)

    def connect(self, blocking: bool = False, retry_on_auth_error: bool = True) -> None:
        """
        Connect to printer via MQTT.

        Args:
            blocking: If True, block until disconnected. If False, run in background.
            retry_on_auth_error: If True, refresh token and retry on auth failure.
        """
        token = self._ensure_token()

        try:
            self._client = BambuClient(token=token)

            # Get user info for UID
            user_info = self._client.get_user_info()
            self._uid = str(user_info.get("uid", ""))

            # Get devices if device_id not specified
            if not self._device_id:
                self._devices = self._client.get_devices()
                if not self._devices:
                    raise RuntimeError("No devices found on this account")
                self._device_id = self._devices[0].get("dev_id")

            # Connect MQTT
            self._mqtt = MQTTClient(
                self._uid,
                token,
                self._device_id,
                on_message=self._on_mqtt_message
            )
            self._mqtt.connect(blocking=blocking)
            self._connected = True
            logger.info(f"Connected to BambuLab printer: {self._device_id}")

        except Exception as e:
            error_msg = str(e).lower()
            is_auth_error = any(x in error_msg for x in ["401", "unauthorized", "token", "auth", "expired"])

            if is_auth_error and retry_on_auth_error and self.username and self.password:
                logger.warning("Auth error detected, refreshing token and retrying...")
                self._token = None  # Clear invalid token
                self._get_fresh_token()
                self.connect(blocking=blocking, retry_on_auth_error=False)  # Retry once
            else:
                raise

    def reconnect(self, blocking: bool = False) -> None:
        """
        Disconnect and reconnect (useful for recovering from errors).
        Will refresh token if authentication fails.
        """
        self.disconnect()
        self._accumulator.reset()
        self.connect(blocking=blocking)

    def disconnect(self) -> None:
        """Disconnect from MQTT"""
        if self._mqtt:
            try:
                self._mqtt.disconnect()
            except Exception:
                pass
        self._connected = False
        logger.debug("Disconnected from BambuLab printer")

    def get_state(self) -> PrinterState:
        """Get current accumulated printer state"""
        return self._accumulator.get_state()

    def get_snapshot(self) -> Dict[str, Any]:
        """Get simplified snapshot for database logging"""
        return self._accumulator.get_state().get_snapshot()

    @property
    def device_id(self) -> Optional[str]:
        """Current device ID"""
        return self._device_id

    @property
    def devices(self) -> List[Dict[str, Any]]:
        """List of devices on the account"""
        return self._devices

    @property
    def is_connected(self) -> bool:
        """Check if connected to MQTT"""
        return self._connected

    def __enter__(self):
        self.connect(blocking=False)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


# Convenience function for quick status check
def get_printer_status(
    username: Optional[str] = None,
    password: Optional[str] = None,
    token: Optional[str] = None,
    timeout_sec: int = 10,
) -> PrinterState:
    """
    Quick function to get current printer status.

    Connects, waits for initial status update, and returns state.

    Args:
        username: BambuLab account email
        password: BambuLab account password
        token: Pre-obtained token (optional)
        timeout_sec: Seconds to wait for status update

    Returns:
        Current PrinterState
    """
    import threading

    received_state: List[PrinterState] = []
    event = threading.Event()

    def on_update(state: PrinterState) -> None:
        if state.sequence_id:  # Got a real update
            received_state.append(state)
            event.set()

    printer = BambuPrinter(
        username=username,
        password=password,
        token=token,
        on_update=on_update,
        silent=True,
    )

    try:
        printer.connect(blocking=False)

        # Wait for first update
        if event.wait(timeout=timeout_sec) and received_state:
            return received_state[0]
        else:
            # Return whatever we have
            return printer.get_state()
    finally:
        printer.disconnect()


# Re-export everything for convenience
__all__ = [
    # From bambu-lab-cloud-api
    "BambuAuthenticator",
    "BambuClient",
    "MQTTClient",
    # Data models
    "FilamentTray",
    "AMSUnit",
    "AMSState",
    "PrinterState",
    "PrinterStateAccumulator",
    # High-level interface
    "BambuPrinter",
    "get_printer_status",
]
