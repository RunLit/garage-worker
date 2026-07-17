import os
import requests
import urllib3
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


urllib3.disable_warnings()  # just to silence the warning while testing

NAS_PORT = 5001
DEFAULT_TIMEZONE = os.getenv("NAS_TIMEZONE", "Australia/Melbourne")


class SynologySampler:
    BASE_URL = "https://{nas_ip}:{port}/webapi/entry.cgi"

    def __init__(self, ip: str, port: Optional[int] = None, username: Optional[str] = None, password: Optional[str] = None):
        self.ip = ip
        self.port = port if port is not None else NAS_PORT
        self.username = username or os.getenv("SYNOLOGY_USER_NAME")
        self.password = password or os.getenv("SYNOLOGY_USER_PASSWORD")
        if not all([self.username, self.password]):
            raise ValueError("Missing one of the credentials: username, password")
        self.base_url = self.BASE_URL.format(nas_ip=self.ip, port=self.port)
        self.session = requests.Session()
        self.sid = None
        self._login()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.logout()

    def _login(self) -> None:
        """Login -> get SID"""
        login = self.session.get(
            self.base_url,
            params={
                "api": "SYNO.API.Auth",
                "version": "6",
                "method": "login",
                "account": self.username,
                "passwd": self.password,
                "session": "Core",
                "format": "sid",
            },
            verify=False,
        ).json()
        if not login.get("success"):
            code = login.get("error", {}).get("code")
            if code == 400:
                raise PermissionError("❌ Login failed: Invalid username or password")
            else:
                raise PermissionError("❌ Login failed:", login.get("error", {}))
        self.sid = login["data"]["sid"]

    def synology_get(self, params) -> dict:
        """helper to always include _sid"""
        return self.session.get(self.base_url, params={**params, "_sid": self.sid}, verify=False).json()

    def get_utilization(self) -> dict:
        """Get system utilization"""
        return self.synology_get(
            {
                "api": "SYNO.Core.System.Utilization",
                "version": "1",
                "method": "get",
            }
        )

    def get_storage_info(self) -> dict:
        """Get storage information"""
        return self.synology_get(
            {
                "api": "SYNO.Storage.CGI.Storage",
                "version": "1",
                "method": "load_info",
            }
        )

    def logout(self) -> dict:
        """Logout from the session"""
        return self.synology_get(
            {
                "api": "SYNO.API.Auth",
                "version": "6",
                "method": "logout",
                "session": "Core",
            }
        )

    @staticmethod
    def now_iso(ts_int):
        return datetime.fromtimestamp(ts_int, ZoneInfo(DEFAULT_TIMEZONE)).isoformat()

    def _check_response(self, response: dict, context: str = "") -> dict:
        """Check API response and re-login if session expired, then retry."""
        if not response.get("success"):
            error_code = response.get("error", {}).get("code")
            # Error codes 105/106/107 = session expired/invalid SID; 119 = SID not found (also invalid session)
            if error_code in (105, 106, 107, 119):
                self._login()
                return None  # Signal caller to retry
            raise RuntimeError(f"Synology API error{' (' + context + ')' if context else ''}: {response.get('error', {})}")
        return response

    def _get_with_reauth(self, fetch_func, context: str = "") -> dict:
        """Call a fetch function, re-authenticate and retry once if session expired."""
        response = fetch_func()
        checked = self._check_response(response, context)
        if checked is None:
            response = fetch_func()
            checked = self._check_response(response, context)
            if checked is None:
                raise RuntimeError(f"Synology API still failing after re-login ({context})")
        return checked

    def get_one_sample(self) -> dict:
        """Sample and return relevant information"""
        util = self._get_with_reauth(self.get_utilization, "utilization")
        storage = self._get_with_reauth(self.get_storage_info, "storage")
        t = util["data"]["time"]

        payload = {
            "ts": self.now_iso(t),
            "cpu": {
                "user": util["data"]["cpu"]["user_load"],
                "system": util["data"]["cpu"]["system_load"],
                "other": util["data"]["cpu"]["other_load"],
                "load_1": util["data"]["cpu"]["1min_load"],
                "load_5": util["data"]["cpu"]["5min_load"],
                "load_15": util["data"]["cpu"]["15min_load"],
            },
            "memory": {
                "real_usage_pct": util["data"]["memory"]["real_usage"],
                "total_real": util["data"]["memory"]["total_real"],
                "avail_real": util["data"]["memory"]["avail_real"],
                "buffer": util["data"]["memory"]["buffer"],
                "cached": util["data"]["memory"]["cached"],
                "memory_size": util["data"]["memory"]["memory_size"],
                "swap_usage_pct": util["data"]["memory"]["swap_usage"],
                "total_swap": util["data"]["memory"]["total_swap"],
                "avail_swap": util["data"]["memory"]["avail_swap"],
                "si_disk": util["data"]["memory"]["si_disk"],
                "so_disk": util["data"]["memory"]["so_disk"],
            },
            # Network (counters direct)
            "network": util["data"]["network"],
            # Disk I/O snapshot (counters)
            "disks_util": util["data"]["disk"]["disk"],
            "space": util["data"]["space"]["volume"],
            # Pools (RAID groups)
            "pools": [
                {
                    "id": p["id"],
                    "raid": p.get("raidType"),
                    "status": p["status"],
                    "used": p["size"]["used"],
                    "total": p["size"]["total"],
                }
                for p in storage["data"]["storagePools"]
            ],
            # Volumes (filesystems created inside those Pools)
            "volumes": [
                {
                    "id": v["id"],
                    "fs": v.get("fs_type"),
                    "status": v["status"],
                    "used": v["size"]["used"],
                    "total": v["size"]["total"],
                }
                for v in storage["data"]["volumes"]
            ],
            # Hardware monitoring: disks
            "hardware": [
                {
                    "id": d["id"],
                    "model": d["model"],
                    "serial": d["serial"],
                    "temp_c": d["temp"],
                    "smart_status": d["smart_status"],
                    "status": d["status"],
                }
                for d in storage["data"]["disks"]
            ],
            # System / chassis info
            "env": {
                "model_name": storage["data"]["env"]["model_name"],
                "bay_number": storage["data"]["env"]["bay_number"],
                "system_crashed": storage["data"]["env"]["status"]["system_crashed"],
                "system_need_repair": storage["data"]["env"]["status"]["system_need_repair"],
                "system_rebuilding": storage["data"]["env"]["status"]["system_rebuilding"],
            },
        }
        return payload

    def get_system_snapshot(self) -> dict:
        """Get a simplified snapshot of system metrics for database logging

        Returns a flattened dictionary with key metrics for charting and monitoring:
        - Timestamp
        - CPU and RAM usage
        - Network traffic
        - Storage usage and health
        - Disk temperatures and health status
        - System status
        """
        raw_data = self.get_one_sample()

        # Calculate total network traffic
        total_network = next((net for net in raw_data['network'] if net['device'] == 'total'), None)

        # Calculate total storage usage across volumes
        total_storage_used = sum(int(v['used']) for v in raw_data['volumes'])
        total_storage_capacity = sum(int(v['total']) for v in raw_data['volumes'])
        storage_usage_pct = round((total_storage_used / total_storage_capacity * 100), 2) if total_storage_capacity > 0 else 0

        # Get disk health summary
        all_disks_healthy = all(d['status'] == 'normal' and d['smart_status'] == 'normal' for d in raw_data['hardware'])

        # Get max disk temperature
        max_disk_temp = max((d['temp_c'] for d in raw_data['hardware']), default=0)

        # Create simplified snapshot
        snapshot = {
            'timestamp': raw_data['ts'],

            # CPU metrics
            'cpu_load_1min': raw_data['cpu']['load_1'],
            'cpu_load_5min': raw_data['cpu']['load_5'],
            'cpu_load_15min': raw_data['cpu']['load_15'],
            'cpu_user_pct': raw_data['cpu']['user'],
            'cpu_system_pct': raw_data['cpu']['system'],

            # Memory metrics
            'memory_usage_pct': raw_data['memory']['real_usage_pct'],
            'memory_total_mb': round(raw_data['memory']['total_real'] / 1024, 2),
            'memory_available_mb': round(raw_data['memory']['avail_real'] / 1024, 2),

            # Memory composition (for stacked chart)
            'memory_reserved_mb': round((raw_data['memory']['memory_size'] - raw_data['memory']['total_real']) / 1024, 2),
            'memory_used_mb': round((raw_data['memory']['total_real'] - raw_data['memory']['avail_real'] -
                                    raw_data['memory']['buffer'] - raw_data['memory']['cached']) / 1024, 2),
            'memory_buffer_mb': round(raw_data['memory']['buffer'] / 1024, 2),
            'memory_cached_mb': round(raw_data['memory']['cached'] / 1024, 2),
            'memory_free_mb': round(raw_data['memory']['avail_real'] / 1024, 2),
            'memory_physical_size_mb': round(raw_data['memory']['memory_size'] / 1024, 2),

            # Swap metrics
            'swap_usage_pct': raw_data['memory']['swap_usage_pct'],
            'swap_total_mb': round(raw_data['memory']['total_swap'] / 1024, 2),
            'swap_available_mb': round(raw_data['memory']['avail_swap'] / 1024, 2),
            'swap_used_mb': round((raw_data['memory']['total_swap'] - raw_data['memory']['avail_swap']) / 1024, 2),

            # Network metrics (bytes/sec or cumulative counters)
            'network_rx_bytes': total_network['rx'] if total_network else 0,
            'network_tx_bytes': total_network['tx'] if total_network else 0,

            # Storage metrics
            'storage_usage_pct': storage_usage_pct,
            'storage_used_gb': round(total_storage_used / (1024**3), 2),
            'storage_total_gb': round(total_storage_capacity / (1024**3), 2),

            # Temperature metrics
            'max_disk_temp_c': max_disk_temp,
            'disk_temperatures': [
                {'disk': d['id'], 'temp_c': d['temp_c']}
                for d in raw_data['hardware']
            ],

            # Health metrics
            'all_disks_healthy': all_disks_healthy,
            'disk_health': [
                {
                    'disk': d['id'],
                    'model': d['model'],
                    'status': d['status'],
                    'smart_status': d['smart_status']
                }
                for d in raw_data['hardware']
            ],

            # RAID/Pool status
            'pools_status': [
                {
                    'id': p['id'],
                    'status': p['status'],
                    'usage_pct': round((int(p['used']) / int(p['total']) * 100), 2) if int(p['total']) > 0 else 0
                }
                for p in raw_data['pools']
            ],

            # System status
            'system_healthy': not any([
                raw_data['env']['system_crashed'],
                raw_data['env']['system_need_repair'],
                raw_data['env']['system_rebuilding']
            ]),
            'system_status': {
                'crashed': raw_data['env']['system_crashed'],
                'needs_repair': raw_data['env']['system_need_repair'],
                'rebuilding': raw_data['env']['system_rebuilding']
            },

            # Model info
            'model': raw_data['env']['model_name'],
        }

        return snapshot
