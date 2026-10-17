import os

import pytest


@pytest.fixture(autouse=True)
def isolate_machine_local_thumbnail_rules(monkeypatch, tmp_path):
    """Public tests never execute a developer's private providers or paths."""
    from src.file_handler.thumbnails import extensions

    monkeypatch.setenv(extensions.LOCAL_PROVIDER_ENV, str(tmp_path / "absent_provider.py"))
    loader = extensions.load_local_provider
    loader.cache_clear()
    yield
    loader.cache_clear()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("FLOWINONE_ONLINE_SMOKE") == "1":
        return
    skip_online = pytest.mark.skip(reason="set FLOWINONE_ONLINE_SMOKE=1 to run public-site smoke tests")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)
