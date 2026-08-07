"""
Kasa power-strip sampler speaking TP-Link's legacy Smart Home Protocol directly.

Why this exists alongside kasa_sampler.py
-----------------------------------------
python-kasa is the right library for the current Kasa/Tapo generation, which
authenticates and encrypts (KLAP). All of that machinery — aiohttp, cryptography,
mashumaro — plus a Python >= 3.11 floor comes with it.

The KP303 predates every bit of that. It speaks the original protocol: a 4-byte
big-endian length header followed by JSON under an XOR autokey cipher, over TCP
9999. No auth, no TLS, no dependencies. Talking to it directly means the driver
runs anywhere Python does, which matters because the collector host is a
Raspberry Pi on Debian bullseye (Python 3.9) where python-kasa cannot install.

This is not a workaround for a missing library — it is the device's native
protocol, and a device on final 2021 firmware will not start speaking a
different one. Verified against a KP303(AU), sw 1.0.4 Build 210428, on both
Python 3.9 and 3.14.

For any newer device that needs KLAP, use KasaStripSampler (python-kasa) and
accept its Python floor on that host.

Interface is deliberately identical to KasaStripSampler so the two are
drop-in interchangeable from the driver registry's point of view.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import json
import socket
import struct

try:                                    # zoneinfo is stdlib from 3.9
    from zoneinfo import ZoneInfo
except ImportError:                     # pragma: no cover - 3.8 and older
    ZoneInfo = None                     # type: ignore[assignment]

DEFAULT_TZ = "Australia/Melbourne"

_PORT = 9999
# The cipher is an XOR autokey stream seeded with 171: each ciphertext byte
# becomes the key for the next plaintext byte. It is obfuscation, not security —
# which is exactly why the protocol needs no credentials.
_INITIAL_KEY = 171
_DEFAULT_TIMEOUT = 10.0


def _now_iso(tz_name: str = DEFAULT_TZ) -> str:
    if ZoneInfo is None:                # pragma: no cover
        return datetime.now().isoformat()
    return datetime.now(tz=ZoneInfo(tz_name)).isoformat()


def _encrypt(payload: str) -> bytes:
    key = _INITIAL_KEY
    out = bytearray()
    for char in payload.encode():
        key = key ^ char
        out.append(key)
    return struct.pack(">I", len(out)) + bytes(out)


def _decrypt(data: bytes) -> str:
    key = _INITIAL_KEY
    out = bytearray()
    for char in data:
        out.append(key ^ char)
        key = char
    return out.decode()


def _recv_exactly(sock: socket.socket, count: int) -> bytes:
    """Read exactly `count` bytes or raise.

    recv() may return short reads on a busy network, and the KP303's sysinfo
    response comfortably exceeds one segment — a single recv() would silently
    truncate the JSON and fail to parse.
    """
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise ConnectionError(
                "connection closed after %d of %d bytes" % (len(buf), count)
            )
        buf += chunk
    return buf


class KasaLegacySampler:
    """
    Synchronous sampler for a legacy-protocol Kasa power strip (KP303 and kin).

    Usage:
        s = KasaLegacySampler("192.168.0.88")
        sample = s.get_one_sample_sync()
        s.turn_on_sync(0)

    Each call opens and closes its own short-lived TCP connection. The device
    accepts one request per connection, and at a 30s polling cadence there is
    nothing to gain from holding a socket open between samples — while a
    persistent socket would need reconnect handling for every Wi-Fi blip.
    """

    def __init__(self, ip: str, tz_name: str = DEFAULT_TZ,
                 timeout: float = _DEFAULT_TIMEOUT):
        self.ip = ip
        self.tz_name = tz_name
        self.timeout = timeout

    # ── protocol ────────────────────────────────────────────────────────────

    def _query(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sock = socket.create_connection((self.ip, _PORT), self.timeout)
        try:
            sock.settimeout(self.timeout)
            sock.sendall(_encrypt(json.dumps(payload)))
            length = struct.unpack(">I", _recv_exactly(sock, 4))[0]
            body = _recv_exactly(sock, length)
        finally:
            sock.close()
        return json.loads(_decrypt(body))

    def _sysinfo(self) -> Dict[str, Any]:
        response = self._query({"system": {"get_sysinfo": {}}})
        try:
            return response["system"]["get_sysinfo"]
        except (KeyError, TypeError):
            raise ValueError("unexpected get_sysinfo response: %r" % (response,))

    @staticmethod
    def _child_id(sysinfo: Dict[str, Any], outlet_index: int) -> str:
        """Resolve the child id the relay command needs.

        Preferred straight from the device, because the id is what set_relay_state
        addresses and reading it avoids assuming the format. The fallback
        (deviceId + zero-padded index) matches what this firmware reports and
        covers a response that omits the children array.
        """
        children = sysinfo.get("children") or []
        if outlet_index < 0 or outlet_index >= len(children):
            raise IndexError(
                "outlet %d out of range; strip has %d outlets"
                % (outlet_index, len(children))
            )
        child = children[outlet_index]
        return child.get("id") or "%s%02d" % (sysinfo.get("deviceId", ""), outlet_index)

    # ── sampling ────────────────────────────────────────────────────────────

    def get_one_sample_sync(self) -> Dict[str, Any]:
        """Collect per-outlet alias/state plus strip-level rssi/sysinfo.

        No power or energy fields: the KP303 has no metering hardware, so the
        relay state is genuinely all there is to read.
        """
        sysinfo = self._sysinfo()

        outlets: List[Dict[str, Any]] = []
        for idx, child in enumerate(sysinfo.get("children") or []):
            outlets.append({
                "outlet_index": idx,
                "alias": child.get("alias"),
                "state": bool(child.get("state")),
                "device_id": child.get("id"),
            })

        return {
            "timestamp": _now_iso(self.tz_name),
            "device_ip": self.ip,
            "alias": sysinfo.get("alias"),
            "rssi": sysinfo.get("rssi"),
            "sysinfo": {
                "model": sysinfo.get("model"),
                "mac": sysinfo.get("mac"),
                "sw_ver": sysinfo.get("sw_ver"),
                "hw_ver": sysinfo.get("hw_ver"),
            },
            "outlets": outlets,
        }

    # ── control ─────────────────────────────────────────────────────────────

    def _set_relay(self, outlet_index: int, on: bool) -> bool:
        sysinfo = self._sysinfo()
        child_id = self._child_id(sysinfo, outlet_index)
        response = self._query({
            "context": {"child_ids": [child_id]},
            "system": {"set_relay_state": {"state": 1 if on else 0}},
        })

        # The device reports failure in-band with err_code rather than by
        # refusing the connection, so a non-zero code must be raised here or a
        # rejected command would look like a success to the caller.
        err = (response.get("system", {})
                       .get("set_relay_state", {})
                       .get("err_code"))
        if err not in (0, None):
            raise RuntimeError(
                "set_relay_state failed for outlet %d: %r" % (outlet_index, response)
            )

        # Read back rather than trusting the ack, so the return value reflects
        # the relay's actual position.
        confirmed = self._sysinfo()
        children = confirmed.get("children") or []
        if outlet_index < len(children):
            return bool(children[outlet_index].get("state"))
        return on

    def turn_on_sync(self, outlet_index: int) -> bool:
        """Turn on the outlet at the given 0-based index. Returns resulting state."""
        return self._set_relay(outlet_index, True)

    def turn_off_sync(self, outlet_index: int) -> bool:
        """Turn off the outlet at the given 0-based index. Returns resulting state."""
        return self._set_relay(outlet_index, False)
