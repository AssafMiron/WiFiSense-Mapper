"""WiFiSense Mapper — Heatmap Image Entities.

Exposes per-floor, per-layer heatmap PNGs as HA ImageEntity objects.
These can be used directly in:
  - picture-elements cards (as image overlays)
  - Zircon3D / Floorplan card background layers
  - xiaomi-vacuum-map-card compatible overlays

One entity is created per floor × layer combination:
  - signal   : WiFi signal strength (RSSI)
  - variance : RSSI variance (obstacle shadows)
  - motion   : CSI motion intensity
  - anomaly  : Spatial anomaly z-score

Images update after each coordinator refresh cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, HEATMAP_LAYERS
from .coordinator import WiFiSenseCoordinator
from .sensor import _floor_device_info, _get_floor_name

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up heatmap image entities."""
    coordinator: WiFiSenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[ImageEntity] = []

    for floor_id in coordinator.grids:
        floor_name = _get_floor_name(hass, floor_id)
        device_info = _floor_device_info(entry, floor_id, floor_name)

        for layer in HEATMAP_LAYERS:
            entities.append(
                HeatmapImageEntity(
                    coordinator, entry, floor_id, floor_name, layer, device_info
                )
            )

    async_add_entities(entities)


class HeatmapImageEntity(CoordinatorEntity[WiFiSenseCoordinator], ImageEntity):
    """Heatmap image entity for a single floor + layer combination.

    The image bytes are pulled from the coordinator's ``heatmap_images``
    dict, which is populated by the async rendering pipeline in executor.

    Compatible with picture-elements cards:
      type: picture-elements
      image: /api/image_proxy/image.wifisense_mapper_ground_floor_signal
    """

    _attr_has_entity_name = True
    _attr_content_type = "image/png"

    def __init__(
        self,
        coordinator: WiFiSenseCoordinator,
        entry: ConfigEntry,
        floor_id: str,
        floor_name: str,
        layer: str,
        device_info: DeviceInfo,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._entry = entry
        self._floor_id = floor_id
        self._floor_name = floor_name
        self._layer = layer
        self._attr_unique_id = f"{entry.entry_id}_heatmap_{floor_id}_{layer}"
        self._attr_name = f"{layer.title()} Heatmap"
        self._attr_device_info = device_info
        self._image_bytes: bytes | None = None
        self._image_last_updated: datetime = datetime.now(timezone.utc)

    @property
    def image_last_updated(self) -> datetime:
        """Return timestamp of last image update."""
        return self._image_last_updated

    async def async_image(self) -> bytes | None:
        """Return the current heatmap PNG bytes."""
        data = self.coordinator.data or {}
        images = data.get("heatmap_images", {})
        floor_images = images.get(self._floor_id, {})
        return floor_images.get(self._layer)

    def _handle_coordinator_update(self) -> None:
        """Update image timestamp when coordinator data changes."""
        data = self.coordinator.data or {}
        images = data.get("heatmap_images", {})
        floor_images = images.get(self._floor_id, {})
        new_bytes = floor_images.get(self._layer)

        if new_bytes is not None and new_bytes != self._image_bytes:
            self._image_bytes = new_bytes
            self._image_last_updated = datetime.now(timezone.utc)

        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "floor_id": self._floor_id,
            "floor_name": self._floor_name,
            "layer": self._layer,
            "last_generated": self._image_last_updated.isoformat(),
            "usage": (
                f"Use /api/image_proxy/{self.entity_id} in picture-elements, "
                "Floorplan, or Zircon3D cards."
            ),
        }
