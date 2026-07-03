"""Config flow for Hanchuess."""
import logging
import time
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant import config_entries
from homeassistant.config_entries import OptionsFlowWithReload
from homeassistant.core import callback
from .const import (
    DOMAIN,
    BASE_URL,
    CONF_REALTIME_INTERVAL,
    CONF_STATISTICS_INTERVAL,
    CONF_BATTERY_INTERVAL,
    CONF_FAST_CHARGE_DURATION,
    DEFAULT_REALTIME_INTERVAL,
    DEFAULT_STATISTICS_INTERVAL,
    DEFAULT_BATTERY_INTERVAL,
    DEFAULT_FAST_CHARGE_DURATION,
)
from .api import HanchuessApiClient

_LOGGER = logging.getLogger(__name__)


class HanchuessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._token = None
        self._devices = []
        self._client = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HanchuessOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Step 1: Login."""
        # Check if already logged in — use shared client directly
        existing = self.hass.data.get(DOMAIN, {})
        shared_client = existing.get("_client")
        if shared_client and shared_client.token:
            self._client = shared_client
            self._token = shared_client.token
            self._devices = await shared_client.async_get_devices()
            if self._devices:
                return await self.async_step_select_device()

        errors = {}
        if user_input is not None:
            client = HanchuessApiClient(BASE_URL)
            token = await client.async_login(
                user_input["account"], user_input["password"]
            )
            if token:
                self._client = client
                self._token = client.token
                self._devices = await client.async_get_devices()
                if self._devices:
                    return await self.async_step_select_device()
                errors["base"] = "no_devices"
            else:
                errors["base"] = "auth_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("account"): str,
                vol.Required("password"): str,
            }),
            errors=errors,
        )

    async def async_step_select_device(self, user_input=None):
        """Step 2: Select devices (multi-select)."""
        errors = {}
        if user_input is not None:
            selected = user_input.get("devices", [])
            if not selected:
                errors["base"] = "no_devices"
            else:
                # Create entry for the first device
                sn = selected[0]
                await self.async_set_unique_id(sn)
                self._abort_if_unique_id_configured()

                # Find devType
                dev_type = self._find_device_type(sn)
                batteries = await self._discover_batteries(sn)

                # Build pending devices with devType
                pending = []
                for p_sn in selected[1:]:
                    pending.append(
                        {
                            "sn": p_sn,
                            "devType": self._find_device_type(p_sn),
                            "batteries": await self._discover_batteries(p_sn),
                        }
                    )

                return self.async_create_entry(
                    title=f"Hanchuess {sn}",
                    data={
                        "sn": sn,
                        "dev_type": dev_type,
                        "batteries": batteries,
                        "token": self._token,
                        "pending_devices": pending,
                    },
                )

        # Filter inverters and exclude already configured
        configured_ids = set()
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            configured_ids.add(entry.data.get("sn"))

        available = {
            d["sn"]: d["sn"]
            for d in self._devices
            if d.get("devType") == "2" and d["sn"] not in configured_ids
        }

        if not available:
            return self.async_abort(reason="no_devices")

        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({
                vol.Required("devices"): cv.multi_select(available),
            }),
            errors=errors,
        )

    async def async_step_import(self, data: dict):
        """Handle creation of additional devices from pending list."""
        sn = data["sn"]
        await self.async_set_unique_id(sn)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Hanchuess {sn}",
            data={
                "sn": sn,
                "dev_type": data.get("dev_type", "2"),
                "batteries": data.get("batteries", []),
                "token": data["token"],
                "pending_devices": [],
            },
        )

    async def async_step_reauth(self, entry_data: dict):
        """Handle reauth triggered by ConfigEntryAuthFailed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Re-authenticate with user credentials."""
        errors = {}
        if user_input is not None:
            client = HanchuessApiClient(BASE_URL)
            token = await client.async_login(
                user_input["account"], user_input["password"]
            )
            if token:
                # Update token for ALL entries and shared client
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    self.hass.config_entries.async_update_entry(
                        entry, data={**entry.data, "token": token}
                    )
                # Update shared client
                domain_data = self.hass.data.get(DOMAIN, {})
                if "_client" in domain_data:
                    domain_data["_client"]._token = token
                    domain_data["_client"]._token_time = time.time()
                    domain_data["_client"]._reauth_triggered = False
                # Schedule reload for all entries (non-blocking)
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    self.hass.async_create_task(
                        self.hass.config_entries.async_reload(entry.entry_id)
                    )
                return self.async_abort(reason="reauth_successful")
            errors["base"] = "auth_failed"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required("account"): str,
                vol.Required("password"): str,
            }),
            errors=errors,
        )

    def _find_device_type(self, sn: str) -> str:
        for device in self._devices:
            if device["sn"] == sn:
                return device.get("devType", "2")
        return "2"

    async def _discover_batteries(self, sn: str) -> list[dict]:
        if not self._client:
            return []
        station_id, bms_list = await self._client.async_get_station_batteries(sn)
        batteries = []
        for battery in bms_list:
            bms_sn = battery.get("sn") or battery.get("devId")
            if not bms_sn:
                continue
            batteries.append(
                {
                    "sn": bms_sn,
                    "devId": battery.get("devId", bms_sn),
                    "stationId": battery.get("stationId", station_id),
                }
            )
        _LOGGER.debug("[HANCHUESS] discovered %s batteries for inverter %s", len(batteries), sn)
        return batteries


class HanchuessOptionsFlow(OptionsFlowWithReload):
    """Options flow: poll intervals and fast-charge duration."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        data_schema = vol.Schema({
            vol.Required(
                CONF_REALTIME_INTERVAL,
                default=options.get(CONF_REALTIME_INTERVAL, DEFAULT_REALTIME_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Required(
                CONF_STATISTICS_INTERVAL,
                default=options.get(CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=300, max=86400)),
            vol.Required(
                CONF_BATTERY_INTERVAL,
                default=options.get(CONF_BATTERY_INTERVAL, DEFAULT_BATTERY_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=300, max=86400)),
            vol.Required(
                CONF_FAST_CHARGE_DURATION,
                default=options.get(CONF_FAST_CHARGE_DURATION, DEFAULT_FAST_CHARGE_DURATION),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=240)),
        })

        return self.async_show_form(step_id="init", data_schema=data_schema)
