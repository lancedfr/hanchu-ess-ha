"""DataUpdateCoordinator for Hanchuess."""
import logging
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed
from .api import HanchuessApiClient, ReauthRequired
from .const import (
    DOMAIN,
    CONF_REALTIME_INTERVAL,
    CONF_STATISTICS_INTERVAL,
    CONF_BATTERY_INTERVAL,
    DEFAULT_REALTIME_INTERVAL,
    DEFAULT_STATISTICS_INTERVAL,
    DEFAULT_BATTERY_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _raise_auth_failed(client: HanchuessApiClient, msg: str):
    """Raise ConfigEntryAuthFailed once, UpdateFailed for the rest."""
    if not client._reauth_triggered:
        client._reauth_triggered = True
        raise ConfigEntryAuthFailed(msg)
    raise UpdateFailed(msg)


async def _try_refresh(client, update_cb, force=False):
    """Try to refresh token. Returns True if refreshed, raises on reauth needed."""
    try:
        new_token = await client.async_refresh_token(force=force)
    except ReauthRequired:
        _raise_auth_failed(client, "Token refresh returned 90076, reauth required")
    if new_token:
        update_cb(new_token)
        return True
    return False


class HanchuessRealtimeCoordinator(DataUpdateCoordinator):
    """Realtime data coordinator (60s)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: HanchuessApiClient):
        super().__init__(
            hass,
            _LOGGER,
            name="hanchuess_realtime",
            update_interval=timedelta(
                seconds=entry.options.get(
                    CONF_REALTIME_INTERVAL, DEFAULT_REALTIME_INTERVAL
                )
            ),
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> dict:
        sn = self.entry.data["sn"]
        language = self.hass.config.language or "en"

        # Proactive refresh at 25 days
        if self.client.should_refresh_token():
            await _try_refresh(self.client, self._update_entry_token)

        data = await self.client.async_get_device_status(sn, language)

        # Reactive refresh on 401
        if data and data.get("_token_expired"):
            refreshed = await _try_refresh(self.client, self._update_entry_token, force=True)
            if refreshed:
                data = await self.client.async_get_device_status(sn, language)

        if data and data.get("_token_expired"):
            _raise_auth_failed(self.client, "Token expired and refresh failed")

        if not data:
            raise UpdateFailed("Failed to get device status")
        return data

    def _update_entry_token(self, token: str):
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, "token": token}
            )


class HanchuessStatisticsCoordinator(DataUpdateCoordinator):
    """Statistics data coordinator (5min)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: HanchuessApiClient):
        super().__init__(
            hass,
            _LOGGER,
            name="hanchuess_statistics",
            update_interval=timedelta(
                seconds=entry.options.get(
                    CONF_STATISTICS_INTERVAL, DEFAULT_STATISTICS_INTERVAL
                )
            ),
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> dict:
        sn = self.entry.data["sn"]
        language = self.hass.config.language or "en"

        data = await self.client.async_get_device_statistics(sn, language)

        if data and data.get("_token_expired"):
            refreshed = await _try_refresh(self.client, self._update_entry_token, force=True)
            if refreshed:
                data = await self.client.async_get_device_statistics(sn, language)

        if data and data.get("_token_expired"):
            _raise_auth_failed(self.client, "Token expired and refresh failed")

        if not data:
            raise UpdateFailed("Failed to get device statistics")
        return data

    def _update_entry_token(self, token: str):
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, "token": token}
            )


class HanchuessBatteryCoordinator(DataUpdateCoordinator):
    """Battery data coordinator for per-BMS sensors."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: HanchuessApiClient):
        super().__init__(
            hass,
            _LOGGER,
            name="hanchuess_battery",
            update_interval=timedelta(
                seconds=entry.options.get(
                    CONF_BATTERY_INTERVAL, DEFAULT_BATTERY_INTERVAL
                )
            ),
        )
        self.entry = entry
        self.client = client

    def _battery_sns(self) -> list[str]:
        batteries = self.entry.data.get("batteries", [])
        serials = []
        for battery in batteries:
            if isinstance(battery, dict):
                bms_sn = battery.get("sn") or battery.get("devId")
                if bms_sn:
                    serials.append(bms_sn)
            elif battery:
                serials.append(str(battery))
        return serials

    async def _async_update_data(self) -> dict:
        battery_sns = self._battery_sns()
        if not battery_sns:
            return {}

        # Proactive refresh at 25 days
        if self.client.should_refresh_token():
            await _try_refresh(self.client, self._update_entry_token)

        previous = self.data if isinstance(self.data, dict) else {}
        data: dict[str, dict] = {}
        needs_refresh = False

        for battery_sn in battery_sns:
            detail = await self.client.async_get_battery_detail(battery_sn)
            if detail and detail.get("_token_expired"):
                needs_refresh = True
                break
            if detail:
                data[battery_sn] = detail
            elif battery_sn in previous:
                data[battery_sn] = previous[battery_sn]

        if needs_refresh:
            refreshed = await _try_refresh(self.client, self._update_entry_token, force=True)
            if refreshed:
                data = {}
                for battery_sn in battery_sns:
                    detail = await self.client.async_get_battery_detail(battery_sn)
                    if detail and detail.get("_token_expired"):
                        _raise_auth_failed(self.client, "Token expired and refresh failed")
                    if detail:
                        data[battery_sn] = detail
                    elif battery_sn in previous:
                        data[battery_sn] = previous[battery_sn]

        if not data:
            raise UpdateFailed("Failed to get battery details")
        return data

    def _update_entry_token(self, token: str):
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, "token": token}
            )
