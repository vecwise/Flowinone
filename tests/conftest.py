import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("FLOWINONE_ONLINE_SMOKE") == "1":
        return
    skip_online = pytest.mark.skip(reason="set FLOWINONE_ONLINE_SMOKE=1 to run public-site smoke tests")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)
