"""Shared state for one HomBot config entry's historical route.

The learned-map image keeps its own PNG. This object is the only place
the route image reads a rendered route from.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .const import DOMAIN
from .route_list import HistoricalRun


ROUTE_HISTORY_KEY = "route_history"


@dataclass
class RouteHistoryState:
    """Options, selection and rendered route for one config entry."""

    runs: tuple[HistoricalRun, ...] = ()
    selected_bbl_name: str | None = None
    png: bytes | None = None
    rendered_at: datetime | None = None
    generation: int = 0
    listeners: list[Callable[[], None]] = field(default_factory=list)

    def add_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback invoked after the route PNG changes."""

        self.listeners.append(listener)

    def remove_listener(self, listener: Callable[[], None]) -> None:
        """Drop a callback. Missing callbacks are ignored."""

        try:
            self.listeners.remove(listener)
        except ValueError:
            return

    def notify(self) -> None:
        """Tell listeners that the route PNG or its timestamp changed."""

        for listener in tuple(self.listeners):
            listener()

    def clear_png(self) -> None:
        """Drop the route PNG. The learned-map image is not stored here."""

        self.png = None
        self.rendered_at = None
        self.notify()

    def publish_png(self, png: bytes, rendered_at: datetime) -> None:
        """Store one successful route render and notify the route image."""

        self.png = png
        self.rendered_at = rendered_at
        self.notify()


def get_route_history(hass: object, entry_id: str) -> RouteHistoryState:
    """Return the route state created for this config entry."""

    data = getattr(hass, "data")
    return data[DOMAIN][entry_id][ROUTE_HISTORY_KEY]
