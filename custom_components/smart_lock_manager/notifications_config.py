"""Portable SMTP credential resolution for the SLM notification layer.

Owns the ``secrets.yaml`` reader that powers :class:`..notifications_channels.
EmailNotifier`. The reader is *generic-first*: it reads the portable
``slm_smtp_*`` keys first and falls back to the office ``smtp2go_*`` keys, so a
fresh HACS install can wire up any SMTP relay while the existing office install
(which only has ``smtp2go_*`` keys) keeps working byte-for-byte.

The SMTP host is REQUIRED and has no default. An earlier version defaulted it
to a specific vendor's relay, which meant an install supplying only credentials
silently authenticated against a third party's server: when that relay was
retired the logins failed with a bare 535 that named no host, and the alert mail
was lost for two months before anyone noticed. A missing host is now a hard,
named configuration error. Only the port defaults, to the standard 587.

SECURITY: this module only ever reads non-secret routing config plus the SMTP
login from the install's own ``secrets.yaml`` — it never logs the values.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import yaml

_LOGGER = logging.getLogger(__name__)

# Only the port has a default. There is deliberately NO default host: sending
# credentials to a host the installer did not choose is a security decision made
# on their behalf, and a wrong one the moment that host changes hands or is
# retired. See the module docstring.
DEFAULT_SMTP_PORT = 587

# Per-kind extra-recipient list kinds resolved from secrets.
_KINDS = ("alert", "daily", "info", "test")

# Logical field -> (generic key, office fallback key). Required fields only.
_REQUIRED_FIELDS = {
    "user": ("slm_smtp_user", "smtp2go_user"),
    "pass": ("slm_smtp_pass", "smtp2go_pass"),
    "from": ("slm_smtp_from", "smtp2go_from"),
    "host": ("slm_smtp_host", "smtp2go_host"),
}


def load_smtp_creds_sync(secrets_path: str) -> Optional[Dict[str, Any]]:
    """Read SMTP creds from ``secrets.yaml`` generic-first (BLOCKING file IO).

    - Description: Reads the portable ``slm_smtp_*`` keys first, falling back to
      the office ``smtp2go_*`` keys per field, so both a generic HACS install
      and the existing office install resolve correctly. ``host`` has NO
      default and is REQUIRED; only the port falls back, to
      :data:`DEFAULT_SMTP_PORT`. ``user``/``pass``/``from``/``host`` are all
      required (from EITHER prefix); ``to`` may be empty because recipients can
      also come from a zone override. MUST run in the executor, never the event
      loop. Returns None on read error or missing requireds, having logged
      exactly which keys were absent.
    - Inputs: secrets_path (str absolute path to secrets.yaml).
    - Outputs: dict {user, pass, from, to, host, port, kind_to} or None.
    - Example: ``load_smtp_creds_sync("/config/secrets.yaml")`` with
      ``smtp2go_user``/``_pass``/``_from``/``_host`` set -> that host, port
      ``587``. With no host key at all -> ``None`` plus an error naming
      ``slm_smtp_host``.
    """
    try:
        with open(secrets_path, "r", encoding="utf-8") as handle:
            secrets = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOGGER.error("notifications: failed to read %s: %s", secrets_path, exc)
        return None

    def pick(generic: str, fallback: str, default: Any = None) -> Any:
        """Return the generic key, else the office fallback, else ``default``."""
        return secrets.get(generic) or secrets.get(fallback) or default

    creds: Dict[str, Any] = {
        "user": pick("slm_smtp_user", "smtp2go_user"),
        "pass": pick("slm_smtp_pass", "smtp2go_pass"),
        "from": pick("slm_smtp_from", "smtp2go_from"),
        "to": pick("slm_smtp_to", "smtp2go_to"),
        "host": pick("slm_smtp_host", "smtp2go_host"),
        "port": int(pick("slm_smtp_port", "smtp2go_port", DEFAULT_SMTP_PORT)),
    }

    missing = [
        generic
        for field, (generic, fallback) in _REQUIRED_FIELDS.items()
        if not creds[field]
    ]
    if missing:
        _LOGGER.error(
            "notifications: missing required SMTP creds (any of slm_smtp_* or "
            "smtp2go_*): %s",
            missing,
        )
        return None

    kind_to: Dict[str, List[str]] = {}
    for kind in _KINDS:
        raw = (
            secrets.get(f"slm_smtp_{kind}_to")
            or secrets.get(f"smtp2go_{kind}_to")
            or ""
        )
        kind_to[kind] = [a.strip() for a in str(raw).split(",") if a.strip()]
    creds["kind_to"] = kind_to
    return creds
