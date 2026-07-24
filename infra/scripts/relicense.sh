#!/usr/bin/env bash
set -euo pipefail
# 24h timebomb ritual (license already lapsed): fetch the current key, copy it to
# the clipboard, and open the Bamboo license-admin page so you can paste + save.
# For the FIRST-TIME setup wizard (no admin page yet) use `make license` instead.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LICENSE_ADMIN="${LICENSE_ADMIN:-http://localhost:8085/admin/updateLicense!doDefault.action}"

key="$("$HERE/get-license.sh")"
echo "Bamboo Data Center 24h timebomb license key:"
echo
echo "$key"
echo
if command -v pbcopy >/dev/null; then
  printf '%s' "$key" | pbcopy && echo "(copied to clipboard)"
fi
echo "Paste it at: ${LICENSE_ADMIN}"
command -v open >/dev/null && open "${LICENSE_ADMIN}" || true
