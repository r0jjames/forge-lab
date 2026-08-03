#!/usr/bin/env python3
"""24h timebomb ritual (license already lapsed): fetch the current key, copy it
to the clipboard, and open the Bamboo license-admin page so you can paste + save.

For the FIRST-TIME setup wizard (no admin page yet) use `make license` instead.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from get_license import LicenseError, fetch_license_key  # noqa: E402

LICENSE_ADMIN = os.environ.get(
    "LICENSE_ADMIN", "http://localhost:8085/admin/updateLicense!doDefault.action"
)


def main():
    try:
        key = fetch_license_key()
    except LicenseError as err:
        print(f"get-license: {err}", file=sys.stderr)
        sys.exit(err.code)
    except OSError as err:
        print(f"get-license: fetch failed: {err}", file=sys.stderr)
        sys.exit(1)

    print("Bamboo Data Center 24h timebomb license key:")
    print()
    print(key)
    print()
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=key, text=True, check=False)
        print("(copied to clipboard)")
    print(f"Paste it at: {LICENSE_ADMIN}")
    if shutil.which("open"):
        subprocess.run(["open", LICENSE_ADMIN], check=False)


if __name__ == "__main__":
    main()
