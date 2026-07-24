"""Browser-controller contract for Navigator's per-source sync retry."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_navigator_single_source_retry_browser_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Navigator browser contract")
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [node, "--test", str(root / "tests/browser/test_navigator_sync_retry.mjs")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
