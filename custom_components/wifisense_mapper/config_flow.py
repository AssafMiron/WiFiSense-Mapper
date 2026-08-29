"""WiFiSense Mapper — Config Flow & Options Flow."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback

from .const import (
    CONF_ANOMALY_THRESHOLD,
    CONF_BASELINE_DAYS,
    CONF_HEATMAP_ENABLED,
    CONF_POLL_INTERVAL,
    CONF_ROUTER_HOST,
    CONF_ROUTER_PASSWORD,
    CONF_ROUTER_TYPE,
    CONF_ROUTER_USERNAME,
    CONF_VACUUM_ENTITIES,
    DEFAULT_ANOMALY_THRESHOLD,
    DEFAULT_BASELINE_DAYS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    ROUTER_TYPE_DECO,
    ROUTER_TYPE_NONE,
    ROUTER_TYPE_UNIFI,
)
from .router_discovery import (
    DiscoveredRouter,
    discover_all_routers,
    find_discovered_router,
    get_first_discovered_router_of_type,
)

_LOGGER = logging.getLogger(__name__)


class WiFiSenseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step config flow for WiFiSense Mapper.

    Steps:
      1. user   — Router type selection (with auto-detection & 1-click setup)
      2. router — Router credentials (skipped for 1-click auto setup, UniFi, or None)
      3. done   — Create entry (vacuum & area links go to Options Flow)
    """

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._discovered_routers: list[DiscoveredRouter] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Choose router type or select an auto-detected router integration."""
        errors: dict[str, str] = {}
        self._discovered_routers = discover_all_routers(self.hass)

        if user_input is not None:
            chosen = user_input.get(CONF_ROUTER_TYPE, ROUTER_TYPE_NONE)

            # Check if user chose an auto-detected router (1-Click Auto Setup)
            if chosen.startswith("auto_"):
                discovery_id = chosen.removeprefix("auto_")
                router = find_discovered_router(self.hass, discovery_id)
                if router:
                    self._data[CONF_ROUTER_TYPE] = router.router_type
                    self._data[CONF_ROUTER_HOST] = router.host
                    self._data[CONF_ROUTER_USERNAME] = router.username
                    self._data[CONF_ROUTER_PASSWORD] = router.password or ""

                    if router.is_bridge_only:
                        return self._create_entry()

                    # Direct API (e.g. Deco) — test connection with discovered credentials
                    if router.password:
                        from .clients.deco import DecoClient

                        client = DecoClient(
                            host=router.host,
                            username=router.username,
                            password=router.password,
                        )
                        if await client.async_connect():
                            await client.async_disconnect()
                            return self._create_entry()
                        _LOGGER.warning(
                            "Auto-connect failed for discovered %s at %s. Falling back to manual entry.",
                            router.title,
                            router.host,
                        )
                        errors["base"] = "cannot_connect"

                    # If no password was extracted or connection failed, route to manual setup with pre-filled IP
                    return await self.async_step_router(errors=errors)

            # Standard manual selections
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

        # Build options dynamically including detected routers
        options: dict[str, str] = {}
        default_selection = ROUTER_TYPE_DECO

        # Add 1-Click options for discovered routers
        has_auto_option = False
        for router in self._discovered_routers:
            if router.router_type == ROUTER_TYPE_DECO:
                opt_key = f"auto_{router.discovery_id}"
                if router.password:
                    options[opt_key] = (
                        f"TP-Link Deco (Detected at {router.host} — 1-Click Auto Setup)"
                    )
                else:
                    options[opt_key] = (
                        f"TP-Link Deco (Detected at {router.host})"
                    )
                if not has_auto_option:
                    default_selection = opt_key
                    has_auto_option = True
            elif router.router_type == ROUTER_TYPE_UNIFI:
                options[ROUTER_TYPE_UNIFI] = "UniFi (Detected — bridge via HA integration)"

        # Manual / Standard options
        options[ROUTER_TYPE_DECO] = "TP-Link Deco (Manual configuration)"
        if ROUTER_TYPE_UNIFI not in options:
            options[ROUTER_TYPE_UNIFI] = "UniFi (bridge via HA integration)"
        options[ROUTER_TYPE_NONE] = "None (CSI / vacuum only)"

        schema = vol.Schema(
            {
                vol.Required(CONF_ROUTER_TYPE, default=default_selection): vol.In(
                    options
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_router(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Step 2: Deco router credentials with smart pre-filled defaults."""
        step_errors: dict[str, str] = errors or {}

        if user_input is not None:
            host = user_input.get(CONF_ROUTER_HOST, "").strip()
            password = user_input.get(CONF_ROUTER_PASSWORD, "")

            if not host:
                step_errors[CONF_ROUTER_HOST] = "host_required"
            elif not password:
                step_errors[CONF_ROUTER_PASSWORD] = "password_required"
            else:
                # Quick connectivity test
                from .clients.deco import DecoClient

                client = DecoClient(
                    host=host,
                    username=user_input.get(CONF_ROUTER_USERNAME, "admin"),
                    password=password,
                )
                if not await client.async_connect():
                    step_errors["base"] = "cannot_connect"
                else:
                    await client.async_disconnect()
                    self._data.update(user_input)
                    return self._create_entry()

        # Dynamic smart pre-fill: use detected Deco IP if available
        suggested_host = self._data.get(CONF_ROUTER_HOST) or ""
        suggested_username = self._data.get(CONF_ROUTER_USERNAME) or "admin"

        if not suggested_host:
            detected = get_first_discovered_router_of_type(
                self.hass, ROUTER_TYPE_DECO
            )
            if detected:
                suggested_host = detected.host
                suggested_username = detected.username or "admin"
            else:
                suggested_host = "192.168.0.1"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ROUTER_HOST, description={"suggested_value": suggested_host}
                ): str,
                vol.Optional(
                    CONF_ROUTER_USERNAME,
                    default=suggested_username,
                    description={"suggested_value": suggested_username},
                ): str,
                vol.Required(CONF_ROUTER_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="router", data_schema=schema, errors=step_errors
        )

    def _create_entry(self) -> ConfigFlowResult:
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
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> WiFiSenseOptionsFlow:
        return WiFiSenseOptionsFlow()


class WiFiSenseOptionsFlow(config_entries.OptionsFlow):
    """Options flow for adjusting polling, thresholds, and vacuum links."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
                    default=current.get(
                        CONF_ANOMALY_THRESHOLD, DEFAULT_ANOMALY_THRESHOLD
                    ),
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
            from .vacuum_helpers import discover_vacuum_maps

            sources = discover_vacuum_maps(self.hass)
            return [s.entity_id for s in sources]
        except Exception:  # noqa: BLE001
            return []
