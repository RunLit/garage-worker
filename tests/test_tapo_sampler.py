import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from garage_worker.tapo_sampler import TapoSampler


def make_sampler_with_mock_device():
    sampler = TapoSampler("user@example.com", "pw", "192.168.0.94")
    sampler._device = AsyncMock()
    sampler._connected = True
    return sampler


@pytest.mark.asyncio
async def test_get_one_sample_extracts_status_fields():
    sampler = make_sampler_with_mock_device()
    sampler._device.get_current_power.return_value = {"current_power": 42}
    sampler._device.get_device_usage.return_value = {}
    sampler._device.get_energy_usage.return_value = {}
    sampler._device.get_device_info.return_value = {
        "device_on": True,
        "overheated": False,
        "power_protection_status": "normal",
        "signal_level": 3,
        "on_time": 12345,
        "nickname": "H2C Printer Plug",
    }

    sample = await sampler.get_one_sample(pause_after_on=0)

    assert sample["power_w"] == 42.0
    assert sample["device_on"] is True
    assert sample["overheated"] is False
    assert sample["power_protection_status"] == "normal"
    assert sample["signal_level"] == 3
    assert sample["on_time"] == 12345
    assert sample["nickname"] == "H2C Printer Plug"


@pytest.mark.asyncio
async def test_turn_on_calls_device_on():
    sampler = make_sampler_with_mock_device()
    await sampler.turn_on()
    sampler._device.on.assert_awaited_once()


@pytest.mark.asyncio
async def test_turn_off_calls_device_off():
    sampler = make_sampler_with_mock_device()
    await sampler.turn_off()
    sampler._device.off.assert_awaited_once()


def test_turn_on_sync_requires_no_running_loop():
    sampler = TapoSampler("user@example.com", "pw", "192.168.0.94")
    sampler.connect = AsyncMock()
    sampler.close = AsyncMock()
    sampler._device = AsyncMock()
    sampler._connected = True

    # turn_on_sync should run without raising when no loop is active
    sampler.turn_on_sync()
    sampler._device.on.assert_awaited_once()
