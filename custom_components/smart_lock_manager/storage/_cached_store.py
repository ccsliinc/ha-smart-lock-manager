"""Shared mechanics for the Smart Lock Manager CACHED storage blobs.

Three modules in this package -- :mod:`.muted`, :mod:`.snooze` and
:mod:`.global_settings` -- each persist ONE process-wide blob under a dedicated
HA storage key and keep a synchronous in-memory mirror of it, because the alert
engine's notify / subscribe paths are sync callbacks that cannot await storage.
Before this helper each of the three re-implemented the same skeleton: Store
construction, a try/except-and-log guard around load and save, a "make sure the
cache has its baseline keys" step, a prime-the-cache step, and a bare
module-level ``_CACHE`` dict.

:class:`CachedStore` owns exactly those MECHANICS and nothing else. Each module
keeps its own DOMAIN logic -- its ``_shape`` validator, its merge semantics on
save, and every public accessor name it already exported. No storage key,
version or serialized shape is defined here, so a user's existing ``.storage``
files load exactly as they did before.

DELIBERATELY NOT unified here (they share the skeleton's look, not its shape):

- :mod:`.alert_storage` -- no cache and no shape function; its load is a
  one-time legacy-key MIGRATION across two different Stores.
- :mod:`.zone_storage` -- one Store PER ZONE under a dynamic key, plus a remove
  path and a ``.storage`` directory scan. Nothing process-wide to cache.
- :mod:`.lock_storage` -- never constructs a Store at all; it is handed one out
  of ``hass.data``.

SECURITY: this module never inspects blob contents. No PIN material passes
through it beyond the opaque dicts each caller shapes for itself.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

# A module's Store factory. Passed IN (rather than built here) on purpose: it
# resolves the ``Store`` symbol in the OWNING module's namespace, which is what
# the test suite patches.
StoreFactory = Callable[[HomeAssistant], Store]

# Validates + fills a raw loaded value into the module's canonical shape.
ShapeFn = Callable[[Any], Dict[str, Any]]

# Returns a fresh dict of the cache's baseline keys and their default values.
BaselineFn = Callable[[], Dict[str, Any]]

# Copies a freshly shaped blob into the live cache dict, in place.
PrimeFn = Callable[[Dict[str, Any], Dict[str, Any]], None]


def replace_prime(cache: Dict[str, Any], shaped: Dict[str, Any]) -> None:
    """Prime the cache by REPLACING its contents with the shaped blob.

    - Description: The default priming strategy. Any cache key absent from
      ``shaped`` is dropped, so the cache is an exact mirror of what was
      persisted.
    - Inputs: cache (the live cache dict, mutated in place), shaped (dict).
    - Outputs: None.
    """
    cache.clear()
    cache.update(shaped)


def merge_prime(cache: Dict[str, Any], shaped: Dict[str, Any]) -> None:
    """Prime the cache by MERGING the shaped blob onto its current contents.

    - Description: Used where the shape function already fills every key with a
      default, so clearing first would be a no-op with an extra step.
    - Inputs: cache (the live cache dict, mutated in place), shaped (dict).
    - Outputs: None.
    """
    cache.update(shaped)


class CachedStore:
    """One process-wide HA ``Store`` plus its synchronous in-memory mirror.

    - Description: Owns Store construction, the load/save I/O guards and their
      log lines, the baseline-key check, and the cache lifecycle. Callers keep
      their own shape and merge logic and call :meth:`ensure_cache`,
      :meth:`shape`, :meth:`prime` and :meth:`persist` around it.
    - Inputs: see :meth:`__init__`.
    - Outputs: n/a (instantiated once per storage module, at import time).
    - Example::

        _STORE = CachedStore(
            store_factory=_store,
            shape=_shape,
            baseline=lambda: {"muted": {}},
            logger=_LOGGER,
            load_error_message="Failed to load muted state: %s",
            save_error_message="Failed to save muted state: %s",
        )
        _CACHE = _STORE.cache
    """

    def __init__(
        self,
        *,
        store_factory: StoreFactory,
        shape: ShapeFn,
        baseline: BaselineFn,
        logger: logging.Logger,
        load_error_message: str,
        save_error_message: str,
        prime: PrimeFn = replace_prime,
        prime_on_load_error: bool = False,
    ) -> None:
        """Build a cached store.

        - Inputs:
            store_factory (callable hass -> Store) -- built by the owning
              module so its ``Store`` symbol stays the patchable one;
            shape (callable raw -> dict) -- the module's validator;
            baseline (callable -> dict) -- fresh baseline keys/defaults;
            logger (logging.Logger) -- the owning module's logger;
            load_error_message / save_error_message (str) -- ``%s`` format
              strings taking the exception;
            prime (callable cache, shaped -> None) -- cache priming strategy,
              defaults to :func:`replace_prime`;
            prime_on_load_error (bool) -- whether a FAILED load primes the
              cache with the shaped defaults (True) or leaves whatever the
              cache already held untouched (False, the default).
        - Outputs: None.
        """
        self._store_factory = store_factory
        self._shape = shape
        self._baseline = baseline
        self._logger = logger
        self._load_error_message = load_error_message
        self._save_error_message = save_error_message
        self._prime = prime
        self._prime_on_load_error = prime_on_load_error
        # Created ONCE and never rebound. Storage modules expose this exact
        # dict object as their module-level ``_CACHE``, so a test that calls
        # ``module._CACHE.clear()`` still resets this instance.
        self.cache: Dict[str, Any] = {}

    def store(self, hass: HomeAssistant) -> Store:
        """Return the HA Store backing this blob.

        - Inputs: hass (HomeAssistant).
        - Outputs: Store (built fresh by the owning module's factory).
        """
        return self._store_factory(hass)

    def shape(self, data: Any) -> Dict[str, Any]:
        """Return ``data`` run through the owning module's shape function.

        - Inputs: data (any raw / partial value).
        - Outputs: dict in the module's canonical shape.
        """
        return self._shape(data)

    def ensure_cache(self) -> None:
        """Ensure the cache holds its baseline keys before a sync mutation.

        - Description: A missing key gets its default. A key whose default is a
          dict but whose cached value is NOT a dict is reset to the default;
          non-dict defaults are presence-checked only, so a legitimately-None
          value survives.
        - Inputs: none.
        - Outputs: None (mutates :attr:`cache` in place).
        """
        for key, default in self._baseline().items():
            if key not in self.cache:
                self.cache[key] = default
            elif isinstance(default, dict) and not isinstance(self.cache[key], dict):
                self.cache[key] = default

    def prime(self, shaped: Dict[str, Any]) -> None:
        """Copy a shaped blob into the cache using this store's prime strategy.

        - Inputs: shaped (dict already run through :meth:`shape`).
        - Outputs: None (mutates :attr:`cache` in place).
        """
        self._prime(self.cache, shaped)

    async def load(self, hass: HomeAssistant) -> Dict[str, Any]:
        """Load, shape and prime -- returning the shaped defaults on I/O error.

        - Description: A storage failure is logged and degrades to
          ``shape(None)``; whether that also primes the cache is set by
          ``prime_on_load_error``.
        - Inputs: hass (HomeAssistant).
        - Outputs: dict in the module's canonical shape (never raises).
        """
        try:
            data = await self.store(hass).async_load()
        except Exception as exc:  # pragma: no cover - storage I/O guard
            self._logger.error(self._load_error_message, exc)
            shaped = self._shape(None)
            if self._prime_on_load_error:
                self.prime(shaped)
            return shaped
        shaped = self._shape(data)
        self.prime(shaped)
        return shaped

    async def persist(self, hass: HomeAssistant, shaped: Dict[str, Any]) -> None:
        """Write an already-shaped blob, logging and swallowing an I/O error.

        - Inputs: hass (HomeAssistant), shaped (dict from :meth:`shape`).
        - Outputs: None (never raises).
        """
        try:
            await self.store(hass).async_save(shaped)
        except Exception as exc:  # pragma: no cover - storage I/O guard
            self._logger.error(self._save_error_message, exc)
