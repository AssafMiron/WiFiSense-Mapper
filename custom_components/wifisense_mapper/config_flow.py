"""WiFiSense Mapper — Config Flow & Options Flow."""
from __future__ import annotations

import logging
import voluptuous as vol
from typing import Any

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_ROUTER_TYPE,
    CONF_ROUTER_HOST,
    CONF_ROUTER_USERNAME,
    CONF_ROUTER_PASSWORD,
    CONF_VACUUM_ENTITIES,
    CONF_POLL_INTERVAL,
    CONF_HEATMAP_ENABLED,
    CONF_ANOMALY_THRESHOLD,
    CONF_BASELINE_DAYS,
    ROUTER_TYPE_DECO,
    ROUTER_TYPE_UNIFI,
    ROUTER_TYPE_NONE,
    ROUTER_TYPES,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_ANOMALY_THRESHOLD,
    DEFAULT_BASELINE_DAYS,
)

_LOGGER = logging.getLogger(__name__)


class WiFiSenseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow for WiFiSense Mapper.

    Steps:
      1. user   — Router type selection
      2. router — Router credentials (skipped for UniFi/None)
      3. done   — Create entry (vacuum & area links go to Options Flow)
    """

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Choose router type."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            router_type = user_input.get(CONF_ROUTER_TYPE, ROUTER_TYPE_NONE)

            if router_type == ROUTER_TYPE_DECO:
                return await self.async_step_router()
            elif router_type == ROUTER_TYPE_UNIFI:
                # UniFi bridges via existing integration — no credentials needed
                self._data[CONF_ROUTER_HOST] = ""
                self._data[CONF_ROUTER_USERNAME] = ""
                self._data[CONF_ROUTER_PASSWORD] = ""
                return self._create_entry()
            else:
                # No router — CSI-only or vacuum-only mode
                return self._create_entry()

        schema = vol.Schema(
            {
                vol.Required(CONF_ROUTER_TYPE, default=ROUTER_TYPE_DECO): vol.In(
                    {
                        ROUTER_TYPE_DECO: "TP-Link Deco (local API)",
                        ROUTER_TYPE_UNIFI: "UniFi (bridge via HA integration)",
                        ROUTER_TYPE_NONE: "None (CSI / vacuum only)",
                    }
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_router(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Deco router credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input.get(CONF_ROUTER_HOST, "").strip()
            password = user_input.get(CONF_ROUTER_PASSWORD, "")

            if not host:
                errors[CONF_ROUTER_HOST] = "host_required"
            elif not password:
                errors[CONF_ROUTER_PASSWORD] = "password_required"
            else:
                # Quick connectivity test
                from .clients.deco import DecoClient  # noqa: PLC0415
                client = DecoClient(
                    host=host,
                    username=user_input.get(CONF_ROUTER_USERNAME, "admin"),
                    password=password,
                )
                if not await client.async_connect():
                    errors["base"] = "cannot_connect"
                else:
                    await client.async_disconnect()
                    self._data.update(user_input)
                    return self._create_entry()

        schema = vol.Schema(
            {
                vol.Required(CONF_ROUTER_HOST, description={"suggested_value": "192.168.0.1"}): str,
                vol.Optional(CONF_ROUTER_USERNAME, default="admin"): str,
                vol.Required(CONF_ROUTER_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="router", data_schema=schema, errors=errors
        )

    def _create_entry(self) -> FlowResult:
        """Create the config entry."""
        title = "WiFiSense Mapper"
        router_type = self._data.get(CONF_ROUTER_TYPE, ROUTER_TYPE_NONE)
        if router_type == ROUTER_TYPE_DECO:
            title = f"WiFiSense Mapper (Deco @ {self._data.get(CONF_ROUTER_HOST, '')})"
        elif router_type == ROUTER_TYPE_UNIFI:
            title = "WiFiSense Mapper (UniFi bridge)"

        return self.async_create_entry(title=title, data=self._data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "WiFiSenseOptionsFlow":
        return WiFiSenseOptionsFlow(config_entry)


class WiFiSenseOptionsFlow(config_entries.OptionsFlow):
    """Options flow for adjusting polling, thresholds, and vacuum links."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Options: polling interval, anomaly threshold, heatmap toggle."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options

        # Build vacuum entity list for selector
        vacuum_entities = self._discover_vacuum_entity_ids()
        vacuum_options = {eid: eid for eid in vacuum_entities}

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=current.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_HEATMAP_ENABLED,
                    default=current.get(CONF_HEATMAP_ENABLED, True),
                ): bool,
                vol.Optional(
                    CONF_ANOMALY_THRESHOLD,
                    default=current.get(CONF_ANOMALY_THRESHOLD, DEFAULT_ANOMALY_THRESHOLD),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=10.0)),
                vol.Optional(
                    CONF_BASELINE_DAYS,
                    default=current.get(CONF_BASELINE_DAYS, DEFAULT_BASELINE_DAYS),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                vol.Optional(
                    CONF_VACUUM_ENTITIES,
                    default=current.get(CONF_VACUUM_ENTITIES, []),
                ): vol.All(list, [vol.In(vacuum_options)] if vacuum_options else [str]),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    def _discover_vacuum_entity_ids(self) -> list[str]:
        """Return entity IDs of vacuum map image/camera entities."""
        try:
            from .vacuum_helpers import discover_vacuum_maps  # noqa: PLC0415
            sources = discover_vacuum_maps(self.hass)
            return [s.entity_id for s in sources]
        except Exception:  # noqa: BLE001
            return []
