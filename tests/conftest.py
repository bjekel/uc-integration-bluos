"""Shared test fixtures."""

import pytest
from bluos import BluOSPlayer


def pytest_configure(config):
    config.addinivalue_line("markers", "real_status_fetch: use the real BluOSPlayer._fetch_status HTTP code")


@pytest.fixture(autouse=True)
def _status_via_pyblu_mock(request, monkeypatch):
    """
    Route status polls through the mocked ``pyblu.Player.status()``.

    ``BluOSPlayer._fetch_status`` does its own HTTP request (to read fields
    pyblu drops). Most tests mock the pyblu player and its ``status()``; this
    keeps those tests meaningful. Tests of the real request use the
    ``real_status_fetch`` marker.
    """
    if request.node.get_closest_marker("real_status_fetch"):
        return

    async def fetch(self, player, etag, poll_timeout, timeout):  # pylint: disable=unused-argument
        status = await player.status(etag=etag, poll_timeout=poll_timeout, timeout=timeout)
        return status, {}

    monkeypatch.setattr(BluOSPlayer, "_fetch_status", fetch)
