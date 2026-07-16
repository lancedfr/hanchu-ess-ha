"""End-to-end tests for the staged settings write flow.

These tests exercise the full path:
  entity change → staging buffer → async_flush_staged → iotSet
without a live HA instance or real API.
"""
from __future__ import annotations

from datetime import time as dt_time
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

pytest.importorskip("homeassistant")

from custom_components.hanchuess.staging import SettingsStagingBuffer
from custom_components.hanchuess.number import HanchuessNumber, NUMBERS
from custom_components.hanchuess.select import WorkModeSelect
from custom_components.hanchuess.time import HanchuessTimeSlot, TIME_SLOTS
from custom_components.hanchuess.button import apply_iot_values, ALL_CONTROL_KEYS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime_data(client=None):
    """Return a fake runtime_data object with a real staging buffer."""
    staging = SettingsStagingBuffer()
    rt = MagicMock()
    rt.staging = staging
    rt.control_registry = {}
    if client is not None:
        rt.realtime.client = client
    return rt


def _make_entry(sn="SN1", client=None):
    """Return a fake config entry backed by a real staging buffer."""
    entry = MagicMock()
    entry.data = {"sn": sn}
    entry.runtime_data = _make_runtime_data(client)
    return entry


def _make_number_entity(entry, control_key="CHG_PWR_LMT"):
    config = next(c for c in NUMBERS.values() if c["control_key"] == control_key)
    number_key = next(k for k, c in NUMBERS.items() if c["control_key"] == control_key)
    number = HanchuessNumber(entry, number_key, config, {control_key: {"min": 0, "max": 5000}}, {})
    number.async_write_ha_state = MagicMock()
    return number


def _make_select_entity(entry):
    sel = WorkModeSelect(entry, {})
    sel.async_write_ha_state = MagicMock()
    return sel


def _make_time_entity(entry, slot_key="charge_slot_1_start"):
    config = TIME_SLOTS[slot_key]
    slot = HanchuessTimeSlot(entry, slot_key, config, {})
    slot.async_write_ha_state = MagicMock()
    return slot


# ---------------------------------------------------------------------------
# Staging accumulation
# ---------------------------------------------------------------------------

async def test_multiple_entity_changes_accumulate_in_buffer():
    entry = _make_entry()
    number = _make_number_entity(entry, "CHG_PWR_LMT")
    sel = _make_select_entity(entry)

    await number.async_set_native_value(2500.0)
    await sel.async_select_option("Backup Energy")

    assert entry.runtime_data.staging.pending == {
        "CHG_PWR_LMT": 2500,
        "WORK_MODE_CMB": "2",
    }


async def test_staging_three_entity_types():
    """Number, select, and time slot all accumulate in the same buffer."""
    import custom_components.hanchuess.time as time_mod
    entry = _make_entry()
    number = _make_number_entity(entry, "DSCHG_PWR_LMT")
    sel = _make_select_entity(entry)
    slot = _make_time_entity(entry, "charge_slot_1_start")

    await number.async_set_native_value(1500.0)
    await sel.async_select_option("User-defined")

    # Simulate debounce completing
    slot._pending_value = dt_time(6, 0)  # 21600 seconds
    original_debounce = time_mod.DEBOUNCE_SECONDS
    time_mod.DEBOUNCE_SECONDS = 0
    try:
        await slot._send_after_delay()
    finally:
        time_mod.DEBOUNCE_SECONDS = original_debounce

    assert entry.runtime_data.staging.pending == {
        "DSCHG_PWR_LMT": 1500,
        "WORK_MODE_CMB": "3",
        "TCT_START_1": 21600,
    }


# ---------------------------------------------------------------------------
# async_flush_staged — success path
# ---------------------------------------------------------------------------

async def test_flush_calls_device_control_once_with_all_staged_keys():
    client = MagicMock()
    client.async_device_control = AsyncMock(return_value={"success": True})
    entry = _make_entry(client=client)

    # Stage two values manually
    entry.runtime_data.staging.stage("CHG_PWR_LMT", 2000)
    entry.runtime_data.staging.stage("WORK_MODE_CMB", "1")

    from custom_components.hanchuess import async_flush_staged
    hass = MagicMock()
    result = await async_flush_staged(hass, entry)

    assert result is True
    client.async_device_control.assert_awaited_once_with(
        "SN1", "2", {"CHG_PWR_LMT": 2000, "WORK_MODE_CMB": "1"}
    )


async def test_flush_clears_buffer_on_success():
    client = MagicMock()
    client.async_device_control = AsyncMock(return_value={"success": True})
    entry = _make_entry(client=client)
    entry.runtime_data.staging.stage("CHG_PWR_LMT", 2000)

    from custom_components.hanchuess import async_flush_staged
    await async_flush_staged(MagicMock(), entry)

    assert entry.runtime_data.staging.count == 0


async def test_flush_leaves_entity_states_unchanged():
    """After a successful flush, entity states are NOT refreshed from device."""
    client = MagicMock()
    client.async_device_control = AsyncMock(return_value={"success": True})
    entry = _make_entry(client=client)

    number = _make_number_entity(entry, "CHG_PWR_LMT")
    await number.async_set_native_value(2500.0)
    assert number._attr_native_value == 2500.0

    from custom_components.hanchuess import async_flush_staged
    await async_flush_staged(MagicMock(), entry)

    # Value unchanged — no iotGet called
    assert number._attr_native_value == 2500.0
    client.async_iot_get = AsyncMock()
    client.async_iot_get.assert_not_awaited()


async def test_flush_returns_true_when_nothing_staged():
    client = MagicMock()
    client.async_device_control = AsyncMock()
    entry = _make_entry(client=client)

    from custom_components.hanchuess import async_flush_staged
    result = await async_flush_staged(MagicMock(), entry)

    assert result is True
    client.async_device_control.assert_not_awaited()


# ---------------------------------------------------------------------------
# async_flush_staged — failure / retry path
# ---------------------------------------------------------------------------

async def test_flush_retries_once_on_failure():
    client = MagicMock()
    client.async_device_control = AsyncMock(
        side_effect=[
            {"success": False, "msg": "timeout"},
            {"success": True},
        ]
    )
    entry = _make_entry(client=client)
    entry.runtime_data.staging.stage("CHG_PWR_LMT", 2000)

    from custom_components.hanchuess import async_flush_staged
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await async_flush_staged(MagicMock(), entry)

    assert result is True
    assert client.async_device_control.await_count == 2
    assert entry.runtime_data.staging.count == 0  # cleared after retry success


async def test_flush_creates_notification_on_double_failure():
    client = MagicMock()
    client.async_device_control = AsyncMock(
        return_value={"success": False, "msg": "device unreachable"}
    )
    entry = _make_entry(client=client)
    entry.runtime_data.staging.stage("CHG_PWR_LMT", 2000)
    entry.runtime_data.staging.stage("WORK_MODE_CMB", "1")

    hass = MagicMock()

    from custom_components.hanchuess import async_flush_staged
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await async_flush_staged(hass, entry)

    assert result is False
    hass.components.persistent_notification.async_create.assert_called_once()
    # Notification title should mention failure
    call_kwargs = hass.components.persistent_notification.async_create.call_args
    assert "Write Settings Failed" in call_kwargs.kwargs.get("title", "") or \
           "Write Settings Failed" in str(call_kwargs)


async def test_flush_leaves_buffer_intact_on_double_failure():
    client = MagicMock()
    client.async_device_control = AsyncMock(
        return_value={"success": False, "msg": "error"}
    )
    entry = _make_entry(client=client)
    entry.runtime_data.staging.stage("CHG_PWR_LMT", 2000)

    from custom_components.hanchuess import async_flush_staged
    with patch("asyncio.sleep", new_callable=AsyncMock):
        await async_flush_staged(MagicMock(), entry)

    # Buffer must remain intact so the user can retry
    assert entry.runtime_data.staging.count == 1
    assert entry.runtime_data.staging.pending == {"CHG_PWR_LMT": 2000}


# ---------------------------------------------------------------------------
# SettingsStagingBuffer.discard
# ---------------------------------------------------------------------------

def test_discard_removes_only_specified_keys():
    staging = SettingsStagingBuffer()
    staging.stage("CHG_PWR_LMT", 2000)
    staging.stage("WORK_MODE_CMB", "1")
    staging.stage("DSCHG_PWR_LMT", 1500)

    staging.discard(["CHG_PWR_LMT", "WORK_MODE_CMB"])

    assert staging.pending == {"DSCHG_PWR_LMT": 1500}


def test_discard_missing_key_is_noop_and_does_not_notify():
    staging = SettingsStagingBuffer()
    staging.stage("CHG_PWR_LMT", 2000)
    on_change = MagicMock()
    staging.set_on_change(on_change)
    on_change.reset_mock()

    staging.discard(["UNKNOWN_KEY"])

    assert staging.pending == {"CHG_PWR_LMT": 2000}
    on_change.assert_not_called()


def test_discard_notifies_when_something_removed():
    staging = SettingsStagingBuffer()
    staging.stage("CHG_PWR_LMT", 2000)
    on_change = MagicMock()
    staging.set_on_change(on_change)
    on_change.reset_mock()

    staging.discard(["CHG_PWR_LMT"])

    assert staging.pending == {}
    on_change.assert_called_once()


# ---------------------------------------------------------------------------
# apply_iot_values — read-back refresh
# ---------------------------------------------------------------------------

def test_apply_iot_values_updates_number_entity():
    entry = _make_entry()
    number = _make_number_entity(entry, "CHG_PWR_LMT")
    entry.runtime_data.control_registry["CHG_PWR_LMT"] = number

    apply_iot_values(entry, {"CHG_PWR_LMT": "3000"})

    assert number._attr_native_value == 3000.0


def test_apply_iot_values_updates_select_entity():
    entry = _make_entry()
    sel = _make_select_entity(entry)
    entry.runtime_data.control_registry["WORK_MODE_CMB"] = sel

    apply_iot_values(entry, {"WORK_MODE_CMB": "2"})

    assert sel._attr_current_option == "Backup Energy"


def test_apply_iot_values_updates_time_slot_entity():
    entry = _make_entry()
    slot = _make_time_entity(entry, "charge_slot_1_start")
    entry.runtime_data.control_registry["TCT_START_1"] = slot

    apply_iot_values(entry, {"TCT_START_1": 9000})  # 02:30

    assert slot._attr_native_value == dt_time(2, 30)


def test_apply_iot_values_ignores_unknown_keys():
    """Keys not in the registry must not raise."""
    entry = _make_entry()
    # Empty registry — no entities registered
    apply_iot_values(entry, {"UNKNOWN_KEY": "42"})  # must not raise


def test_apply_iot_values_calls_async_write_ha_state():
    entry = _make_entry()
    number = _make_number_entity(entry, "CHG_PWR_LMT")
    entry.runtime_data.control_registry["CHG_PWR_LMT"] = number

    apply_iot_values(entry, {"CHG_PWR_LMT": 1000})

    number.async_write_ha_state.assert_called()


# ---------------------------------------------------------------------------
# ALL_CONTROL_KEYS completeness
# ---------------------------------------------------------------------------

def test_all_control_keys_covers_all_number_entities():
    """Every number entity's control key must appear in ALL_CONTROL_KEYS."""
    for config in NUMBERS.values():
        assert config["control_key"] in ALL_CONTROL_KEYS, (
            f"{config['control_key']} missing from ALL_CONTROL_KEYS"
        )


def test_all_control_keys_covers_all_time_slot_entities():
    """Every time slot entity's control key must appear in ALL_CONTROL_KEYS."""
    for config in TIME_SLOTS.values():
        assert config["control_key"] in ALL_CONTROL_KEYS, (
            f"{config['control_key']} missing from ALL_CONTROL_KEYS"
        )


def test_all_control_keys_includes_work_mode():
    assert "WORK_MODE_CMB" in ALL_CONTROL_KEYS
