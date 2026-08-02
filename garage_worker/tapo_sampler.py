"""
Minimal Tapo P110 sampler: collects current power, device usage, energy usage and device info.
Returns a clean dict suitable for direct saving into a Django model JSONField.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import asyncio
from typing import Any, Dict, Optional

from tapo import ApiClient

DEFAULT_TZ = "Australia/Melbourne"


def _obj_to_dict(obj: Any) -> Dict:
    """
    Convert tapo return objects to simple dicts.
    If object has to_dict(), call it; if it's already a dict return it; otherwise stringify.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
        try:
            return obj.to_dict() or {}
        except Exception:
            return {"raw": str(obj)}
    # fallback
    return {"raw": str(obj)}


def _now_iso(tz_name: str = DEFAULT_TZ) -> str:
    return datetime.now(tz=ZoneInfo(tz_name)).isoformat()


class TapoSampler:
    """
    Simple synchronous-friendly wrapper.
    Usage (sync):
        s = TapoSampler("you@example.com", "pw", "192.168.1.123")
        sample = s.get_one_sample_sync()
    Or async:
        await s.connect()
        sample = await s.get_one_sample()
        await s.close()
    """

    def __init__(self, username: str, password: str, ip: str, tz_name: str = DEFAULT_TZ):
        self.username = username
        self.password = password
        self.ip = ip
        self.tz_name = tz_name
        self._client: Optional[ApiClient] = None
        self._device = None
        self._connected = False

    # --- async lifecycle ---
    async def connect(self, timeout: int = 10):
        if self._connected:
            return
        self._client = ApiClient(self.username, self.password)
        # resolve device object
        self._device = await asyncio.wait_for(self._client.p110(self.ip), timeout=timeout)
        self._connected = True

    async def close(self):
        # tapo.ApiClient has no explicit teardown/close method; nothing to await.
        self._client = None
        self._device = None
        self._connected = False

    # --- main async collector ---
    async def get_one_sample(self, pause_after_on: float = 0.5) -> Dict:
        """
        Collects:
         - get_current_power()
         - get_device_usage()
         - get_energy_usage()
         - get_device_info()
        Returns a clean dict:
        {
            "timestamp": ISO str (tz aware),
            "device_ip": "...",
            "power_w": float|None,
            "device_info": {...},
            "device_usage": {...},
            "energy_usage": {...},
            "errors": {"current_power": "...", ...}  # present if any call raises
        }
        """
        if not self._connected:
            await self.connect()

        out: Dict[str, Any] = {
            "timestamp": _now_iso(self.tz_name),
            "device_ip": self.ip,
        }
        errors: Dict[str, str] = {}

        # small pause (in case caller just toggled device)
        if pause_after_on:
            await asyncio.sleep(pause_after_on)

        # 1. current power
        try:
            cur = await self._device.get_current_power()
            curd = _obj_to_dict(cur)
            # common keys: current_power / power / value
            power = next(
                (curd[k] for k in ("current_power", "power", "value") if curd.get(k) is not None),
                None,
            )
            out["power_w"] = float(power) if power is not None else None
            out["current_power_raw"] = curd
        except Exception as exc:
            errors["current_power"] = str(exc)
            out["power_w"] = None

        # 2. device usage (totals: today / 7 / 30)
        try:
            usage = await self._device.get_device_usage()
            out["device_usage"] = _obj_to_dict(usage)
        except Exception as exc:
            errors["device_usage"] = str(exc)
            out["device_usage"] = {}

        # 3. energy usage (energy object / aggregates)
        try:
            energy = await self._device.get_energy_usage()
            out["energy_usage"] = _obj_to_dict(energy)
        except Exception as exc:
            errors["energy_usage"] = str(exc)
            out["energy_usage"] = {}

        # 4. device info (baseline metadata)
        try:
            info = await self._device.get_device_info()
            out["device_info"] = _obj_to_dict(info)
        except Exception as exc:
            errors["device_info"] = str(exc)
            out["device_info"] = {}

        # Extract status fields from device_info
        device_info_dict = out.get("device_info", {}) or {}
        out["device_on"] = device_info_dict.get("device_on")
        out.update(self._extract_overheat_fields(device_info_dict))
        out["power_protection_status"] = device_info_dict.get("power_protection_status", "")
        out["signal_level"] = device_info_dict.get("signal_level")
        out["on_time"] = device_info_dict.get("on_time")
        out["nickname"] = device_info_dict.get("nickname", "")

        if errors:
            out["errors"] = errors

        return out

    @staticmethod
    def _extract_overheat_fields(device_info_dict: Dict) -> Dict[str, Any]:
        """
        Extract thermal state from device_info. The real P110 API has no
        `overheated` boolean key -- it has `overheat_status`, an enum-like
        value with states "normal" / "cool_down" / "overheated"
        (tapo's DeviceInfoPlugEnergyMonitoringResult).
        """
        overheat = device_info_dict.get("overheat_status")
        overheat = getattr(overheat, "value", overheat)  # unwrap enum to str if needed
        return {
            "overheat_status": overheat or "",
            "overheated": overheat == "overheated",
        }

    # --- control methods ---
    async def turn_on(self) -> Optional[bool]:
        """Turn the plug's relay on. Returns the resulting device_on state."""
        if not self._connected:
            await self.connect()
        await self._device.on()
        return await self._get_device_on_state()

    async def turn_off(self) -> Optional[bool]:
        """Turn the plug's relay off. Returns the resulting device_on state."""
        if not self._connected:
            await self.connect()
        await self._device.off()
        return await self._get_device_on_state()

    async def _get_device_on_state(self) -> Optional[bool]:
        """Fetch fresh device info and return its device_on boolean."""
        info = await self._device.get_device_info()
        info_dict = _obj_to_dict(info)
        return info_dict.get("device_on")

    # --- sync wrapper (convenience) ---
    def get_one_sample_sync(self, pause_after_on: float = 0.5) -> Dict:
        """
        Synchronous convenience wrapper. Uses asyncio.run().
        WARNING: will raise RuntimeError if called inside an already-running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            raise RuntimeError("An event loop is already running. Call get_one_sample() (async) instead.")

        async def _inner():
            await self.connect()
            try:
                return await self.get_one_sample(pause_after_on=pause_after_on)
            finally:
                await self.close()

        return asyncio.run(_inner())

    def turn_on_sync(self):
        """Synchronous convenience wrapper for turn_on()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("An event loop is already running. Call turn_on() (async) instead.")

        async def _inner():
            await self.connect()
            try:
                return await self.turn_on()
            finally:
                await self.close()

        return asyncio.run(_inner())

    def turn_off_sync(self):
        """Synchronous convenience wrapper for turn_off()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("An event loop is already running. Call turn_off() (async) instead.")

        async def _inner():
            await self.connect()
            try:
                return await self.turn_off()
            finally:
                await self.close()

        return asyncio.run(_inner())
