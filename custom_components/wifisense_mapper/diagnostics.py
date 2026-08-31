"""WiFiSense Mapper — Home Assistant Diagnostics.

Dumps coordinator state, Pillow/system health, router stats, CSI discovery,
vacuum map mappings, and recent log messages with sanitized sensitive values.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import WiFiSenseCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator: WiFiSenseCoordinator | None = entry_data.get("coordinator")

    pillow_installed = False
    try:
        import PIL

        pillow_installed = True
    except ImportError:
        pillow_installed = False

    diag: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "options": {
            k: v for k, v in entry.options.items() if "password" not in k.lower()
        },
        "system_health": {
            "pillow_installed": pillow_installed,
            "pillow_version": getattr(PIL, "__version__", None)
            if pillow_installed
            else None,
            "scanning_active": coordinator.is_scanning if coordinator else False,
        },
    }

    if coordinator:
        coverage = coordinator.get_area_coverage_summary()
        diag.update(
            {
                "router_connected": coordinator.router_client.is_connected
                if coordinator.router_client
                else False,
                "ap_count": len(coordinator.ap_stats),
                "client_count": len(coordinator.router_clients),
                "csi_node_count": len(coordinator.csi_nodes),
                "vacuum_source_count": len(coordinator.vacuum_sources),
                "floors_configured": list(coordinator.grids.keys()),
                "coverage_summary": coverage,
                "recent_logs": coordinator.get_recent_logs(max_lines=30),
            }
        )

    return diag
