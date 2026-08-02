"""
Minimal Kasa KP303 power-strip sampler: collects per-outlet on/off state
and exposes on/off control. KP303 has no power/energy sensing hardware —
only relay state is available.
Returns a clean dict suitable for direct saving into a Django model JSONField.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
from typing import Any, Dict, List, Optional

from kasa import Discover

DEFAULT_TZ = "Australia/Melbourne"


def _now_iso(tz_name: str = DEFAULT_TZ) -> str:
    return datetime.now(tz=ZoneInfo(tz_name)).isoformat()


class KasaStripSampler:
    """
    Simple synchronous-friendly wrapper around a Kasa KP303 power strip.
    Usage (sync):
        s = KasaStripSampler("192.168.0.88")
        sample = s.get_one_sample_sync()
    Or async:
        await s.connect()
        sample = await s.get_one_sample()
        await s.close()
    """

    def __init__(self, ip: str, tz_name: str = DEFAULT_TZ):
        self.ip = ip
        self.tz_name = tz_name
        self._device = None
        self._connected = False

    async def connect(self, timeout: int = 10):
        if self._connected:
            return
        self._device = await asyncio.wait_for(Discover.discover_single(self.ip), timeout=timeout)
        await self._device.update()
        self._connected = True

    async def close(self):
        if self._device:
            try:
                await self._device.disconnect()
            except Exception:
                pass
            finally:
                self._device = None
                self._connected = False
        else:
            self._device = None
            self._connected = False

    async def get_one_sample(self) -> Dict[str, Any]:
        """
        Collects per-outlet alias/state and strip-level rssi/sysinfo.
        No power/energy data — KP303 has no monitoring hardware.
        """
        if not self._connected:
            await self.connect()
        await self._device.update()

        outlets: List[Dict[str, Any]] = []
        for idx, child in enumerate(self._device.children):
            outlets.append({
                "outlet_index": idx,
                "alias": child.alias,
                "state": bool(child.is_on),
                "device_id": child.device_id,
            })

        return {
            "timestamp": _now_iso(self.tz_name),
            "device_ip": self.ip,
            "rssi": getattr(self._device, "rssi", None),
            "sysinfo": {
                "model": getattr(self._device, "model", None),
                "mac": getattr(self._device, "mac", None),
            },
            "outlets": outlets,
        }

    def get_one_sample_sync(self) -> Dict[str, Any]:
        """Synchronous convenience wrapper. Uses asyncio.run()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("An event loop is already running. Call get_one_sample() (async) instead.")

        async def _inner():
            await self.connect()
            try:
                return await self.get_one_sample()
            finally:
                await self.close()

        return asyncio.run(_inner())

    async def turn_on(self, outlet_index: int):
        """Turn on the outlet at the given 0-based index."""
        if not self._connected:
            await self.connect()
        await self._device.children[outlet_index].turn_on()

    async def turn_off(self, outlet_index: int):
        """Turn off the outlet at the given 0-based index."""
        if not self._connected:
            await self.connect()
        await self._device.children[outlet_index].turn_off()

    def turn_on_sync(self, outlet_index: int):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("An event loop is already running. Call turn_on() (async) instead.")

        async def _inner():
            await self.connect()
            try:
                await self.turn_on(outlet_index)
            finally:
                await self.close()

        return asyncio.run(_inner())

    def turn_off_sync(self, outlet_index: int):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("An event loop is already running. Call turn_off() (async) instead.")

        async def _inner():
            await self.connect()
            try:
                await self.turn_off(outlet_index)
            finally:
                await self.close()

        return asyncio.run(_inner())
