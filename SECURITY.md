# Security Policy

Smart Lock Manager controls physical door locks. A bug here can affect a real door, so security reports matter.

## Supported versions

| Version | Supported |
|---------|-----------|
| 2026.8.x | Yes. Recommended: 2026.8.6 |
| Earlier than 2026.8.0 | No |

Releases earlier than 2026.8.0 contain security issues that were fixed in 2026.8.0 and 2026.8.1. If you run one, upgrade.

## Reporting a vulnerability

Do not open a public issue for a vulnerability.

1. Preferred: GitHub private vulnerability reporting, at https://github.com/ccsliinc/ha-smart-lock-manager/security/advisories/new
2. Alternative: email `jsugamele@gmail.com`

Include the affected version, what you found, and the impact. Reproduction steps help, but send them only through one of the private channels above.

This is a one-person project. I aim to acknowledge reports promptly, but I do not commit to a response time or a fix deadline. Please give me reasonable time to fix an issue before disclosing it publicly.

## What the code does

As of 2026.8.6:

- PIN codes are never exposed in entity state attributes. The sensor publishes a `has_pin_code` boolean only.
- PIN codes are never included in fired events. The codes-read event carries slot number and occupancy only.
- PIN codes are not written to logs at any level.
- All 21 services are registered with `async_register_admin_service`, so they require an admin user.
- The custom panel is registered with `require_admin=True`.
- The HTTP API view requires authentication and validates paths. Static files are served through `hass.http.async_register_static_paths`.
- Hardware writes are verified by reading the value back, so a failed write is not reported as success.
- The SMTP alert sender verifies the relay's certificate and hostname before it logs in, using Home Assistant's own verifying SSL context.
- Every value the panel interpolates into HTML is escaped, including into attributes, so a lock or zone name cannot inject markup.

## Tooling

- `bandit` runs in CI (`bandit -r custom_components/smart_lock_manager/ -ll`) and as a pre-commit hook.
- `vulture` runs as a pre-commit hook to catch dead code.
- GitHub secret scanning and push protection are enabled.
- Dependabot alerts and automated security updates are enabled.
- The test suite has 419 passing tests, 78% overall coverage, and 94% coverage on the lock sync engine.

These tools reduce risk. They do not prove the code is free of vulnerabilities.
