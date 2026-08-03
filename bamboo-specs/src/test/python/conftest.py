"""Put the lab's Python trees on sys.path the same way the entrypoints do."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
LAB = REPO_ROOT / "bamboo-specs" / "src" / "main" / "java" / "lab"

for path in (
    LAB / "shared" / "python",
    LAB / "provisioncluster" / "scripts",
    LAB / "deprovisioncluster" / "scripts",
    REPO_ROOT / "infra" / "scripts",
    REPO_ROOT / "infra" / "agent",
):
    sys.path.insert(0, str(path))
