# Smart Lock Manager

Manage PIN codes on Z-Wave smart locks (Kwikset, Yale, Schlage, and other locks that
expose user-code management through Z-Wave JS) from a single summary sensor per lock
instead of dozens of entities.

## What it does

- **Per-slot PIN codes** — named slots, up to 50 per lock.
- **Scheduled access** — restrict a code by allowed hours, allowed days, and start/end
  date range. Codes outside their window auto-disable and re-enable.
- **Usage limits** — cap a code at a number of uses, then auto-disable it.
- **Zones** — a zone owns the canonical code set; every member lock obeys it uniformly.
  Each lock belongs to exactly one zone (a single lock is a 1-member zone); unhomed locks
  sit in a pool until you place them.
- **Lock-health alerting (opt-in, off by default)** — detect outside-hours unlocks,
  sustained unlocks, jams, low battery, and offline locks; record them and fire recovery
  notices when they clear. Ships in a safe observe / dry-run posture.
- **Auto-lock (opt-in, off by default)** — scheduled close-of-business lockdown and
  idle-timeout auto-lock, per zone. Records "would auto-lock" intents until you enable
  real actions.
- **Mute & snooze** — sticky per-lock mute (optionally one alert type) and time-boxed
  per-zone or global snooze.
- **Custom sidebar panel** for managing slots, zones, schedules, and usage.

## Requirements

- Home Assistant 2024.8.0 or newer.
- Z-Wave JS configured and running, with a lock that supports user-code management.

## Install via HACS

1. Add this repository as a HACS **custom repository** with category **Integration**.
2. Search for **Smart Lock Manager**, download it, and restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration**, search **Smart Lock
   Manager**, pick the lock entity, name it, and set the slot count.

See the [README](README.md) for full configuration, zone usage, and the service reference.
