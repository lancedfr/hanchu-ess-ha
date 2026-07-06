"""Hanchuess Home Assistant integration."""
import logging
import os
import asyncio
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.components import websocket_api
import homeassistant.helpers.config_validation as cv
from .const import DOMAIN, PLATFORMS, BASE_URL
from .api import HanchuessApiClient
from .battery import merge_battery_serials
from .coordinator import (
    HanchuessRealtimeCoordinator,
    HanchuessStatisticsCoordinator,
    HanchuessBatteryCoordinator,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_DEVICE_CONTROL = "device_control"
SERVICE_SCHEMA = vol.Schema({
    vol.Required("sn"): cv.string,
    vol.Required("dev_type"): cv.string,
    vol.Required("value"): dict,
})

CARD_URL = "/hacsfiles/hanchuess/hanchuess-energy-card.js"


async def _resolve_station_id(
    hass: HomeAssistant, entry: ConfigEntry, client: HanchuessApiClient
) -> str | None:
    """Return the stored station ID, backfilling it from device status if needed."""
    station_id = entry.data.get("stationId")
    if station_id:
        return station_id

    language = hass.config.language or "en"
    device_status = await client.async_get_device_status(entry.data["sn"], language)
    station_id = device_status.get("stationId") if device_status else None
    if station_id:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "stationId": station_id},
        )
    return station_id


async def _refresh_battery_serials(
    hass: HomeAssistant,
    entry: ConfigEntry,
    client: HanchuessApiClient,
    station_id: str | None,
) -> list[str]:
    """Refresh stored battery serials from the latest station detail."""
    existing_serials = entry.data.get("battery_serials", [])
    if not station_id:
        return existing_serials

    language = hass.config.language or "en"
    station_detail = await client.async_get_station_detail(station_id, language)
    merged_serials = merge_battery_serials(existing_serials, station_detail)
    if merged_serials != existing_serials:
        for other_entry in hass.config_entries.async_entries(DOMAIN):
            if other_entry.data.get("stationId") == station_id:
                hass.config_entries.async_update_entry(
                    other_entry,
                    data={**other_entry.data, "battery_serials": merged_serials},
                )
    return merged_serials


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    try:
        from homeassistant.components.http import StaticPathConfig
        await hass.http.async_register_static_paths([
            StaticPathConfig(
                "/hacsfiles/hanchuess",
                os.path.join(os.path.dirname(__file__), "www"),
                cache_headers=False,
            )
        ])
    except Exception:
        _LOGGER.warning("[HANCHUESS] Static path registration failed")

    websocket_api.async_register_command(hass, ws_iot_get)
    websocket_api.async_register_command(hass, ws_iot_set)
    websocket_api.async_register_command(hass, ws_fast_charge)

    return True


@websocket_api.websocket_command({
    vol.Required("type"): "hanchuess/iot_get",
    vol.Required("sn"): str,
    vol.Required("dev_type"): str,
    vol.Required("keys"): [str],
})
@websocket_api.async_response
async def ws_iot_get(hass, connection, msg):
    inverter_serial_number = msg["sn"]
    dev_type = msg["dev_type"]
    keys = msg["keys"]
    target_client = None
    for eid, data in hass.data[DOMAIN].items():
        if isinstance(data, dict) and "realtime" in data:
            if data["realtime"].entry.data.get("sn") == inverter_serial_number:
                target_client = data["realtime"].client
                break
    if not target_client:
        connection.send_error(
            msg["id"], "not_found", f"Device {inverter_serial_number} not found"
        )
        return
    result = await target_client.async_iot_get(inverter_serial_number, dev_type, keys)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    vol.Required("type"): "hanchuess/iot_set",
    vol.Required("sn"): str,
    vol.Required("dev_type"): str,
    vol.Required("value"): dict,
})
@websocket_api.async_response
async def ws_iot_set(hass, connection, msg):
    inverter_serial_number = msg["sn"]
    dev_type = msg["dev_type"]
    value = msg["value"]
    for k, v in value.items():
        if isinstance(v, str):
            try:
                value[k] = int(v)
            except ValueError:
                pass
    target_client = None
    for eid, data in hass.data[DOMAIN].items():
        if isinstance(data, dict) and "realtime" in data:
            if data["realtime"].entry.data.get("sn") == inverter_serial_number:
                target_client = data["realtime"].client
                break
    if not target_client:
        connection.send_error(
            msg["id"], "not_found", f"Device {inverter_serial_number} not found"
        )
        return
    result = await target_client.async_device_control(
        inverter_serial_number, dev_type, value
    )
    if result.get("success"):
        connection.send_result(msg["id"], result.get("data", {}))
    else:
        connection.send_error(msg["id"], "control_failed", result.get("msg", "Unknown error"))


@websocket_api.websocket_command({
    vol.Required("type"): "hanchuess/fast_charge",
    vol.Required("sn"): str,
    vol.Required("act"): vol.All(int, vol.Range(min=-3, max=3)),
    vol.Required("duration"): vol.All(int, vol.Range(min=0)),
})
@websocket_api.async_response
async def ws_fast_charge(hass, connection, msg):
    inverter_serial_number = msg["sn"]
    target_client = None
    for eid, data in hass.data[DOMAIN].items():
        if isinstance(data, dict) and "realtime" in data:
            if data["realtime"].entry.data.get("sn") == inverter_serial_number:
                target_client = data["realtime"].client
                break
    if not target_client:
        connection.send_error(
            msg["id"], "not_found", f"Device {inverter_serial_number} not found"
        )
        return
    result = await target_client.async_fast_charge_discharge(
        inverter_serial_number, msg["act"], msg["duration"]
    )
    if result.get("success"):
        connection.send_result(msg["id"], result.get("data", {}))
    else:
        connection.send_error(msg["id"], "fast_charge_failed", result.get("msg", "Unknown error"))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    if not hass.data[DOMAIN].get("_card_registered"):
        try:
            lovelace_data = hass.data.get("lovelace")
            if lovelace_data and lovelace_data.resource_mode == "storage":
                resources = lovelace_data.resources
                existing = [r for r in resources.async_items() if "hanchuess-energy-card" in r.get("url", "")]
                if not existing:
                    await resources.async_create_item({
                        "res_type": "module",
                        "url": CARD_URL,
                    })
                    _LOGGER.info("[HANCHUESS] Card resource auto-registered")
            hass.data[DOMAIN]["_card_registered"] = True
        except Exception as err:
            _LOGGER.warning("[HANCHUESS] Card resource auto-register failed: %s", err)

    if "_client" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["_client"] = HanchuessApiClient(
            domain=BASE_URL,
            token=entry.data.get("token"),
        )
    client = hass.data[DOMAIN]["_client"]

    coordinator = HanchuessRealtimeCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    stats_coordinator = HanchuessStatisticsCoordinator(hass, entry, client)
    await stats_coordinator.async_config_entry_first_refresh()

    station_id = await _resolve_station_id(hass, entry, client)
    battery_serials = await _refresh_battery_serials(hass, entry, client, station_id)

    battery_coordinator = None
    if battery_serials:
        battery_coordinator = HanchuessBatteryCoordinator(hass, entry, client)
        await battery_coordinator.async_config_entry_first_refresh()

    # Fetch menu to get device-specific min/max values for number entities
    language = hass.config.language or "en"
    inverter_serial_number = entry.data["sn"]
    number_limits = {
        "CHG_PWR_LMT": {"min": 0, "max": 5000},
        "DSCHG_PWR_LMT": {"min": 0, "max": 5000},
        "CHG_BAT_SOC_LMT": {"min": 50, "max": 100},
        "DSCHG_BAT_SOC_LMT": {"min": 5, "max": 45},
        "DTU_AC_CHG_SOC_LMT": {"min": 20, "max": 100},
    }
    try:
        menu_data = await client.async_get_menu(inverter_serial_number, language)
        energy = menu_data.get("data", {}).get("energy", {})
        for group in energy.get("items", []):
            for item in group:
                signal = item.get("itemCodeSignal", "")
                if signal in number_limits:
                    try:
                        min_val = float(item.get("minVal", number_limits[signal]["min"]))
                        max_val = float(item.get("maxVal", number_limits[signal]["max"]))
                        if min_val is not None and max_val is not None:
                            number_limits[signal]["min"] = min_val
                            number_limits[signal]["max"] = max_val
                            _LOGGER.info("[HANCHUESS] %s limits: %s-%s", signal, min_val, max_val)
                    except (ValueError, TypeError):
                        pass
    except Exception as err:
        _LOGGER.warning("[HANCHUESS] Could not fetch menu for number limits, using defaults: %s", err)

    # Fetch all current values in one iotGet call with timeout
    startup_values = {}
    try:
        startup_values = await asyncio.wait_for(
            client.async_iot_get(
                inverter_serial_number,
                "2",
                [
                    "WORK_MODE_CMB",
                    "CHG_PWR_LMT",
                    "DSCHG_PWR_LMT",
                    "CHG_BAT_SOC_LMT",
                    "DSCHG_BAT_SOC_LMT",
                    "DTU_AC_CHG_SOC_LMT",
                    "TCT_START_1", "TCT_END_1",
                    "TCT_START_2", "TCT_END_2",
                    "TCT_START_3", "TCT_END_3",
                    "TDT_START_1", "TDT_END_1",
                    "TDT_START_2", "TDT_END_2",
                    "TDT_START_3", "TDT_END_3",
                ]
            ),
            timeout=8.0
        )
        _LOGGER.info("[HANCHUESS] Startup values fetched: %s", startup_values)
    except asyncio.TimeoutError:
        _LOGGER.warning("[HANCHUESS] Startup iotGet timed out, entities will populate on next poll")
    except Exception as err:
        _LOGGER.warning("[HANCHUESS] Could not fetch startup values: %s", err)

    hass.data[DOMAIN][entry.entry_id] = {
        "realtime": coordinator,
        "statistics": stats_coordinator,
        "battery": battery_coordinator,
        "number_limits": number_limits,
        "startup_values": startup_values,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_device_control(call: ServiceCall):
        inverter_serial_number = call.data["sn"]
        dev_type = call.data["dev_type"]
        value = call.data["value"]
        _LOGGER.info(
            "[HANCHUESS] service device_control: %s %s",
            inverter_serial_number,
            value,
        )
        target_client = None
        for eid, data in hass.data[DOMAIN].items():
            if isinstance(data, dict) and "realtime" in data:
                if data["realtime"].entry.data.get("sn") == inverter_serial_number:
                    target_client = data["realtime"].client
                    break
        if not target_client:
            _LOGGER.error(
                "[HANCHUESS] device_control: device %s not found",
                inverter_serial_number,
            )
            return
        result = await target_client.async_device_control(
            inverter_serial_number, dev_type, value
        )
        if not result.get("success"):
            _LOGGER.error("[HANCHUESS] device_control failed: %s", result.get("msg"))

    if not hass.services.has_service(DOMAIN, SERVICE_DEVICE_CONTROL):
        hass.services.async_register(
            DOMAIN, SERVICE_DEVICE_CONTROL, handle_device_control, schema=SERVICE_SCHEMA
        )

    async def handle_fast_charge(call: ServiceCall):
        inverter_serial_number = call.data.get("sn")

        if not inverter_serial_number:
            for eid, data in hass.data[DOMAIN].items():
                if isinstance(data, dict) and "realtime" in data:
                    inverter_serial_number = data["realtime"].entry.data.get("sn")
                    break

        if not inverter_serial_number:
            _LOGGER.error("[HANCHUESS] fast_charge: No inverter serial found")
            return

        act = call.data["act"]
        duration = call.data.get("duration", 0)

        target_client = None
        for eid, data in hass.data[DOMAIN].items():
            if isinstance(data, dict) and "realtime" in data:
                if data["realtime"].entry.data.get("sn") == inverter_serial_number:
                    target_client = data["realtime"].client
                    break

        if not target_client:
            _LOGGER.error(
                "[HANCHUESS] fast_charge: device %s not found",
                inverter_serial_number,
            )
            return

        result = await target_client.async_fast_charge_discharge(
            inverter_serial_number, act, duration
        )

        if not result.get("success"):
            _LOGGER.error("[HANCHUESS] fast_charge failed: %s", result.get("msg"))

    if not hass.services.has_service(DOMAIN, "fast_charge"):
        hass.services.async_register(
            DOMAIN,
            "fast_charge",
            handle_fast_charge,
            schema=vol.Schema({
                vol.Optional("sn"): cv.string,
                vol.Required("act"): vol.All(int, vol.Range(min=-3, max=3)),
                vol.Optional("duration"): vol.All(int, vol.Range(min=0)),
            }),
        )

    pending = entry.data.get("pending_devices", [])
    if pending:
        for item in pending:
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data={
                    "sn": item["sn"],
                    "dev_type": item.get("devType", "2"),
                    "token": entry.data["token"],
                    "stationId": entry.data.get("stationId"),
                    "battery_serials": entry.data.get("battery_serials", []),
                },
            )
        new_data = {k: v for k, v in entry.data.items() if k != "pending_devices"}
        hass.config_entries.async_update_entry(entry, data=new_data)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
