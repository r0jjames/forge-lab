#!/usr/bin/env bash
set -euo pipefail
# Fetch the current Bamboo Data Center 24h timebomb license key from Atlassian's
# developer site and print it (key only) to stdout. Human messages go to stderr.
# The key is a "timebomb": it expires 24h after Bamboo first installs it, so the
# published value is stable — re-fetch and re-apply whenever the lab license lapses.
TIMEBOMB_URL="${TIMEBOMB_URL:-https://developer.atlassian.com/platform/marketplace/timebomb-licenses-for-testing-server-apps/}"
LICENSE_LABEL="${LICENSE_LABEL:-10 user Bamboo Data Center license, expires in 24 hours}"

command -v curl >/dev/null || { echo "get-license: need curl" >&2; exit 1; }
command -v python3 >/dev/null || { echo "get-license: need python3" >&2; exit 1; }

# SC2016: the single-quoted body is Python, not shell — expansion is not wanted.
# shellcheck disable=SC2016
curl -fsSL "$TIMEBOMB_URL" | python3 -c '
import re, sys
html = sys.stdin.read()
label = sys.argv[1]
i = html.find(label)
if i < 0:
    sys.stderr.write("get-license: label not found on page: %s\n" % label)
    sys.exit(2)
m = re.search(r"```bash\\n(.+?)\\n```", html[i:i + 2000], re.S)
if not m:
    sys.stderr.write("get-license: could not parse key block after label\n")
    sys.exit(3)
key = m.group(1).replace("\\n", "").strip()
if not re.fullmatch(r"[A-Za-z0-9+/=]+", key):
    sys.stderr.write("get-license: extracted text does not look like a license key\n")
    sys.exit(4)
print(key)
' "$LICENSE_LABEL"
