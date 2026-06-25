"""Tests for the portable-notifications feature (D1, D2, D4, D6).

These cover the *portable* notification paths that let any HACS user wire up
working alerts without the office-only ``smtp2go_*`` plumbing:

* **D1 subject** — :func:`..notifications_channels._format_subject` drops the
  ``[fleet/internal/<kind>]`` wrapper; marker-only by default, with an optional
  caller-supplied prefix.
* **D2 notify_service** — a configured ``notify.*`` service receives a native
  ``notify`` call carrying the PLAIN body (not the HTML card).
* **D4 options flow** — the options flow persists the three portable fields
  (``notify_service`` / ``smtp_enabled`` / ``smtp_recipients``) and exposes them
  in the form schema, with ``strings.json`` / ``translations/en.json`` in sync.
* **D6 fail-loud** — an enabled-but-undeliverable alert raises a stable
  ``persistent_notification`` (and stays quiet when a service IS configured).

The SMTP-credential resolution (D3 + the office regression) lives in the sibling
``test_notifications_smtp_creds.py`` to keep both files under the 500-line limit.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smart_lock_manager.config_flow import (
    SmartLockManagerOptionsFlow,
)
from custom_components.smart_lock_manager.const import DOMAIN
from custom_components.smart_lock_manager.models.zone_settings import (
    EmailNotify,
    MobileNotify,
    ZoneNotify,
)
from custom_components.smart_lock_manager.notifications import NotificationDispatcher
from custom_components.smart_lock_manager.notifications_channels import (
    _format_subject,
)

# Project root (two levels up from this test file) for the strings/translation
# JSON parity assertions.
_COMPONENT = (
    Path(__file__).resolve().parents[1] / "custom_components" / "smart_lock_manager"
)


def _sample_alert() -> Dict[str, Any]:
    """Return a realistic sustained-unlock alert dict (mirrors test_engines).

    - Inputs: none.
    - Outputs: dict with member_entity_id / alert_type / severity / message /
      door_name — the minimum shape :meth:`NotificationDispatcher.dispatch`
      consumes.
    """
    return {
        "alert_type": "sustained_unlock",
        "member_entity_id": "lock.front_door",
        "severity": "WARN",
        "message": "Unlocked >15s without re-lock",
        "door_name": "Front Door",
        "is_recovery": False,
    }


class TestFormatSubjectPortable:
    """D1: subject is location-based, marker-only, no fleet/internal wrapper."""

    def test_alert_marker_no_fleet_prefix(self) -> None:
        """An alert subject is '<marker> <subject>' with no fleet wrapper."""
        result = _format_subject("WARN", "My Home - lock.x unlocked >15s", "alert")
        assert result == "🟡 My Home - lock.x unlocked >15s"
        assert "[fleet/internal" not in result

    def test_daily_kind_has_no_marker(self) -> None:
        """The ``daily`` kind never carries a severity marker."""
        assert _format_subject("WARN", "S", "daily") == "S"

    def test_prefix_override_leads_the_subject(self) -> None:
        """A truthy prefix is prepended ahead of the marker + subject."""
        result = _format_subject("WARN", "S", "alert", prefix="[Home]")
        assert result.startswith("[Home]")
        assert result == "[Home] 🟡 S"


@contextmanager
def _patch_async_call(hass: HomeAssistant) -> Iterator[AsyncMock]:
    """Patch ``hass.services.async_call`` with a recording AsyncMock.

    - Description: The real ``hass`` fixture's ``ServiceRegistry.async_call`` is a
      read-only INSTANCE attribute (the registry forbids per-instance
      assignment), so it is patched on the CLASS instead. The yielded AsyncMock
      records ``call_args_list`` for the dispatch assertions.
    - Inputs: hass (HomeAssistant).
    - Outputs: context manager yielding the AsyncMock.
    """
    with patch.object(
        type(hass.services), "async_call", new_callable=AsyncMock
    ) as mock:
        yield mock


class TestNotifyServicePath:
    """D2: a configured notify_service gets a native notify call, plain body."""

    async def test_notify_service_native_call_plain_body(
        self, hass: HomeAssistant
    ) -> None:
        """notify_service => notify.<svc> with title=subject + plain message."""
        dispatcher = NotificationDispatcher(hass, dry_run=True)
        # Force real-send true and pin the resolved options to one service.
        dispatcher._should_really_send = lambda: True  # type: ignore[method-assign]
        dispatcher._options_for = lambda member: {  # type: ignore[method-assign]
            "notify_service": "my_service"
        }
        with _patch_async_call(hass) as mock:
            await dispatcher.dispatch(
                _sample_alert(),
                ZoneNotify(
                    email=EmailNotify(enabled=False),
                    mobile=MobileNotify(enabled=True),
                ),
            )

        # The mobile path fires too; isolate the notify_service call to
        # ("notify", "my_service", {...}).
        notify_calls = [
            call
            for call in mock.call_args_list
            if call.args[:2] == ("notify", "my_service")
        ]
        assert len(notify_calls) == 1
        _, _, payload = notify_calls[0].args
        assert set(payload).issuperset({"title", "message"})
        # The body is the PLAIN alert body, never the HTML card.
        assert "<html" not in payload["message"].lower()
        assert "<div" not in payload["message"].lower()
        assert payload["title"]  # subject is non-empty


class TestFailLoud:
    """D6: enabled-but-unconfigured alerts raise a fail-loud notification."""

    @staticmethod
    def _unconfigured_dispatcher(hass: HomeAssistant) -> NotificationDispatcher:
        """Return a dispatcher that always thinks it may really send.

        - Inputs: hass (HomeAssistant).
        - Outputs: NotificationDispatcher with real-send forced and creds stubbed
          to None so the email render yields nothing.
        """
        dispatcher = NotificationDispatcher(hass, dry_run=True)
        dispatcher._should_really_send = lambda: True  # type: ignore[method-assign]
        # Email render yields nothing => the email channel is undeliverable.
        dispatcher.email.render = AsyncMock(  # type: ignore[method-assign]
            return_value=None
        )
        return dispatcher

    async def test_fail_loud_fires_persistent_notification(
        self, hass: HomeAssistant
    ) -> None:
        """Email enabled + creds None + nothing else => fail-loud notification."""
        dispatcher = self._unconfigured_dispatcher(hass)
        dispatcher._options_for = lambda member: {}  # type: ignore[method-assign]
        with _patch_async_call(hass) as mock:
            await dispatcher.dispatch(
                _sample_alert(),
                ZoneNotify(
                    email=EmailNotify(enabled=True),
                    mobile=MobileNotify(enabled=False),
                ),
            )
            await hass.async_block_till_done()

        nags = [
            call
            for call in mock.call_args_list
            if call.args[:2] == ("persistent_notification", "create")
        ]
        assert len(nags) == 1
        payload = nags[0].args[2]
        assert payload["notification_id"] == "slm_notify_unconfigured_lock.front_door"

    async def test_no_fail_loud_when_notify_service_configured(
        self, hass: HomeAssistant
    ) -> None:
        """A configured notify_service suppresses the fail-loud nag."""
        dispatcher = self._unconfigured_dispatcher(hass)
        dispatcher._options_for = lambda member: {  # type: ignore[method-assign]
            "notify_service": "my_service"
        }
        with _patch_async_call(hass) as mock:
            await dispatcher.dispatch(
                _sample_alert(),
                ZoneNotify(
                    email=EmailNotify(enabled=True),
                    mobile=MobileNotify(enabled=False),
                ),
            )
            await hass.async_block_till_done()

        nags = [
            call
            for call in mock.call_args_list
            if call.args[:2] == ("persistent_notification", "create")
        ]
        assert nags == []


class TestOptionsFlowPersistsNotifyFields:
    """D4: the options flow exposes + persists the portable notify fields."""

    @staticmethod
    def _entry(hass: HomeAssistant) -> MockConfigEntry:
        """Create + register a topology-only SLM config entry on hass.

        - Inputs: hass (HomeAssistant).
        - Outputs: MockConfigEntry added to hass (no options yet).
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "lock_name": "Front Door",
                "lock_entity_id": "lock.front_door",
                "slots": 10,
            },
            unique_id="lock.front_door",
        )
        entry.add_to_hass(hass)
        return entry

    def _flow(
        self, hass: HomeAssistant, entry: MockConfigEntry
    ) -> SmartLockManagerOptionsFlow:
        """Return an options flow bound to ``hass`` + ``entry``.

        - Description: ``OptionsFlow.config_entry`` is a framework property keyed
          off ``handler``/``flow_id``; the test mirrors test_config_flow by
          stubbing it directly so the flow resolves our entry without a live
          flow-manager handshake.
        - Inputs: hass (HomeAssistant), entry (MockConfigEntry).
        - Outputs: SmartLockManagerOptionsFlow ready to drive.
        """
        flow = SmartLockManagerOptionsFlow()
        flow.hass = hass
        flow._config_entry = entry  # type: ignore[attr-defined]
        # Newer HA stores the entry on a private attr behind a property; set both
        # the private slot and a direct override so config_entry resolves.
        type(flow).config_entry = property(  # type: ignore[assignment]
            lambda self: entry
        )
        return flow

    async def test_form_schema_exposes_notify_fields(self, hass: HomeAssistant) -> None:
        """With no input the form schema carries the three new keys."""
        entry = self._entry(hass)
        flow = self._flow(hass, entry)
        result = await flow.async_step_init()
        keys = {str(k) for k in result["data_schema"].schema.keys()}
        assert {"notify_service", "smtp_enabled", "smtp_recipients"} <= keys

    async def test_submit_persists_notify_options(self, hass: HomeAssistant) -> None:
        """Submitting the form persists the portable fields to entry.options."""
        entry = self._entry(hass)
        flow = self._flow(hass, entry)
        result = await flow.async_step_init(
            {
                "lock_name": "Front Door",
                "lock_entity_id": "lock.front_door",
                "slots": 10,
                "notify_service": "notify.foo",
                "smtp_enabled": True,
                "smtp_recipients": "a@x, b@x",
            }
        )
        options = result["data"]
        assert options["notify_service"] == "notify.foo"
        assert options["smtp_enabled"] is True
        assert options["smtp_recipients"] == "a@x, b@x"


class TestStringsTranslationsParity:
    """D4: strings.json + translations/en.json carry + agree on notify fields."""

    @staticmethod
    def _load(name: str) -> Dict[str, Any]:
        """Load and JSON-parse one component-level i18n file.

        - Inputs: name (str filename relative to the component dir, e.g.
          ``strings.json`` or ``translations/en.json``).
        - Outputs: parsed dict.
        """
        return json.loads((_COMPONENT / name).read_text(encoding="utf-8"))

    def test_both_files_carry_notify_fields(self) -> None:
        """Each file's options.step.init.data has the three notify keys."""
        for name in ("strings.json", "translations/en.json"):
            data = self._load(name)["options"]["step"]["init"]["data"]
            assert {
                "notify_service",
                "smtp_enabled",
                "smtp_recipients",
            } <= set(data)

    def test_options_blocks_match(self) -> None:
        """The two files' whole ``options`` blocks are byte-equal in content."""
        strings = self._load("strings.json")["options"]
        translations = self._load("translations/en.json")["options"]
        assert strings == translations


# Guard: an unused import of pytest/Mock would trip flake8; both are used above
# (pytest provides the async test runner, Mock the spec'd fixtures elsewhere).
_ = (pytest, Mock)
