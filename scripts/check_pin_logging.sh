#!/usr/bin/env bash
# check_pin_logging.sh
# Description: Pre-commit guard that fails if a raw PIN/usercode value is emitted
#   from a logging or exception statement. Catches both printf-style
#   (`pin=%s`, `usercode=%s`) and f-string / dict-format (`{pin}`, `pin: {`)
#   exposure. Conservative: only flags logger calls and `raise` statements that
#   pair a pin/usercode token with an interpolated value, so legitimate
#   service-call dict keys and plain assignments do not trip it.
# Inputs: none (scans custom_components/smart_lock_manager/**/*.py)
# Outputs: exit 0 if clean, exit 1 (with offending lines printed) if a leak is found.
# Example: scripts/check_pin_logging.sh
set -euo pipefail

ROOT="custom_components/smart_lock_manager/"

# Tokens that indicate a value was MASKED — these are allowed and stripped from
# consideration so masked log lines never count as violations.
#   ****        -> masked literal
#   <set>       -> presence-only marker
#   len(        -> logging the length, not the value
#   MISSING     -> placeholder for an absent value
#   isdigit     -> a boolean check, not the value
MASK_ALLOW='\*\*\*\*|<set>|len\(|MISSING|isdigit'

# Pattern 1 (printf / %-format): a logger call or raise that contains a
#   pin/usercode token immediately followed by a %-format placeholder.
#   Matches: pin=%s  usercode=%s  pin=%d  pin: %s  "pin %r"  pin=%(x)s
PRINTF='(_LOGGER\.[a-z]+|raise [A-Za-z]*Error)\(.*(pin|usercode)[^a-zA-Z0-9_]*[:= ][^a-zA-Z0-9_]*%[srd(]'

# Pattern 2 (f-string / brace-format): a logger call or raise that interpolates
#   a pin/usercode token inside a {...} brace.
#   Matches: f"... {slot.pin_code} ..."  "...{pin}..."  "pin: {usercode}"
BRACE='(_LOGGER\.[a-z]+|raise [A-Za-z]*Error)\(.*\{[^}]*(pin|usercode)[^}]*\}'

# Legacy pattern kept for back-compat: `pin ... : ... {`
LEGACY='pin.*:.*\{'

violations="$(
  grep -rEn --include="*.py" -e "$PRINTF" -e "$BRACE" -e "$LEGACY" "$ROOT" 2>/dev/null \
    | grep -vE "$MASK_ALLOW" || true
)"

if [ -n "$violations" ]; then
  echo "❌ Found potential PIN/usercode logging without masking:"
  echo "$violations"
  echo ""
  echo "Mask the value: log a presence marker (\"<set>\"), the length, or '****' instead of the raw code."
  exit 1
fi

echo "✅ No unmasked PIN/usercode logging found"
exit 0
