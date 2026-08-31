"""WiFiSense Mapper — TP-Link Deco Router Client.

Uses the ``tplinkrouterc6u`` PyPI package (TPLinkDecoClient) for
authentication and client data, which handles the AES/RSA session
auth that the Deco local web API requires.

References:
  - https://pypi.org/project/tplinkrouterc6u/
  - https://github.com/amosyuen/ha-tplink-deco (pattern reference)

Limitations:
  - Not all Deco models expose per-client RSSI via the web API;
    in that case rssi=None is returned and the coordinator falls back
    to CSI-only positioning.
  - Session tokens expire; reconnection is handled automatically.
  - Mesh roaming: a client can hop between Deco nodes between polls,
    causing apparent AP assignment changes. The coordinator smooths
    this with a short rolling window.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import APStats, ClientInfo, RouterClient

_LOGGER = logging.getLogger(__name__)


class DecoClient(RouterClient):
    """TP-Link Deco client using tplinkrouterc6u."""

    def __init__(self, host: str, username: str, password: str) -> None:
        super().__init__(host, username, password)
        self._client: Any = None  # tplinkrouterc6u.TPLinkDecoClient

    async def async_connect(self) -> bool:
        """Authenticate with the Deco router.

        The tplinkrouterc6u library is synchronous, so we run it in
        an executor to avoid blocking the HA event loop.
        """
        try:
            await asyncio.get_event_loop().run_in_executor(None, self._connect_sync)
            self._connected = True
            _LOGGER.debug("Connected to Deco at %s", self.host)
            return True
        except ImportError:
            _LOGGER.error(
                "tplinkrouterc6u package not installed. "
                "Add 'tplinkrouterc6u>=4.3.0' to requirements."
            )
            return False
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to connect to Deco at %s: %s", self.host, exc)
            self._connected = False
            return False

    def _connect_sync(self) -> None:
        """Blocking connect — runs in executor."""
        # Import is guarded to give a helpful error if package is missing.
        from tplinkrouterc6u import TPLinkDecoClient  # type: ignore[import]

        self._client = TPLinkDecoClient(
            self.host,
            self.password,
            username=self.username or "admin",
            verify_ssl=False,
        )
        self._client.authorize()

    async def async_get_clients(self) -> list[ClientInfo]:
        """Return connected WiFi clients from the Deco."""
        if (
            not self._connected or self._client is None
        ) and not await self.async_connect():
            return []
        try:
            raw = await asyncio.get_event_loop().run_in_executor(
                None, self._get_clients_sync
            )
            return raw
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Error fetching Deco clients: %s", exc)
            self._connected = False  # Force re-auth on next poll
            return []

    def _decode_string(self, val: Any) -> str:
        """Safely decode potentially base64-encoded strings from Deco API."""
        if not val:
            return ""
        val_str = str(val).strip()
        try:
            import base64

            decoded = base64.b64decode(val_str).decode("utf-8", errors="ignore").strip()
            if decoded and all(c.isprintable() or c.isspace() for c in decoded):
                return decoded
        except Exception:  # noqa: BLE001, S110
            pass
        return val_str

    def _fetch_deco_nodes(self) -> list[dict[str, Any]]:
        """Directly fetch Deco mesh nodes via admin/device?form=device_list."""
        import json

        nodes: list[dict[str, Any]] = []
        if hasattr(self._client, "request"):
            try:
                req_data = json.dumps({"operation": "read"})
                resp = self._client.request("admin/device?form=device_list", req_data, ignore_errors=True)
                if isinstance(resp, dict):
                    raw_list = resp.get("device_list", [])
                    if isinstance(raw_list, list) and raw_list:
                        nodes = raw_list
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Direct device_list request failed: %s", exc)

        if not nodes:
            cached = getattr(self._client, "devices", [])
            if isinstance(cached, list) and cached:
                nodes = [n if isinstance(n, dict) else vars(n) for n in cached]

        if not nodes and hasattr(self._client, "get_firmware"):
            try:
                self._client.get_firmware()
                cached = getattr(self._client, "devices", [])
                if isinstance(cached, list) and cached:
                    nodes = [n if isinstance(n, dict) else vars(n) for n in cached]
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("get_firmware fallback failed: %s", exc)

        return nodes

    def _extract_node_name(self, node: dict[str, Any]) -> str:
        """Extract the friendly name of a Deco mesh node, decoding base64 if needed."""
        # 1. custom_nickname
        if node.get("custom_nickname"):
            return self._decode_string(node["custom_nickname"])
        # 2. nickname (commonly base64 encoded)
        if node.get("nickname"):
            dec = self._decode_string(node["nickname"])
            if dec:
                return dec
        # 3. custom_name / name
        if node.get("custom_name"):
            return self._decode_string(node["custom_name"])
        if node.get("name"):
            dec = self._decode_string(node["name"])
            if dec:
                return dec
        # 4. device_model / model fallback
        model = node.get("device_model") or node.get("model") or "Deco"
        return str(model).strip()

    def _get_clients_sync(self) -> list[ClientInfo]:
        """Blocking client fetch — runs in executor.
        
        Queries client_list per Deco node MAC for accurate AP association and RSSI.
        Falls back to global client_list and get_status on failure.
        """
        import json

        node_name_to_mac: dict[str, str] = {}
        nodes = self._fetch_deco_nodes()

        for node in nodes:
            mac_raw = (
                node.get("mac")
                or node.get("macaddr")
                or node.get("_macaddr")
            )
            if not mac_raw:
                continue
            norm_m = self.normalize_mac(str(mac_raw))
            name = self._extract_node_name(node)
            if name:
                node_name_to_mac[name.lower()] = norm_m
            model = node.get("device_model") or node.get("model")
            if model:
                node_name_to_mac[str(model).strip().lower()] = norm_m
            node_name_to_mac[norm_m] = norm_m

        # Per-node query loop (yields exact AP association and RSSI)
        seen_clients: dict[str, ClientInfo] = {}

        if nodes and hasattr(self._client, "request"):
            for node in nodes:
                node_mac = node.get("mac") or node.get("macaddr") or node.get("_macaddr")
                if not node_mac:
                    continue
                norm_node_mac = self.normalize_mac(str(node_mac))
                try:
                    payload = json.dumps({"operation": "read", "params": {"device_mac": str(node_mac)}})
                    resp = self._client.request("admin/client?form=client_list", payload, ignore_errors=True)
                    if isinstance(resp, dict):
                        raw_clients = resp.get("client_list", [])
                        if isinstance(raw_clients, list) and raw_clients:
                            for item in raw_clients:
                                client_info = self._parse_client_item(item, default_ap_mac=norm_node_mac, node_name_to_mac=node_name_to_mac)
                                if client_info and client_info.mac:
                                    seen_clients[client_info.mac] = client_info
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("Per-node client_list failed for node %s: %s", node_mac, exc)

        # If per-node queries returned no clients, fallback to global client_list
        if not seen_clients and hasattr(self._client, "request"):
            try:
                payload = json.dumps({"operation": "read", "params": {"device_mac": "default"}})
                resp = self._client.request("admin/client?form=client_list", payload, ignore_errors=True)
                if isinstance(resp, dict):
                    raw_clients = resp.get("client_list", [])
                    if isinstance(raw_clients, list) and raw_clients:
                        for item in raw_clients:
                            client_info = self._parse_client_item(item, default_ap_mac=None, node_name_to_mac=node_name_to_mac)
                            if client_info and client_info.mac:
                                seen_clients[client_info.mac] = client_info
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Global client_list request failed: %s", exc)

        # Secondary fallback: try get_status() if direct request yielded nothing
        if not seen_clients and hasattr(self._client, "get_status"):
            try:
                status = self._client.get_status()
                raw_devices = getattr(status, "devices", None) or getattr(status, "clients", []) or []
                for dev in raw_devices:
                    client_info = self._parse_client_item(dev, default_ap_mac=None, node_name_to_mac=node_name_to_mac)
                    if client_info and client_info.mac:
                        seen_clients[client_info.mac] = client_info
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Deco get_status fallback failed: %s", exc)

        return list(seen_clients.values())

    def _parse_client_item(
        self,
        item: dict[str, Any] | Any,
        default_ap_mac: str | None,
        node_name_to_mac: dict[str, str],
    ) -> ClientInfo | None:
        """Parse raw Deco client JSON dict or object into ClientInfo."""
        data: dict[str, Any] = item if isinstance(item, dict) else vars(item)

        # Check online status if present
        if "online" in data and not data["online"]:
            return None

        mac_raw = (
            data.get("mac")
            or data.get("macaddr")
            or data.get("_macaddr")
            or getattr(item, "macaddr", None)
            or getattr(item, "mac", None)
        )
        if not mac_raw:
            return None

        mac = self.normalize_mac(str(mac_raw))
        ip = (
            data.get("ip")
            or data.get("ipaddr")
            or data.get("_ipaddr")
            or getattr(item, "ipaddr", None)
            or getattr(item, "ip", None)
        )
        raw_name = (
            data.get("name")
            or data.get("hostname")
            or getattr(item, "hostname", None)
            or getattr(item, "name", None)
            or ""
        )
        hostname = self._decode_string(raw_name) if raw_name else None

        # Extract RSSI / signal level
        rssi: int | None = None
        sig_lvl = data.get("signal_level")
        if isinstance(sig_lvl, dict):
            val = sig_lvl.get("band5") or sig_lvl.get("band2_4") or sig_lvl.get("band6")
            if isinstance(val, (int, float)):
                rssi = int(val)
        elif isinstance(sig_lvl, (int, float)):
            rssi = int(sig_lvl)

        if rssi is None:
            raw_sig = (
                data.get("signal")
                if data.get("signal") is not None
                else data.get("rssi")
                if data.get("rssi") is not None
                else getattr(item, "signal", None)
                if getattr(item, "signal", None) is not None
                else getattr(item, "rssi", None)
            )
            if isinstance(raw_sig, (int, float)):
                rssi = int(raw_sig)

        # AP MAC attribution
        ap_mac = default_ap_mac
        if not ap_mac:
            ap_raw = (
                data.get("device_mac")
                or data.get("ap_mac")
                or data.get("ap_name")
                or getattr(item, "ap_name", None)
                or getattr(item, "device_mac", None)
                or getattr(item, "ap_mac", None)
            )
            if ap_raw:
                ap_str = str(ap_raw).strip()
                if ap_str.lower() in node_name_to_mac:
                    ap_mac = node_name_to_mac[ap_str.lower()]
                else:
                    ap_mac = self.normalize_mac(ap_str) or None

        # Band / SSID
        band = (
            data.get("frequency")
            or data.get("band")
            or data.get("connection_type")
            or getattr(item, "frequency", None)
            or getattr(item, "band", None)
        )
        if band:
            band_str = str(band)
            if "band5" in band_str.lower() or "5g" in band_str.lower():
                band = "5GHz"
            elif "band2" in band_str.lower() or "2g" in band_str.lower():
                band = "2.4GHz"
            elif "band6" in band_str.lower() or "6g" in band_str.lower():
                band = "6GHz"

        ssid = data.get("ssid") or getattr(item, "ssid", None)
        wire_type = data.get("wire_type")

        conn_type = data.get("type") or getattr(item, "type", None)
        if band is None and conn_type is not None:
            if hasattr(conn_type, "get_band"):
                band_str = conn_type.get_band()
                if band_str:
                    band = "2.4GHz" if band_str == "2G" else f"{band_str}Hz"
            elif hasattr(conn_type, "name"):
                band = str(conn_type.name)

        extra: dict[str, Any] = {"via_deco": True}
        down_spd = data.get("down_speed") or getattr(item, "down_speed", None)
        up_spd = data.get("up_speed") or getattr(item, "up_speed", None)
        if down_spd is not None:
            extra["down_speed"] = down_spd
        if up_spd is not None:
            extra["up_speed"] = up_spd
        if wire_type:
            extra["wire_type"] = wire_type
        if conn_type is not None:
            extra["client_type"] = (
                conn_type.value if hasattr(conn_type, "value") else str(conn_type)
            )

        return ClientInfo(
            mac=mac,
            ip=str(ip) if ip is not None else None,
            hostname=hostname,
            rssi=rssi,
            ap_mac=ap_mac,
            ssid=str(ssid) if ssid else None,
            band=str(band) if band else None,
            extra=extra,
        )

    async def async_get_ap_stats(self) -> list[APStats]:
        """Return per-Deco-node statistics."""
        if (
            not self._connected or self._client is None
        ) and not await self.async_connect():
            return []
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._get_ap_stats_sync
            )

        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Error fetching Deco AP stats: %s", exc)
            return []

    def _get_ap_stats_sync(self) -> list[APStats]:
        """Blocking AP stats fetch — runs in executor."""
        result: list[APStats] = []
        deco_nodes = self._fetch_deco_nodes()

        # Fetch clients to count per-node association
        clients = self._get_clients_sync()
        client_counts: dict[str, int] = {}
        for c in clients:
            if c.ap_mac:
                client_counts[c.ap_mac] = client_counts.get(c.ap_mac, 0) + 1

        for node in deco_nodes:
            mac_raw = node.get("mac") or node.get("macaddr") or ""
            if not mac_raw:
                continue

            norm_node_mac = self.normalize_mac(str(mac_raw))
            name = self._extract_node_name(node)

            # Count clients associated with this node
            matching_clients = client_counts.get(norm_node_mac, 0)
            if len(deco_nodes) == 1 and matching_clients == 0 and len(clients) > 0:
                matching_clients = len(clients)

            role = node.get("role")
            hw_ver = node.get("hardware_ver")
            sw_ver = node.get("software_ver")
            ip = node.get("device_ip") or node.get("ip") or node.get("ipaddr")

            result.append(
                APStats(
                    mac=norm_node_mac,
                    name=name,
                    client_count=matching_clients,
                    extra={
                        "via_deco": True,
                        "role": role,
                        "hardware_ver": hw_ver,
                        "software_ver": sw_ver,
                        "ip": ip,
                    },
                )
            )

        # Fallback if no mesh nodes found in device_list but LAN MAC exists
        if not result:
            try:
                status = self._client.get_status()
                lan_mac = getattr(status, "lan_macaddr", None) or getattr(
                    status, "wan_macaddr", None
                )
                if lan_mac:
                    result.append(
                        APStats(
                            mac=self.normalize_mac(str(lan_mac)),
                            name="Deco Master",
                            client_count=len(clients),
                            extra={"via_deco": True, "role": "master"},
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("LAN MAC fallback failed: %s", exc)

        return result

    async def async_disconnect(self) -> None:
        """No persistent session to close for Deco."""
        self._connected = False
        self._client = None
