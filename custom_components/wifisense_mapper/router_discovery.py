"""WiFiSense Mapper — Extensible Router Discovery & Adapter Engine.

Scans Home Assistant's configuration entries and entity registries to automatically
detect existing router integrations (such as TP-Link Deco and UniFi).

Architecture:
- ``DiscoveredRouter``: Normalized data model representing any detected router in HA.
- ``RouterDiscoveryProvider``: Abstract base class for platform-specific discovery logic.
- Modular providers (e.g. ``DecoDiscoveryProvider``, ``UniFiDiscoveryProvider``) allow
  effortlessly adding new router integrations (AsusWRT, Keenetic, Fritz!Box, OpenWrt, etc.)
  in the future without changing config flow or coordinator logic.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .const import ROUTER_TYPE_DECO, ROUTER_TYPE_UNIFI

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def clean_host(host_raw: str | None) -> str:
    """Clean a host string, URL, or title into a clean IP or hostname.

    Handles formats like:
      - "http://192.168.1.246" -> "192.168.1.246"
      - "https://192.168.0.1:8080/" -> "192.168.0.1"
      - "192.168.1.1" -> "192.168.1.1"
      - "deco.local" -> "deco.local"
    """
    if not host_raw:
        return ""

    raw = host_raw.strip()

    # If it contains a URL scheme (e.g. http://192.168.1.246)
    if "://" in raw:
        try:
            parsed = urlparse(raw)
            hostname = parsed.hostname or parsed.netloc.split(":")[0]
            if hostname:
                return hostname
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Failed to parse URL %s: %s", raw, exc)

    # Extract IPv4 or hostname if enclosed in extra text
    ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw)
    if ip_match:
        return ip_match.group(0)

    # Remove any trailing slash or port
    clean = re.sub(r"/.*$", "", raw)
    clean = re.sub(r":\d+$", "", clean)
    return clean.strip()


@dataclass
class DiscoveredRouter:
    """Standardized representation of a detected router in Home Assistant."""

    router_type: str
    """WiFiSense router type key (e.g., ROUTER_TYPE_DECO, ROUTER_TYPE_UNIFI)."""

    integration_domain: str
    """HA integration domain where this router was configured (e.g., 'tplink_deco', 'unifi')."""

    entry_id: str
    """Source HA ConfigEntry ID."""

    title: str
    """Human-readable display title for UI selection."""

    host: str
    """Clean IP address or hostname."""

    username: str = "admin"
    """Admin username."""

    password: str | None = None
    """Auto-extracted password if available in HA config entry data."""

    is_bridge_only: bool = False
    """True if this router uses HA entity states (like UniFi) instead of direct credentials."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Platform-specific metadata."""

    @property
    def discovery_id(self) -> str:
        """Unique discovery identifier for config flow selection keys."""
        return f"{self.router_type}_{self.integration_domain}_{self.entry_id}"


class RouterDiscoveryProvider(ABC):
    """Abstract provider for router integration discovery in Home Assistant."""

    @property
    @abstractmethod
    def router_type(self) -> str:
        """WiFiSense router type key."""

    @property
    @abstractmethod
    def target_domains(self) -> tuple[str, ...]:
        """HA integration domains to scan."""

    @abstractmethod
    def discover(self, hass: HomeAssistant) -> list[DiscoveredRouter]:
        """Scan HA for configured router entries and return discovered routers."""


class DecoDiscoveryProvider(RouterDiscoveryProvider):
    """Discovery provider for TP-Link Deco mesh systems."""

    @property
    def router_type(self) -> str:
        return ROUTER_TYPE_DECO

    @property
    def target_domains(self) -> tuple[str, ...]:
        return ("tplink_deco", "tplink")

    def discover(self, hass: HomeAssistant) -> list[DiscoveredRouter]:
        """Scan HA for configured TP-Link Deco entries."""
        discovered: list[DiscoveredRouter] = []

        for domain in self.target_domains:
            entries = hass.config_entries.async_entries(domain)
            for entry in entries:
                # Check if this is a Deco entry (by domain or title/model)
                data = entry.data or {}
                options = entry.options or {}

                # Look for host in entry data, options, or entry title
                raw_host = (
                    data.get("host")
                    or data.get("ip")
                    or data.get("router_host")
                    or options.get("host")
                    or entry.title
                )
                host = clean_host(raw_host)
                if not host:
                    continue

                username = data.get("username") or data.get("admin_username") or "admin"
                password = data.get("password") or data.get("admin_password")

                display_title = (
                    f"TP-Link Deco ({host})"
                    if host
                    else f"TP-Link Deco ({entry.title})"
                )

                discovered.append(
                    DiscoveredRouter(
                        router_type=ROUTER_TYPE_DECO,
                        integration_domain=domain,
                        entry_id=entry.entry_id,
                        title=display_title,
                        host=host,
                        username=username,
                        password=password,
                        is_bridge_only=False,
                        extra={"raw_title": entry.title},
                    )
                )
                _LOGGER.debug("Discovered Deco router at %s (entry_id=%s)", host, entry.entry_id)

        return discovered


class UniFiDiscoveryProvider(RouterDiscoveryProvider):
    """Discovery provider for UniFi Network integration."""

    @property
    def router_type(self) -> str:
        return ROUTER_TYPE_UNIFI

    @property
    def target_domains(self) -> tuple[str, ...]:
        return ("unifi",)

    def discover(self, hass: HomeAssistant) -> list[DiscoveredRouter]:
        """Scan HA for configured UniFi entries or components."""
        discovered: list[DiscoveredRouter] = []

        entries = hass.config_entries.async_entries("unifi")
        for entry in entries:
            raw_host = entry.data.get("host", "")
            host = clean_host(raw_host)
            display_title = f"UniFi ({entry.title})" if entry.title else "UniFi Network"

            discovered.append(
                DiscoveredRouter(
                    router_type=ROUTER_TYPE_UNIFI,
                    integration_domain="unifi",
                    entry_id=entry.entry_id,
                    title=display_title,
                    host=host,
                    is_bridge_only=True,
                    extra={"raw_title": entry.title},
                )
            )
            _LOGGER.debug("Discovered UniFi integration (entry_id=%s)", entry.entry_id)

        return discovered


# Registry of active discovery providers.
# Additional providers (e.g. AsusWRT, Keenetic, Fritz!Box) can be added here.
ROUTER_DISCOVERY_PROVIDERS: list[RouterDiscoveryProvider] = [
    DecoDiscoveryProvider(),
    UniFiDiscoveryProvider(),
]


def discover_all_routers(hass: HomeAssistant) -> list[DiscoveredRouter]:
    """Execute all registered discovery providers and aggregate discovered routers."""
    results: list[DiscoveredRouter] = []
    for provider in ROUTER_DISCOVERY_PROVIDERS:
        try:
            discovered = provider.discover(hass)
            results.extend(discovered)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Error running discovery provider %s: %s", provider.router_type, exc)
    return results


def find_discovered_router(
    hass: HomeAssistant, discovery_id: str
) -> DiscoveredRouter | None:
    """Find a specific discovered router by its discovery_id."""
    for router in discover_all_routers(hass):
        if router.discovery_id == discovery_id:
            return router
    return None


def get_first_discovered_router_of_type(
    hass: HomeAssistant, router_type: str
) -> DiscoveredRouter | None:
    """Get the first discovered router matching a specific router type."""
    for router in discover_all_routers(hass):
        if router.router_type == router_type:
            return router
    return None
