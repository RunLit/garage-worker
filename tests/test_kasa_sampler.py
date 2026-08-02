from unittest.mock import AsyncMock, MagicMock

import pytest

from garage_worker.kasa_sampler import KasaStripSampler


def make_mock_child(alias, is_on, device_id):
    child = MagicMock()
    child.alias = alias
    child.is_on = is_on
    child.device_id = device_id
    child.turn_on = AsyncMock()
    child.turn_off = AsyncMock()
    return child


def make_sampler_with_mock_device():
    sampler = KasaStripSampler("192.168.0.88")
    device = MagicMock()
    device.update = AsyncMock()
    device.disconnect = AsyncMock()
    device.alias = "Garage Power Strip"
    device.rssi = -50
    device.model = "KP303(AU)"
    device.mac = "AA:BB:CC:DD:EE:FF"
    device.children = [
        make_mock_child("H2C Fan", True, "outlet-0"),
        make_mock_child("Bench Light", False, "outlet-1"),
        make_mock_child("Spare", False, "outlet-2"),
    ]
    sampler._device = device
    sampler._connected = True
    return sampler, device


@pytest.mark.asyncio
async def test_get_one_sample_lists_all_outlets():
    sampler, device = make_sampler_with_mock_device()

    sample = await sampler.get_one_sample()

    assert sample["device_ip"] == "192.168.0.88"
    assert sample["alias"] == "Garage Power Strip"
    assert sample["rssi"] == -50
    assert sample["sysinfo"]["model"] == "KP303(AU)"
    assert len(sample["outlets"]) == 3
    assert sample["outlets"][0] == {
        "outlet_index": 0, "alias": "H2C Fan", "state": True, "device_id": "outlet-0",
    }
    assert sample["outlets"][1]["state"] is False


@pytest.mark.asyncio
async def test_turn_on_calls_correct_child_outlet():
    sampler, device = make_sampler_with_mock_device()
    device.children[1].is_on = True
    result = await sampler.turn_on(1)
    device.children[1].turn_on.assert_awaited_once()
    device.children[0].turn_on.assert_not_called()
    device.update.assert_awaited()
    assert result is True


@pytest.mark.asyncio
async def test_turn_off_calls_correct_child_outlet():
    sampler, device = make_sampler_with_mock_device()
    device.children[2].is_on = False
    result = await sampler.turn_off(2)
    device.children[2].turn_off.assert_awaited_once()
    device.update.assert_awaited()
    assert result is False


@pytest.mark.asyncio
async def test_close_calls_disconnect():
    sampler, device = make_sampler_with_mock_device()
    await sampler.close()
    device.disconnect.assert_awaited_once()
    assert sampler._device is None
    assert sampler._connected is False
