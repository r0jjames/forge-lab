#!/usr/bin/env python3
"""Check the DEPROV plan variable before the host agent is asked for.

Its own copy rather than an import of the PROV plan's validate_prov.py: no
plan reaches into another plan's scripts, and the shared part is planvars.
The `_deprov` suffix keeps the two importable side by side under test — both
scripts directories are on the suite's sys.path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import planvars, proc  # noqa: E402

USAGE = "usage: validate_deprov.py <cluster_name>"


def main(argv):
    cluster = planvars.require_cluster_name(argv[0] if argv else "", USAGE)
    print(f"==> cluster_name {cluster}")


if __name__ == "__main__":
    proc.main(main)
