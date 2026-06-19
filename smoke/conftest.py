"""Smoke-test conftest — gate on env var, build a real aiohttp session.

Smoke tests hit live upstream APIs. They are slow, fragile, and may
fail for reasons unrelated to the integration (geo-blocks, rate limits,
upstream maintenance windows). Skip them in the default pytest run.

Enable with:

    FUELCOMPARE_RUN_SMOKE=1 pytest smoke -p no:homeassistant -v

`-p no:homeassistant` disables the pytest_homeassistant_custom_component
plugin auto-loaded by tests/conftest.py (which installs a process-wide
socket block).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import aiohttp
import pytest

try:
    from pytest_socket import enable_socket as _enable_socket
except ImportError:  # pragma: no cover
    _enable_socket = None


_RUN = os.environ.get("FUELCOMPARE_RUN_SMOKE") == "1"


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Skip every smoke test unless FUELCOMPARE_RUN_SMOKE=1."""
    if _RUN:
        return
    skip_marker = pytest.mark.skip(
        reason="Smoke tests off; set FUELCOMPARE_RUN_SMOKE=1 to run.",
    )
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _allow_real_sockets():
    """Re-enable sockets for the duration of every smoke test.

    If pytest-socket has been activated by another plugin (e.g.
    pytest_homeassistant_custom_component), unblock at the start of each
    smoke test so the real network calls go through.
    """
    if _enable_socket is not None:
        _enable_socket()
    yield


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    """Yield a real aiohttp session backed by the threaded resolver.

    aiodns + some Python builds ship a broken `Channel.getaddrinfo()`
    signature; force the threaded resolver so the smoke runner works in
    dev environments without surgery.
    """
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as s:
        yield s
