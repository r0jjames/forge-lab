#!/usr/bin/env bash
set -euo pipefail
# 24h timebomb ritual: grab key from Atlassian dev page, paste into Bamboo admin.
TIMEBOMB_URL="https://developer.atlassian.com/platform/marketplace/timebomb-licenses-for-testing-server-apps/"
LICENSE_ADMIN="http://localhost:8085/admin/updateLicense!doDefault.action"
echo "1. Copy the '10 user Bamboo Data Center license (24h)' key from:"
echo "   ${TIMEBOMB_URL}"
echo "2. Paste it at: ${LICENSE_ADMIN}"
command -v open >/dev/null && { open "${TIMEBOMB_URL}"; open "${LICENSE_ADMIN}"; } || true
