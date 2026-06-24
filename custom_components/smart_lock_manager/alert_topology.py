"""Monitored-entity topology + event routing for the SLM alert engine.

Split out of :mod:`.alert_engine` purely to keep that file under the 500-line
standard — behaviour is identical to the inlined version. This mixin owns:

* :meth:`_monitored_entities` — the lock + companion (battery / jam) entity set
  the engine subscribes to, resolved via ``member_meta`` overrides first and
  auto-discovery second;
* :meth:`_battery_entity_for` — the resolved battery sensor for a member;
* :meth:`_zone_for` / :meth:`_friendly_name` — owning-zone + display-name
  lookups used by the recorder; and
* :meth:`_handle_state_event` + the companion reverse-lookups
  (:meth:`_lock_for_battery` / :meth:`_lock_for_jam_sensor`) that route a raw HA
  state-change event to the right detector.

The mixin relies on the engine / sibling detector mixins providing
``self.hass``, the ``_resolve_*`` companion resolvers and the ``_eval_*``
detector entrypoints.

SECURITY: nothing here records PIN material — it only routes entity ids.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from homeassistant.core import Event, callback

from .zone_runtime import get_zone_registry

_LOGGER = logging.getLogger(__name__)


class AlertTopologyMixin:
    """Monitored-entity resolution + state-event routing for the engine."""

    hass: Any

    # -- cross-mixin methods (provided by the engine / detector mixins) ------

    def _resolve_battery_entity(
        self, entity_id: str
    ) -> str:  # pragma: no cover - provided by detector mixin
        raise NotImplementedError

    def _resolve_jam_sensor(
        self, entity_id: str
    ) -> str:  # pragma: no cover - provided by detector mixin
        raise NotImplementedError

    def _eval_low_battery(
        self, lock_entity_id: str, raw_value: str
    ) -> None:  # pragma: no cover - provided by health mixin
        raise NotImplementedError

    def _eval_offline(
        self, entity_id: str, value: str
    ) -> None:  # pragma: no cover - provided by health mixin
        raise NotImplementedError

    def _eval_jam(
        self, entity_id: str, value: str, attributes: Dict[str, Any]
    ) -> None:  # pragma: no cover - provided by health mixin
        raise NotImplementedError

    def _eval_outside_hours(
        self, entity_id: str, value: str
    ) -> None:  # pragma: no cover - provided by detector mixin
        raise NotImplementedError

    def _eval_sustained(
        self, entity_id: str, value: str
    ) -> None:  # pragma: no cover - provided by detector mixin
        raise NotImplementedError

    # -- topology -----------------------------------------------------------

    def _monitored_entities(self) -> List[str]:
        """Return every entity the engine should watch (locks + companions).

        - Description: All zone member lock entity ids PLUS each member's
          resolved companion battery sensor AND jam binary_sensor. Resolution
          prefers the explicit ``settings.member_meta`` overrides and falls back
          to auto-discovery (see :meth:`_resolve_battery_entity` /
          :meth:`_resolve_jam_sensor`), so the state-change path drives the
          low-battery and jam detectors even for real-world Z-Wave entities whose
          ids do not match the auto-discovery convention.
        - Inputs: none (reads the zone registry).
        - Outputs: de-duplicated list of entity_id strings.
        """
        entities: List[str] = []
        for zone in get_zone_registry(self.hass).values():
            for entity_id in zone.member_lock_entity_ids:
                entities.append(entity_id)
                entities.append(self._battery_entity_for(entity_id))
                entities.append(self._resolve_jam_sensor(entity_id))
        # De-dup while preserving order.
        seen: set = set()
        result: List[str] = []
        for ent in entities:
            if ent not in seen:
                seen.add(ent)
                result.append(ent)
        return result

    def _battery_entity_for(self, lock_entity_id: str) -> str:
        """Return the resolved companion battery sensor id for a lock entity.

        - Description: Delegates to the shared
          :meth:`_resolve_battery_entity` resolver (explicit
          ``member_meta.battery_entity`` first, auto-discovery
          ``sensor.<object_id>_battery`` second).
        - Inputs: lock_entity_id (str), e.g. ``lock.front_door``.
        - Outputs: str, e.g. ``sensor.front_battery_level`` (override) or
          ``sensor.front_door_battery`` (auto-discovery fallback).
        """
        return self._resolve_battery_entity(lock_entity_id)

    def _zone_for(self, entity_id: str) -> tuple[Optional[str], Optional[str], str]:
        """Return (zone_id, zone_name, door_name) for a member entity.

        - Inputs: entity_id (str lock entity id).
        - Outputs: tuple(zone_id, zone_name, door_name); door_name falls back
          to the live HA friendly_name then the entity id.
        """
        for zone in get_zone_registry(self.hass).values():
            if zone.has_member(entity_id):
                door = self._friendly_name(entity_id)
                return zone.zone_id, zone.name, door
        return None, None, self._friendly_name(entity_id)

    def _friendly_name(self, entity_id: str) -> str:
        """Return the live HA friendly name for an entity, or the id.

        - Inputs: entity_id (str).
        - Outputs: str display name (never empty).
        """
        state = self.hass.states.get(entity_id)
        if state is not None:
            name = state.attributes.get("friendly_name")
            if name:
                return str(name)
        return entity_id

    # -- event routing ------------------------------------------------------

    @callback
    def _handle_state_event(self, event: Event) -> None:
        """Route a state-change event to the relevant detectors.

        - Description: Lock entity events drive the unlock-based detectors
          (outside-hours, sustained), jam, and offline detectors. Companion
          events (battery sensor / jam binary_sensor) are resolved back to their
          owning lock via ``member_meta``-aware reverse lookups and drive the
          low-battery / jam detectors — so a non-conventional override id still
          routes correctly.
        - Inputs: event (HA state_changed Event).
        - Outputs: None (records alerts as a side effect).
        """
        entity_id = event.data.get("entity_id", "")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        # Lock entity path.
        if entity_id.startswith("lock."):
            value = (new_state.state or "unknown").lower()
            self._eval_offline(entity_id, value)
            self._eval_jam(entity_id, value, new_state.attributes)
            self._eval_outside_hours(entity_id, value)
            self._eval_sustained(entity_id, value)
            return

        # Companion-entity path. A companion may be a configured ``member_meta``
        # override (any id) or an auto-discovered one — resolve it back to its
        # owning lock by comparing against each member's RESOLVED battery / jam
        # entity, so the state path works regardless of naming convention.
        battery_lock = self._lock_for_battery(entity_id)
        if battery_lock is not None:
            self._eval_low_battery(battery_lock, new_state.state)
            return
        jam_lock = self._lock_for_jam_sensor(entity_id)
        if jam_lock is not None:
            lock_state = self.hass.states.get(jam_lock)
            value = (lock_state.state if lock_state is not None else "unknown").lower()
            self._eval_jam(jam_lock, value, new_state.attributes)

    def _lock_for_battery(self, battery_entity_id: str) -> Optional[str]:
        """Resolve a battery sensor back to its monitored lock entity.

        - Description: Compares the event entity against each member's RESOLVED
          battery entity (``member_meta.battery_entity`` override first,
          auto-discovery second) so a non-conventional override id still routes
          to its lock.
        - Inputs: battery_entity_id (str).
        - Outputs: the lock entity id if it is a monitored member, else None.
        """
        for zone in get_zone_registry(self.hass).values():
            for member in zone.member_lock_entity_ids:
                if self._resolve_battery_entity(member) == battery_entity_id:
                    return member
        return None

    def _lock_for_jam_sensor(self, jam_entity_id: str) -> Optional[str]:
        """Resolve a jam binary_sensor back to its monitored lock entity.

        - Description: Compares the event entity against each member's RESOLVED
          jam binary_sensor (``member_meta.jam_sensor`` override first,
          auto-discovery second) so a state change on a non-conventional jam
          sensor still drives the jam detector for its lock.
        - Inputs: jam_entity_id (str).
        - Outputs: the lock entity id if it is a monitored member, else None.
        """
        for zone in get_zone_registry(self.hass).values():
            for member in zone.member_lock_entity_ids:
                if self._resolve_jam_sensor(member) == jam_entity_id:
                    return member
        return None
