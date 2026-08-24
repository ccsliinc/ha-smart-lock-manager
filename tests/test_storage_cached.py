"""Behaviour-preservation tests for the shared CachedStore storage helper.

:mod:`custom_components.smart_lock_manager.storage._cached_store` extracted the
mechanics (Store construction, the load/save I/O guards, the baseline-key check
and the cache lifecycle) that :mod:`.muted`, :mod:`.snooze` and
:mod:`.global_settings` each used to re-implement. That extraction must be
INVISIBLE from the outside, so these tests pin the observable contract rather
than the helper's internals:

1. ROUND TRIP -- for all three refactored stores, what ``save_*`` writes is what
   ``load_*`` returns, byte-for-byte through a real (in-memory) Store, and the
   synchronous cache accessors agree with it.
2. DEGRADATION -- a missing store file yields the documented empty/default
   shape, and a store whose I/O RAISES is logged and swallowed: ``load_*``
   returns the shaped default and ``save_*`` returns None. Neither raises.
3. STORAGE IDENTITY -- key and version are unchanged, so existing ``.storage``
   files keep loading.

SECURITY: no PIN material is involved -- these blobs hold alert-type ids, epoch
deadlines and cadence integers only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.smart_lock_manager.storage import global_settings as gs_mod
from custom_components.smart_lock_manager.storage import muted as muted_mod
from custom_components.smart_lock_manager.storage import snooze as snooze_mod


class FakeStore:
    """An in-memory stand-in for HA's ``Store`` that can be made to fail.

    - Description: Round-trips whatever ``async_save`` was handed. Set
      ``raise_on`` to ``"load"`` / ``"save"`` to simulate a storage I/O fault,
      or leave ``data`` as None to simulate an absent file.
    - Inputs: data (the initial persisted value), raise_on (str|None).
    - Outputs: n/a.
    """

    def __init__(
        self, data: Optional[Dict[str, Any]] = None, raise_on: Optional[str] = None
    ) -> None:
        """Build a fake store. See the class docstring for the parameters."""
        self.data = data
        self.raise_on = raise_on
        self.saved: Optional[Dict[str, Any]] = None

    async def async_load(self) -> Optional[Dict[str, Any]]:
        """Return the persisted value, or raise when configured to fail."""
        if self.raise_on == "load":
            raise OSError("simulated storage read failure")
        return self.data

    async def async_save(self, data: Dict[str, Any]) -> None:
        """Persist a value, or raise when configured to fail."""
        if self.raise_on == "save":
            raise OSError("simulated storage write failure")
        self.saved = data
        self.data = data


def _patch_store(module: Any, store: FakeStore) -> Any:
    """Patch a storage module's ``Store`` symbol to yield ``store``.

    - Description: The modules build their Store through a module-local
      ``_store`` factory, so patching the module's ``Store`` name is what
      redirects them -- and proves the refactor did not move that construction
      out of the owning module.
    - Inputs: module (the storage module), store (FakeStore).
    - Outputs: an unittest.mock patcher context manager.
    """
    return patch.object(module, "Store", return_value=store)


@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear all three module caches before AND after each test."""
    for module in (muted_mod, snooze_mod, gs_mod):
        module._CACHE.clear()
    yield
    for module in (muted_mod, snooze_mod, gs_mod):
        module._CACHE.clear()


class TestStorageIdentityUnchanged:
    """The refactor must not touch any on-disk key, version or shape."""

    def test_keys_and_versions_are_unchanged(self) -> None:
        """Keys/versions are pinned literals so a rename cannot slip through."""
        assert muted_mod.MUTED_STORAGE_KEY == "smart_lock_manager_muted"
        assert snooze_mod.SNOOZE_STORAGE_KEY == "smart_lock_manager_snooze"
        assert (
            gs_mod.GLOBAL_SETTINGS_STORAGE_KEY == "smart_lock_manager_global_settings"
        )
        assert muted_mod.STORAGE_VERSION == 1
        assert snooze_mod.STORAGE_VERSION == 1
        assert gs_mod.STORAGE_VERSION == 1

    def test_module_cache_is_the_store_cache(self) -> None:
        """``_CACHE`` is the SAME object the CachedStore mutates, not a copy.

        This is the seam existing tests use (``module._CACHE.clear()``); if the
        helper ever rebinds its dict, that seam silently stops resetting state.
        """
        assert muted_mod._CACHE is muted_mod._STORE.cache
        assert snooze_mod._CACHE is snooze_mod._STORE.cache
        assert gs_mod._CACHE is gs_mod._STORE.cache


class TestRoundTrip:
    """save_* then load_* returns the same data, through a real Store call."""

    async def test_muted_round_trip(self, hass: HomeAssistant) -> None:
        """A muted blob survives save -> load and primes the sync accessors."""
        store = FakeStore()
        blob = {"muted": {"lock.front": ["jam", "low_battery"], "lock.back": ["all"]}}
        with _patch_store(muted_mod, store):
            await muted_mod.save_muted(hass, blob)
            # Persisted form is sorted lists, exactly as before the refactor.
            assert store.saved == {
                "muted": {
                    "lock.front": ["jam", "low_battery"],
                    "lock.back": ["all"],
                }
            }
            muted_mod._CACHE.clear()
            loaded = await muted_mod.load_muted(hass)
        assert loaded == store.saved
        assert muted_mod.get_cached_muted() == loaded
        assert muted_mod.is_muted("lock.front", "jam") is True
        assert muted_mod.is_muted("lock.back", "anything") is True
        assert muted_mod.is_muted("lock.front", "offline") is False

    async def test_snooze_round_trip(self, hass: HomeAssistant) -> None:
        """A snooze blob survives save -> load and primes the sync accessors."""
        store = FakeStore()
        blob = {"global_until": 1000.0, "zones": {"zone_a": 2000.0}}
        with _patch_store(snooze_mod, store):
            await snooze_mod.save_snooze(hass, blob)
            assert store.saved == blob
            snooze_mod._CACHE.clear()
            loaded = await snooze_mod.load_snooze(hass)
        assert loaded == blob
        assert snooze_mod.get_cached_snooze() == blob
        assert snooze_mod.global_snooze_active(now_epoch=999.0) is True
        assert snooze_mod.global_snooze_active(now_epoch=1001.0) is False
        assert snooze_mod.zone_snooze_active("zone_a", now_epoch=1999.0) is True

    async def test_global_settings_round_trip(self, hass: HomeAssistant) -> None:
        """A settings blob survives save -> load and primes the sync accessor."""
        store = FakeStore()
        with _patch_store(gs_mod, store):
            await gs_mod.save_global_settings(hass, {"health_sweep_minutes": 30})
            gs_mod._CACHE.clear()
            loaded = await gs_mod.load_global_settings(hass)
        assert loaded["health_sweep_minutes"] == 30
        # The untouched keys persisted at their defaults, not as absent keys.
        assert (
            loaded["outside_hours_sweep_minutes"]
            == gs_mod.DEFAULT_OUTSIDE_HOURS_SWEEP_MINUTES
        )
        assert loaded["nag_interval_minutes"] == gs_mod.DEFAULT_NAG_INTERVAL_MINUTES
        assert loaded == store.saved
        assert gs_mod.get_cached_global_settings() == loaded

    async def test_muted_save_merges_onto_the_cache(self, hass: HomeAssistant) -> None:
        """A PARTIAL save merges onto cached members instead of replacing them."""
        store = FakeStore()
        with _patch_store(muted_mod, store):
            await muted_mod.save_muted(hass, {"muted": {"lock.a": ["jam"]}})
            await muted_mod.save_muted(hass, {"muted": {"lock.b": ["offline"]}})
        assert store.saved == {"muted": {"lock.a": ["jam"], "lock.b": ["offline"]}}


class TestDegradesWithoutRaising:
    """A missing OR broken store file degrades exactly as it did before."""

    async def test_missing_files_yield_documented_defaults(
        self, hass: HomeAssistant
    ) -> None:
        """An absent store (async_load -> None) gives the empty/default shape."""
        with _patch_store(muted_mod, FakeStore(data=None)):
            assert await muted_mod.load_muted(hass) == {"muted": {}}
        with _patch_store(snooze_mod, FakeStore(data=None)):
            assert await snooze_mod.load_snooze(hass) == {
                "global_until": None,
                "zones": {},
            }
        with _patch_store(gs_mod, FakeStore(data=None)):
            assert await gs_mod.load_global_settings(hass) == gs_mod._shape(None)

    @pytest.mark.parametrize(
        "corrupt",
        ["not a dict", 42, [], {"muted": "not a dict"}],
    )
    async def test_corrupt_payloads_collapse_to_the_empty_shape(
        self, hass: HomeAssistant, corrupt: Any
    ) -> None:
        """Garbage on disk is shaped away rather than propagated or raised."""
        with _patch_store(muted_mod, FakeStore(data=corrupt)):
            assert await muted_mod.load_muted(hass) == {"muted": {}}
        with _patch_store(snooze_mod, FakeStore(data=corrupt)):
            assert await snooze_mod.load_snooze(hass) == {
                "global_until": None,
                "zones": {},
            }
        with _patch_store(gs_mod, FakeStore(data=corrupt)):
            assert await gs_mod.load_global_settings(hass) == gs_mod._shape(None)

    async def test_load_io_failure_is_swallowed_and_returns_defaults(
        self, hass: HomeAssistant
    ) -> None:
        """A raising async_load is logged, not propagated (the I/O guard)."""
        with _patch_store(muted_mod, FakeStore(raise_on="load")):
            assert await muted_mod.load_muted(hass) == {"muted": {}}
        with _patch_store(snooze_mod, FakeStore(raise_on="load")):
            assert await snooze_mod.load_snooze(hass) == {
                "global_until": None,
                "zones": {},
            }
        with _patch_store(gs_mod, FakeStore(raise_on="load")):
            assert await gs_mod.load_global_settings(hass) == gs_mod._shape(None)

    async def test_load_failure_leaves_muted_and_snooze_caches_intact(
        self, hass: HomeAssistant
    ) -> None:
        """A failed load must not wipe cached mute/snooze state (pre-refactor).

        muted/snooze returned the shaped default WITHOUT priming; global
        settings DID prime. Both behaviours are preserved deliberately.
        """
        muted_mod.set_mute("lock.front", "jam")
        snooze_mod.set_global_snooze(5000.0)
        with _patch_store(muted_mod, FakeStore(raise_on="load")):
            await muted_mod.load_muted(hass)
        with _patch_store(snooze_mod, FakeStore(raise_on="load")):
            await snooze_mod.load_snooze(hass)
        assert muted_mod.is_muted("lock.front", "jam") is True
        assert snooze_mod.get_cached_snooze()["global_until"] == 5000.0

        # global_settings, by contrast, primes its defaults on a failed load.
        with _patch_store(gs_mod, FakeStore(raise_on="load")):
            await gs_mod.load_global_settings(hass)
        assert gs_mod._CACHE == gs_mod._shape(None)

    async def test_save_io_failure_is_swallowed(self, hass: HomeAssistant) -> None:
        """A raising async_save is logged, not propagated, and still caches."""
        with _patch_store(muted_mod, FakeStore(raise_on="save")):
            assert (
                await muted_mod.save_muted(hass, {"muted": {"lock.a": ["jam"]}}) is None
            )
        with _patch_store(snooze_mod, FakeStore(raise_on="save")):
            assert await snooze_mod.save_snooze(hass, {"global_until": 1.0}) is None
        with _patch_store(gs_mod, FakeStore(raise_on="save")):
            assert (
                await gs_mod.save_global_settings(hass, {"health_sweep_minutes": 5})
                is None
            )
        # The in-memory cache is still primed even though the write failed.
        assert muted_mod.is_muted("lock.a", "jam") is True
        assert snooze_mod.get_cached_snooze()["global_until"] == 1.0
        assert gs_mod.get_cached_global_settings()["health_sweep_minutes"] == 5


class TestEnsureCacheBaseline:
    """The extracted baseline check reproduces each module's original rules."""

    def test_muted_baseline_replaces_a_non_dict_value(self) -> None:
        """A corrupt cached ``muted`` value is reset to an empty dict."""
        muted_mod._CACHE["muted"] = "not a dict"
        muted_mod._STORE.ensure_cache()
        assert muted_mod._CACHE["muted"] == {}

    def test_snooze_baseline_keeps_a_legitimate_none(self) -> None:
        """``global_until`` is presence-checked only, so None survives."""
        snooze_mod._CACHE["global_until"] = None
        snooze_mod._CACHE["zones"] = "not a dict"
        snooze_mod._STORE.ensure_cache()
        assert snooze_mod._CACHE["global_until"] is None
        assert snooze_mod._CACHE["zones"] == {}
