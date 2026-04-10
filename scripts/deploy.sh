#!/usr/bin/env bash
# deploy.sh - Deploy Smart Lock Manager to Raspberry Pi running Home Assistant
# Usage: ./scripts/deploy.sh [--no-restart] [--force]

set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ─── Config ───────────────────────────────────────────────────────────────────
REMOTE_HOST="root@10.0.17.11"
REMOTE_BASE="/homeassistant/custom_components"
REMOTE_PATH="${REMOTE_BASE}/smart_lock_manager"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SOURCE="${SCRIPT_DIR}/../custom_components/smart_lock_manager"
HA_URL="http://homeassistant.office.sugamele.com"
TARBALL="/tmp/slm-deploy.tar.gz"

# ─── Flags ────────────────────────────────────────────────────────────────────
NO_RESTART=false
FORCE=false

for arg in "$@"; do
  case $arg in
    --no-restart) NO_RESTART=true ;;
    --force)      FORCE=true ;;
    *) echo -e "${RED}Unknown argument: $arg${RESET}"; exit 1 ;;
  esac
done

# ─── Helpers ──────────────────────────────────────────────────────────────────
info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*"; }
header()  { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════${RESET}"; echo -e "${BOLD}${CYAN}  $*${RESET}"; echo -e "${BOLD}${CYAN}══════════════════════════════════════${RESET}"; }

confirm() {
  local prompt="$1"
  local response
  echo -ne "${YELLOW}[?]${RESET}     ${prompt} [y/N]: "
  read -r response
  [[ "$response" =~ ^[Yy]$ ]]
}

# ─── Pre-flight ───────────────────────────────────────────────────────────────
header "Smart Lock Manager — Deploy"

info "Local source : ${LOCAL_SOURCE}"
info "Remote target: ${REMOTE_HOST}:${REMOTE_PATH}"

# Verify local source exists
if [[ ! -d "$LOCAL_SOURCE" ]]; then
  error "Local source directory not found: ${LOCAL_SOURCE}"
  exit 1
fi

# Verify SSH connectivity
info "Checking SSH connectivity to ${REMOTE_HOST}..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE_HOST" "echo ok" &>/dev/null; then
  error "Cannot reach ${REMOTE_HOST} via SSH. Check your connection or SSH keys."
  exit 1
fi
success "SSH connectivity confirmed."

# ─── Backup ───────────────────────────────────────────────────────────────────
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_PATH="${REMOTE_BASE}/smart_lock_manager.bak.${TIMESTAMP}"

info "Creating remote backup → ${BACKUP_PATH}"
ssh "$REMOTE_HOST" "cp -r ${REMOTE_PATH} ${BACKUP_PATH} 2>/dev/null && echo 'backup ok' || echo 'no existing dir, skipping backup'"
success "Backup step complete."

# ─── Diff Summary ─────────────────────────────────────────────────────────────
header "Diff Summary"

LOCAL_COUNT=$(find "$LOCAL_SOURCE" -type f \
  ! -path '*/__pycache__/*' \
  ! -name '*.pyc' \
  ! -name '*.backup' \
  | wc -l | tr -d ' ')

info "Local file count: ${LOCAL_COUNT}"

# Remote file count (if directory exists)
REMOTE_COUNT=$(ssh "$REMOTE_HOST" "find ${REMOTE_PATH} -type f ! -name '*.pyc' 2>/dev/null | wc -l | tr -d ' '" || echo "0")
info "Remote file count: ${REMOTE_COUNT}"

# Checksum-based diff: list files that differ or are new
info "Comparing checksums for changed/new files..."
echo ""

# Build local checksum map (relative paths, excluding noise)
LOCAL_CHECKSUMS=$(find "$LOCAL_SOURCE" -type f \
  ! -path '*/__pycache__/*' \
  ! -name '*.pyc' \
  ! -name '*.backup' \
  | sort | while read -r f; do
    rel="${f#${LOCAL_SOURCE}/}"
    md5sum "$f" | awk -v r="$rel" '{print $1, r}'
  done)

# Build remote checksum map
REMOTE_CHECKSUMS=$(ssh "$REMOTE_HOST" \
  "find ${REMOTE_PATH} -type f ! -name '*.pyc' 2>/dev/null | sort | while read -r f; do
    rel=\"\${f#${REMOTE_PATH}/}\"
    md5sum \"\$f\" 2>/dev/null | awk -v r=\"\$rel\" '{print \$1, r}'
  done" || echo "")

# Compare: show files that are new or have different checksums
CHANGED_FILES=()
while IFS=' ' read -r local_hash local_rel; do
  remote_hash=$(echo "$REMOTE_CHECKSUMS" | awk -v r="$local_rel" '$2==r {print $1}')
  if [[ -z "$remote_hash" ]]; then
    echo -e "  ${GREEN}[NEW]${RESET}     ${local_rel}"
    CHANGED_FILES+=("$local_rel")
  elif [[ "$local_hash" != "$remote_hash" ]]; then
    echo -e "  ${YELLOW}[CHANGED]${RESET} ${local_rel}"
    CHANGED_FILES+=("$local_rel")
  fi
done <<< "$LOCAL_CHECKSUMS"

# Show files that exist remotely but not locally (would be removed)
while IFS=' ' read -r remote_hash remote_rel; do
  [[ -z "$remote_rel" ]] && continue
  local_hash=$(echo "$LOCAL_CHECKSUMS" | awk -v r="$remote_rel" '$2==r {print $1}')
  if [[ -z "$local_hash" ]]; then
    echo -e "  ${RED}[REMOVED]${RESET} ${remote_rel}"
  fi
done <<< "$REMOTE_CHECKSUMS"

CHANGED_COUNT="${#CHANGED_FILES[@]}"
echo ""
if [[ "$CHANGED_COUNT" -eq 0 ]]; then
  info "No changes detected — remote is already up to date."
else
  info "${CHANGED_COUNT} file(s) will be updated."
fi
echo ""

# ─── Confirmation ─────────────────────────────────────────────────────────────
if [[ "$FORCE" == false ]]; then
  if ! confirm "Proceed with deployment?"; then
    warn "Deployment aborted by user."
    exit 0
  fi
fi

# ─── Build & Transfer ─────────────────────────────────────────────────────────
header "Deploying"

info "Creating tarball at ${TARBALL}..."
tar czf "$TARBALL" \
  --exclude='__pycache__' \
  --exclude='.git' \
  --exclude='*.pyc' \
  --exclude='*.backup' \
  --exclude='.claude' \
  -C "$(dirname "$LOCAL_SOURCE")" \
  "$(basename "$LOCAL_SOURCE")"
success "Tarball created."

info "Transferring to ${REMOTE_HOST}:/tmp/..."
scp "$TARBALL" "${REMOTE_HOST}:/tmp/"
success "Transfer complete."

info "Extracting on remote..."
ssh "$REMOTE_HOST" "cd ${REMOTE_BASE} && rm -rf smart_lock_manager && tar xzf /tmp/slm-deploy.tar.gz && rm /tmp/slm-deploy.tar.gz"
success "Extraction complete."

# Clean up local tarball
rm -f "$TARBALL"

# ─── Post-Deploy Verification ─────────────────────────────────────────────────
header "Post-Deploy Verification"

REMOTE_COUNT_POST=$(ssh "$REMOTE_HOST" "find ${REMOTE_PATH} -type f ! -name '*.pyc' | wc -l | tr -d ' '")
info "Remote file count after deploy: ${REMOTE_COUNT_POST}"

if [[ "$LOCAL_COUNT" -eq "$REMOTE_COUNT_POST" ]]; then
  success "File counts match (${LOCAL_COUNT} files)."
else
  warn "File count mismatch — local: ${LOCAL_COUNT}, remote: ${REMOTE_COUNT_POST}"
fi

# ─── HA Restart ───────────────────────────────────────────────────────────────
if [[ "$NO_RESTART" == false ]]; then
  if [[ "$FORCE" == true ]] || confirm "Restart Home Assistant core?"; then
    info "Restarting HA core..."
    ssh "$REMOTE_HOST" "ha core restart"
    success "HA core restart triggered."
  else
    warn "Skipping HA restart. Reload integration manually if needed."
  fi
else
  info "--no-restart flag set, skipping HA restart."
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
header "Deploy Complete"
success "Smart Lock Manager deployed successfully!"
echo -e "${CYAN}  HA URL: ${HA_URL}${RESET}"
echo ""
