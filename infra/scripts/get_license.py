#!/usr/bin/env python3
"""Fetch the current Bamboo Data Center 24h timebomb license key.

Prints the key (and nothing else) to stdout; human messages go to stderr.
The key is a "timebomb": it expires 24h after Bamboo first installs it, so the
published value is stable — re-fetch and re-apply whenever the lab license lapses.

Uses only the standard library, so it runs anywhere python3 does.
"""

import os
import re
import sys
import urllib.request

TIMEBOMB_URL = os.environ.get(
    "TIMEBOMB_URL",
    "https://developer.atlassian.com/platform/marketplace/"
    "timebomb-licenses-for-testing-server-apps/",
)
LICENSE_LABEL = os.environ.get(
    "LICENSE_LABEL", "10 user Bamboo Data Center license, expires in 24 hours"
)

# The page embeds its markdown inside JSON, so the newlines around the fenced
# code block arrive as the literal two-character sequence \n, not real newlines.
KEY_BLOCK_RE = re.compile(r"```bash\\n(.+?)\\n```", re.S)
KEY_CHARS_RE = re.compile(r"[A-Za-z0-9+/=]+")

# Distinct exit codes, preserved from the shell version, so callers can tell a
# network failure from a page-layout change.
EXIT_LABEL_NOT_FOUND = 2
EXIT_UNPARSEABLE = 3
EXIT_NOT_A_KEY = 4


class LicenseError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def extract_license_key(html: str, label: str = LICENSE_LABEL) -> str:
    """Pull the key out of the timebomb page's HTML."""
    index = html.find(label)
    if index < 0:
        raise LicenseError(
            f"label not found on page: {label}", EXIT_LABEL_NOT_FOUND
        )
    match = KEY_BLOCK_RE.search(html[index:index + 2000])
    if not match:
        raise LicenseError("could not parse key block after label", EXIT_UNPARSEABLE)
    key = match.group(1).replace("\\n", "").strip()
    if not KEY_CHARS_RE.fullmatch(key):
        raise LicenseError(
            "extracted text does not look like a license key", EXIT_NOT_A_KEY
        )
    return key


def fetch_license_key(url: str = TIMEBOMB_URL, label: str = LICENSE_LABEL) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:
        html = response.read().decode("utf-8", "replace")
    return extract_license_key(html, label)


def main():
    try:
        print(fetch_license_key())
    except LicenseError as err:
        print(f"get-license: {err}", file=sys.stderr)
        sys.exit(err.code)
    except OSError as err:
        print(f"get-license: fetch failed: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
