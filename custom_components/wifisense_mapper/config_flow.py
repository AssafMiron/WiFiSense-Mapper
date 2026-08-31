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
    CONF_PERSON_TAGS,
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


from homeassistant.helpers import selector


class WiFiSenseOptionsFlow(config_entries.OptionsFlow):
    """Options flow for adjusting polling, thresholds, AP area mapping, vacuum alignment, and troubleshooting."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Main options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "general",
                "person_tracking",
                "ap_mapping",
                "vacuum_mapping",
                "troubleshooting",
            ],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """General settings: polling interval, anomaly threshold, heatmap toggle."""
        current = dict(self.config_entry.options)
        if user_input is not None:
            current.update(user_input)
            return self.async_create_entry(title="", data=current)

        # Build vacuum entity list for selector
        vacuum_entities = self._discover_vacuum_entity_ids()

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
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=eid, label=eid)
                            for eid in vacuum_entities
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
                if vacuum_entities
                else selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[],
                        multiple=True,
                        custom_value=True,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_person_tracking(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Map WiFi client devices / wearables to Home Assistant Person entities."""
        current = dict(self.config_entry.options)
        current_person_tags: dict[str, Any] = dict(current.get(CONF_PERSON_TAGS, {}))

        # Discover all HA person entities
        person_entities = self.hass.states.async_entity_ids("person")
        person_options = {"": "None (Not Tracked as Person)"}
        for pe in person_entities:
            state = self.hass.states.get(pe)
            name = state.name if state and state.name else pe
            person_options[pe] = f"{name} ({pe})"

        clients = self._get_known_clients()

        if user_input is not None:
            updated_tags = dict(current_person_tags)
            for mac, label in clients.items():
                val = (
                    user_input.get(label)
                    or user_input.get(f"person_{mac.replace(':', '_')}")
                    or user_input.get(mac)
                )
                if val:
                    person_name = person_options.get(val, val).split(" (")[0]
                    updated_tags[mac] = {
                        "person_entity_id": val,
                        "person_name": person_name,
                    }
                elif mac in updated_tags:
                    updated_tags.pop(mac)
            current[CONF_PERSON_TAGS] = updated_tags
            return self.async_create_entry(title="", data=current)

        schema_dict: dict[Any, Any] = {}
        for mac, label in clients.items():
            existing = current_person_tags.get(mac, {})
            existing_person = (
                existing
                if isinstance(existing, str)
                else existing.get("person_entity_id", "")
            )
            schema_dict[vol.Optional(label, default=existing_person)] = vol.In(
                person_options
            )

        if not schema_dict:
            schema_dict[
                vol.Optional("info_no_clients", default="No WiFi clients detected yet")
            ] = str

        return self.async_show_form(
            step_id="person_tracking",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "client_count": str(len(clients)),
            },
        )

    async def async_step_ap_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Map discovered Deco nodes / APs to Home Assistant Areas."""
        current = dict(self.config_entry.options)
        current_node_areas: dict[str, str] = dict(current.get("node_area_map", {}))

        from homeassistant.helpers import area_registry as ar

        area_reg = ar.async_get(self.hass)
        area_options = {"": "Auto-detect / None"}
        for area in area_reg.areas.values():
            area_options[area.id] = area.name

        # Find all known APs from coordinator data or registered devices
        ap_list = self._get_known_aps()

        if user_input is not None:
            updated_map = dict(current_node_areas)
            for mac, label in ap_list.items():
                val = (
                    user_input.get(label)
                    or user_input.get(f"ap_{mac.replace(':', '_')}")
                    or user_input.get(mac)
                )
                if val:
                    updated_map[mac] = val
                elif mac in updated_map:
                    updated_map.pop(mac)
            current["node_area_map"] = updated_map
            return self.async_create_entry(title="", data=current)

        schema_dict: dict[Any, Any] = {}
        for mac, label in ap_list.items():
            default_area = current_node_areas.get(mac, "")
            schema_dict[vol.Optional(label, default=default_area)] = vol.In(
                area_options
            )

        if not schema_dict:
            schema_dict[vol.Optional("info_no_aps", default="No APs detected yet")] = str

        return self.async_show_form(
            step_id="ap_mapping",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "ap_count": str(len(ap_list)),
            },
        )

    async def async_step_vacuum_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Map Roborock / vacuum room segments to Home Assistant Areas."""
        current = dict(self.config_entry.options)
        current_vac_mappings: dict[str, str] = dict(
            current.get("vacuum_room_mappings", {})
        )

        from homeassistant.helpers import area_registry as ar

        area_reg = ar.async_get(self.hass)
        area_options = {"": "Auto-detect / None"}
        for area in area_reg.areas.values():
            area_options[area.id] = area.name

        # Discover vacuum room segments
        segments = self._get_vacuum_segments()

        if user_input is not None:
            updated_map = dict(current_vac_mappings)
            for seg_id, label in segments.items():
                val = (
                    user_input.get(label)
                    or user_input.get(f"vac_seg_{seg_id}")
                    or user_input.get(str(seg_id))
                )
                if val:
                    updated_map[str(seg_id)] = val
                elif str(seg_id) in updated_map:
                    updated_map.pop(str(seg_id))
            current["vacuum_room_mappings"] = updated_map
            return self.async_create_entry(title="", data=current)

        schema_dict: dict[Any, Any] = {}
        for seg_id, label in segments.items():
            default_area = current_vac_mappings.get(str(seg_id), "")
            schema_dict[vol.Optional(label, default=default_area)] = vol.In(
                area_options
            )

        if not schema_dict:
            schema_dict[
                vol.Optional("info_no_vac", default="No vacuum room segments detected")
            ] = str

        return self.async_show_form(
            step_id="vacuum_mapping",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "segment_count": str(len(segments)),
            },
        )

    async def async_step_troubleshooting(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Troubleshooting: Pillow status, system health, and recent WiFiSense logs."""
        if user_input is not None:
            return self.async_show_menu(
                step_id="init",
                menu_options=["general", "person_tracking", "ap_mapping", "vacuum_mapping", "troubleshooting"],
            )

        pillow_installed = False
        pillow_version = ""
        try:
            import PIL  # type: ignore[import]

            pillow_installed = True
            pillow_version = getattr(PIL, "__version__", "Unknown")
        except ImportError:
            pillow_installed = False

        pillow_status = (
            f"✅ Installed (v{pillow_version})"
            if pillow_installed
            else "❌ Not Installed (Using pure-Python BMP/PNG fallback. Install via: pip install Pillow)"
        )

        entry_data = self.hass.data.get(DOMAIN, {}).get(
            self.config_entry.entry_id, {}
        )
        coordinator = entry_data.get("coordinator")
        recent_logs = (
            coordinator.get_recent_logs(max_lines=25) if coordinator else []
        )
        log_text = (
            "\n".join(recent_logs) if recent_logs else "No logs recorded yet."
        )

        router_connected = (
            coordinator.router_client.is_connected
            if coordinator and coordinator.router_client
            else False
        )
        router_status = (
            "✅ Connected" if router_connected else "⚠️ Disconnected / Not Polled"
        )

        coverage = coordinator.get_area_coverage_summary() if coordinator else {}
        cov_info = (
            f"{coverage.get('covered_count', 0)}/{coverage.get('total_areas', 0)} Areas Covered ({coverage.get('cross_covered_count', 0)} Cross-covered)"
            if coverage
            else "N/A"
        )

        return self.async_show_form(
            step_id="troubleshooting",
            data_schema=vol.Schema({}),
            description_placeholders={
                "pillow_status": pillow_status,
                "router_status": router_status,
                "coverage_info": cov_info,
                "recent_logs": log_text,
            },
        )

    def _get_known_aps(self) -> dict[str, str]:
        """Return dict of ap_mac -> display name (matched with HA Device Registry)."""
        aps: dict[str, str] = {}
        from homeassistant.helpers import device_registry as dr

        from .clients.base import RouterClient

        dev_reg = dr.async_get(self.hass)
        mac_to_dev_name: dict[str, str] = {}

        devices_iter = (
            dev_reg.devices
            if not isinstance(dev_reg.devices, dict)
            else dev_reg.devices.values()
        )
        for device in devices_iter:
            dev_name = device.name_by_user or device.name
            if not dev_name:
                continue
            for conn in device.connections:
                if len(conn) >= 2 and conn[0] == dr.CONNECTION_NETWORK_MAC:
                    norm = RouterClient.normalize_mac(str(conn[1]))
                    if norm:
                        mac_to_dev_name[norm] = dev_name

        try:
            entry_data = self.hass.data.get(DOMAIN, {}).get(
                self.config_entry.entry_id, {}
            )
            coordinator = entry_data.get("coordinator")
            if coordinator and coordinator.ap_stats:
                for mac, ap in coordinator.ap_stats.items():
                    norm_mac = RouterClient.normalize_mac(mac)
                    name_part = mac_to_dev_name.get(norm_mac) or ap.name or "Deco Hub"
                    aps[norm_mac] = f"{name_part} ({norm_mac})"
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Could not read AP stats: %s", exc)

        for mac in self.config_entry.options.get("node_area_map", {}):
            norm_mac = RouterClient.normalize_mac(mac)
            if norm_mac not in aps:
                name_part = mac_to_dev_name.get(norm_mac) or "Deco Hub"
                aps[norm_mac] = f"{name_part} ({norm_mac})"

        return aps

    def _get_vacuum_segments(self) -> dict[str, str]:
        """Return dict of segment_id -> segment_name."""
        segments: dict[str, str] = {}
        configured_vacs = self.config_entry.options.get(CONF_VACUUM_ENTITIES, [])

        try:
            from .vacuum_helpers import discover_vacuum_maps

            sources = discover_vacuum_maps(self.hass, additional_entity_ids=configured_vacs)
            for src in sources:
                for seg in src.room_segments:
                    sid = str(seg.segment_id)
                    sname = seg.name or f"Room {sid}"
                    segments[sid] = f"{sname} (Segment {sid})" if sid not in sname else sname
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Could not read vacuum segments: %s", exc)

        for seg_id in self.config_entry.options.get("vacuum_room_mappings", {}):
            sid = str(seg_id)
            if sid not in segments:
                segments[sid] = f"Room {sid} (Segment {sid})"

        return segments

    def _discover_vacuum_entity_ids(self) -> list[str]:
        """Return entity IDs of vacuum map image/camera entities."""
        try:
            from .vacuum_helpers import discover_vacuum_maps

            sources = discover_vacuum_maps(self.hass)
            return [s.entity_id for s in sources]
        except Exception:  # noqa: BLE001
            return []

    def _get_known_clients(self) -> dict[str, str]:
        """Return dict of client_mac -> display name / hostname."""
        clients: dict[str, str] = {}
        from .clients.base import RouterClient

        try:
            entry_data = self.hass.data.get(DOMAIN, {}).get(
                self.config_entry.entry_id, {}
            )
            coordinator = entry_data.get("coordinator")
            if coordinator and coordinator.router_clients:
                for mac, client in coordinator.router_clients.items():
                    norm_mac = RouterClient.normalize_mac(mac)
                    name_part = client.hostname or client.ip or "Client"
                    clients[norm_mac] = f"{name_part} ({norm_mac})"
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Could not read client list: %s", exc)

        person_tags = self.config_entry.options.get(CONF_PERSON_TAGS, {})
        for mac, data in person_tags.items():
            norm_mac = RouterClient.normalize_mac(mac)
            if norm_mac not in clients:
                name = data.get("person_name") if isinstance(data, dict) else str(data)
                clients[norm_mac] = f"{name or 'Tag'} ({norm_mac})"

        return clients


