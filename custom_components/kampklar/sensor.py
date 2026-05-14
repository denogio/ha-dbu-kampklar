"""Sensor-entiteter for Kampklar."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import KampklarCoordinator, KampklarData
from .parsers import Child, TeamActivity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KampklarCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        RecentMessagesSensor(coordinator, entry),
    ]
    for child in coordinator.data.children:
        entities.extend(
            [
                NextActivitySensor(coordinator, entry, child),
                UpcomingActivitiesSensor(coordinator, entry, child),
                PendingSignupsSensor(coordinator, entry, child),
            ]
        )
    async_add_entities(entities)


def _activity_start(activity: TeamActivity) -> datetime | None:
    """Kombinér aktivitetens dato + start fra time_range til en tz-aware datetime."""
    if activity.date is None or not activity.time_range:
        return None
    start = activity.time_range.split("-", 1)[0].strip()
    try:
        h, m = start.split(":")
        return datetime.combine(
            activity.date, time(int(h), int(m)), tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
    except (ValueError, IndexError):
        return None


def _slug(child: Child) -> str:
    return f"{child.person_id}_{child.team_id}"


def _child_device(entry_id: str, child: Child) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{_slug(child)}")},
        # Kort navn — slugges ind i entity_id. Fx "Kampklar Josva" → kampklar_josva_*
        name=f"Kampklar {child.short_name}",
        manufacturer="DBU",
        model=child.team_name or "mit.dbu.dk",
    )


def _account_device(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Kampklar",  # → sensor.kampklar_beskeder
        manufacturer="DBU",
        model="mit.dbu.dk",
    )


class _BaseKampklarSensor(CoordinatorEntity[KampklarCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: KampklarCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def data(self) -> KampklarData:
        return self.coordinator.data


class RecentMessagesSensor(_BaseKampklarSensor):
    """Antal beskeder i indbakken; attributter med de seneste 5.

    Bemærk: mit.dbu.dk eksponerer ikke "læst"-status i indbakkelisten — alle
    rækker bærer "Ny besked"-tag uanset. Derfor viser vi alle som seneste
    beskeder i stedet for ulæste.
    """

    _attr_translation_key = "recent_messages"
    _attr_name = "Beskeder"
    _attr_icon = "mdi:email"
    _attr_native_unit_of_measurement = "beskeder"

    def __init__(self, coordinator: KampklarCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_recent_messages"
        self._attr_device_info = _account_device(entry)

    def _sorted(self) -> list:
        return sorted(
            self.data.inbox,
            key=lambda m: m.received or datetime.min,
            reverse=True,
        )

    @property
    def native_value(self) -> int:
        return len(self.data.inbox)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bodies = self.data.message_bodies
        return {
            "messages": [
                {
                    "id": m.message_id,
                    "subject": m.subject,
                    "sender": m.sender,
                    "received": m.received.isoformat() if m.received else None,
                    "preview": m.preview,
                    "body": bodies.get(m.message_id) or m.preview,
                    "category": m.category,
                    "url": f"https://mit.dbu.dk/Message/MessageDetails.aspx?id={m.message_id}",
                }
                for m in self._sorted()[:10]
            ],
            "total": len(self.data.inbox),
        }


class _ChildSensorBase(_BaseKampklarSensor):
    def __init__(
        self,
        coordinator: KampklarCoordinator,
        entry: ConfigEntry,
        child: Child,
    ) -> None:
        super().__init__(coordinator, entry)
        self._child_key = child.key
        self._attr_device_info = _child_device(entry.entry_id, child)

    @property
    def _activities(self) -> list[TeamActivity]:
        return self.data.activities_by_child.get(self._child_key, [])

    @property
    def _child(self) -> Child | None:
        for c in self.data.children:
            if c.key == self._child_key:
                return c
        return None


class NextActivitySensor(_ChildSensorBase):
    _attr_translation_key = "next_activity"
    _attr_name = "Næste aktivitet"
    _attr_icon = "mdi:soccer"
    _attr_device_class = "timestamp"

    def __init__(
        self,
        coordinator: KampklarCoordinator,
        entry: ConfigEntry,
        child: Child,
    ) -> None:
        super().__init__(coordinator, entry, child)
        self._attr_unique_id = f"{entry.entry_id}_{child.key}_next_activity"

    @property
    def native_value(self) -> datetime | None:
        now = dt_util.now()
        upcoming = sorted(
            (a for a in self._activities if (dt := _activity_start(a)) and dt >= now),
            key=lambda a: _activity_start(a) or dt_util.utc_from_timestamp(0),
        )
        return _activity_start(upcoming[0]) if upcoming else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.now()
        upcoming = sorted(
            (a for a in self._activities if (dt := _activity_start(a)) and dt >= now),
            key=lambda a: _activity_start(a) or dt_util.utc_from_timestamp(0),
        )
        if not upcoming:
            return {"team": self._child.team_name if self._child else None}
        a = upcoming[0]
        return {
            "title": a.title,
            "type": a.activity_type,
            "location": a.location,
            "signup_status": a.signup_status,
            "team": self._child.team_name if self._child else None,
            "activity_id": a.activity_id,
        }


class UpcomingActivitiesSensor(_ChildSensorBase):
    """Alle kommende aktiviteter som liste — antal i state, detaljer i attributter."""

    _attr_translation_key = "upcoming_activities"
    _attr_name = "Kommende aktiviteter"
    _attr_icon = "mdi:calendar-clock"
    _attr_native_unit_of_measurement = "aktiviteter"

    def __init__(
        self,
        coordinator: KampklarCoordinator,
        entry: ConfigEntry,
        child: Child,
    ) -> None:
        super().__init__(coordinator, entry, child)
        self._attr_unique_id = f"{entry.entry_id}_{child.key}_upcoming_activities"

    def _upcoming(self) -> list[TeamActivity]:
        today = date.today()
        return sorted(
            (a for a in self._activities if a.date and a.date >= today),
            key=lambda a: (a.date, a.time_range or ""),
        )

    @property
    def native_value(self) -> int:
        return len(self._upcoming())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "activities": [
                {
                    "id": a.activity_id,
                    "title": a.title,
                    "type": a.activity_type,
                    "date": a.date.isoformat() if a.date else None,
                    "weekday": a.weekday,
                    "time": a.time_range,
                    "location": a.location,
                    "signup_status": a.signup_status,
                    "signup_locked": a.signup_locked,
                }
                for a in self._upcoming()
            ],
            "team": self._child.team_name if self._child else None,
        }


class PendingSignupsSensor(_ChildSensorBase):
    """Antal kommende aktiviteter som mangler tilmelding/afmelding."""

    _attr_translation_key = "pending_signups"
    _attr_name = "Mangler tilmelding"
    _attr_icon = "mdi:account-question"
    _attr_native_unit_of_measurement = "aktiviteter"

    def __init__(
        self,
        coordinator: KampklarCoordinator,
        entry: ConfigEntry,
        child: Child,
    ) -> None:
        super().__init__(coordinator, entry, child)
        self._attr_unique_id = f"{entry.entry_id}_{child.key}_pending_signups"

    def _pending(self) -> list[TeamActivity]:
        today = date.today()
        return [
            a
            for a in self._activities
            if a.date
            and a.date >= today
            and not a.signup_locked
            and (a.signup_status or "").strip().lower() in ("", "ikke svaret")
        ]

    @property
    def native_value(self) -> int:
        return len(self._pending())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "activities": [
                {
                    "id": a.activity_id,
                    "title": a.title,
                    "date": a.date.isoformat() if a.date else None,
                    "time": a.time_range,
                    "location": a.location,
                }
                for a in self._pending()
            ]
        }
