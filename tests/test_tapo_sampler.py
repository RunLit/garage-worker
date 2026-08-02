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
        "overheat_status": "normal",
        "power_protection_status": "normal",
        "signal_level": 3,
        "on_time": 12345,
        "nickname": "H2C Printer Plug",
    }

    sample = await sampler.get_one_sample(pause_after_on=0)

    assert sample["power_w"] == 42.0
    assert sample["device_on"] is True
    assert sample["overheated"] is False
    assert sample["overheat_status"] == "normal"
    assert sample["power_protection_status"] == "normal"
    assert sample["signal_level"] == 3
    assert sample["on_time"] == 12345
    assert sample["nickname"] == "H2C Printer Plug"


@pytest.mark.asyncio
async def test_get_one_sample_power_w_zero_when_plug_off():
    """current_power == 0 is a legitimate value and must not fall through to None."""
    sampler = make_sampler_with_mock_device()
    sampler._device.get_current_power.return_value = {"current_power": 0}
    sampler._device.get_device_usage.return_value = {}
    sampler._device.get_energy_usage.return_value = {}
    sampler._device.get_device_info.return_value = {
        "device_on": False,
        "overheat_status": "normal",
        "power_protection_status": "normal",
        "signal_level": 3,
        "on_time": 0,
        "nickname": "H2C Printer Plug",
    }

    sample = await sampler.get_one_sample(pause_after_on=0)

    assert sample["power_w"] == 0.0


@pytest.mark.asyncio
async def test_get_one_sample_overheated_true_when_overheat_status_overheated():
    # Mock matches the REAL tapo API shape (overheat_status enum-like value),
    # not the old (wrong) "overheated" boolean key, so this can't mask the same bug.
    sampler = make_sampler_with_mock_device()
    sampler._device.get_current_power.return_value = {"current_power": 42}
    sampler._device.get_device_usage.return_value = {}
    sampler._device.get_energy_usage.return_value = {}
    sampler._device.get_device_info.return_value = {
        "device_on": True,
        "overheat_status": "overheated",
        "power_protection_status": "normal",
        "signal_level": 3,
        "on_time": 12345,
        "nickname": "H2C Printer Plug",
    }

    sample = await sampler.get_one_sample(pause_after_on=0)

    assert sample["overheated"] is True
    assert sample["overheat_status"] == "overheated"


@pytest.mark.asyncio
async def test_get_one_sample_overheated_false_when_overheat_status_normal():
    sampler = make_sampler_with_mock_device()
    sampler._device.get_current_power.return_value = {"current_power": 42}
    sampler._device.get_device_usage.return_value = {}
    sampler._device.get_energy_usage.return_value = {}
    sampler._device.get_device_info.return_value = {
        "device_on": True,
        "overheat_status": "normal",
        "power_protection_status": "normal",
        "signal_level": 3,
        "on_time": 12345,
        "nickname": "H2C Printer Plug",
    }

    sample = await sampler.get_one_sample(pause_after_on=0)

    assert sample["overheated"] is False
    assert sample["overheat_status"] == "normal"


@pytest.mark.asyncio
async def test_turn_on_calls_device_on():
    sampler = make_sampler_with_mock_device()
    sampler._device.get_device_info.return_value = {"device_on": True}
    result = await sampler.turn_on()
    sampler._device.on.assert_awaited_once()
    assert result is True


@pytest.mark.asyncio
async def test_turn_off_calls_device_off():
    sampler = make_sampler_with_mock_device()
    sampler._device.get_device_info.return_value = {"device_on": False}
    result = await sampler.turn_off()
    sampler._device.off.assert_awaited_once()
    assert result is False


def test_turn_on_sync_requires_no_running_loop():
    sampler = TapoSampler("user@example.com", "pw", "192.168.0.94")
    sampler.connect = AsyncMock()
    sampler.close = AsyncMock()
    sampler._device = AsyncMock()
    sampler._device.get_device_info.return_value = {"device_on": True}
    sampler._connected = True

    # turn_on_sync should run without raising when no loop is active
    result = sampler.turn_on_sync()
    sampler._device.on.assert_awaited_once()
    assert result is True
