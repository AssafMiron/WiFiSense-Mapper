"""WiFiSense Mapper — Router Client Base."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class ClientInfo:
    """Information about a single WiFi client associated with a router/AP."""

    mac: str
    """MAC address of the client (normalized, lower-case, colon-separated)."""

    ip: str | None = None
    """IP address of the client, if available."""

    hostname: str | None = None
    """Human-readable hostname, if provided by the router."""

    rssi: int | None = None
    """Received Signal Strength Indicator in dBm (negative, e.g. -65).
    Note: RSSI is inherently noisy due to multipath, reflection, and
    mesh roaming events. Treat it as an approximate proximity signal,
    not a precise measurement."""

    ap_mac: str | None = None
    """MAC address of the Access Point (or Deco node) the client is
    associated with. Critical for multi-node spatial reasoning."""

    ssid: str | None = None
    """SSID the client is connected to."""

    band: str | None = None
    """Frequency band, e.g. '2.4GHz' or '5GHz'."""

    # Spatial hints — populated by registry_helpers when area/floor is known
    area_id: str | None = None
    floor_id: str | None = None

    extra: dict[str, Any] = field(default_factory=dict)
    """Router-specific extra attributes."""


@dataclass
class APStats:
    """Statistics for a single Access Point / mesh node."""

    mac: str
    """MAC address of the AP."""

    name: str | None = None
    """Human-readable name (e.g. 'Living Room Deco')."""

    channel: int | None = None
    """WiFi channel currently in use."""

    band: str | None = None
    """Frequency band, e.g. '2.4GHz' or '5GHz'."""

    tx_rate: float | None = None
    """Transmit rate in Mbps."""

    rx_rate: float | None = None
    """Receive rate in Mbps."""

    noise_floor: int | None = None
    """Noise floor in dBm."""

    client_count: int = 0
    """Number of clients currently associated."""

    # Spatial context — set by registry helpers
    area_id: str | None = None
    floor_id: str | None = None

    extra: dict[str, Any] = field(default_factory=dict)


class RouterClient(ABC):
    """Abstract base class for router clients.

    Concrete implementations must be async-safe and must not block
    the Home Assistant event loop. All network I/O should use
    ``aiohttp.ClientSession`` provided via the constructor or created
    internally inside an executor job.

    Router integrations should be kept as thin adapters:
    - Prefer reusing existing HA integrations' entity data where possible.
    - Use this direct HTTP client only when the entity-level data is
      insufficient (e.g. raw RSSI per client not exposed by HA entities).
    """

    def __init__(self, host: str, username: str, password: str) -> None:
        self.host = host
        self.username = username
        self.password = password
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        """Return True if the client is currently authenticated/connected."""
        return self._connected

    @abstractmethod
    async def async_connect(self) -> bool:
        """Authenticate with the router. Return True on success."""

    @abstractmethod
    async def async_get_clients(self) -> list[ClientInfo]:
        """Return a list of currently connected WiFi clients.

        Each entry should include at minimum: mac, rssi, ap_mac.
        RSSI may be None if the router model doesn't expose it.
        """

    @abstractmethod
    async def async_get_ap_stats(self) -> list[APStats]:
        """Return per-AP statistics (channel, noise floor, client count, etc.)."""

    @abstractmethod
    async def async_disconnect(self) -> None:
        """Clean up sessions, tokens, and connections."""

    @staticmethod
    def normalize_mac(mac: str) -> str:
        """Normalize a MAC address to lower-case colon-separated format."""
        cleaned = mac.replace("-", ":").replace(".", ":").lower()
        # Some routers return 12 hex chars without separators
        if len(cleaned) == 12 and ":" not in cleaned:
            cleaned = ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
        return cleaned
