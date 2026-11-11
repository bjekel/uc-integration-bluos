"""Tests for the wake-up reconnect race (commands during reconnect, fast network retry)."""

import asyncio
import errno
import time
from unittest.mock import AsyncMock, MagicMock, patch

import bluos
import pytest
from bluos import BluOSPlayer
from config import BluOSDevice
from pyblu.errors import PlayerUnreachableError


def _net_unreachable() -> PlayerUnreachableError:
    err = PlayerUnreachableError("Connection error")
    err.__cause__ = OSError(errno.ENETUNREACH, "Network is unreachable")
    return err


@pytest.fixture
async def player():
    device = BluOSDevice(id="d", name="Node", address="10.0.0.1", port=11000)
    return BluOSPlayer(device, asyncio.get_running_loop())


def _ok_pyblu():
    p = MagicMock()
    p.sync_status = AsyncMock()
    p.close = AsyncMock()
    p.inputs = AsyncMock(return_value=[])
    p.presets = AsyncMock(return_value=[])
    return p


@pytest.mark.asyncio
async def test_command_during_pending_reconnect_waits_and_succeeds(player):
    """Failed connect + 1s backoff pending: ensure_connected connects right away."""
    bad = MagicMock()
    bad.sync_status = AsyncMock(side_effect=PlayerUnreachableError("timeout"))
    bad.close = AsyncMock()
    with patch("bluos.Player", return_value=bad):
        assert await player.connect() is False
    assert player.is_reconnecting  # 1s backoff pending

    with patch("bluos.Player", return_value=_ok_pyblu()):
        start = asyncio.get_running_loop().time()
        assert await player.ensure_connected(timeout=3.0) is True
        assert asyncio.get_running_loop().time() - start < 0.5  # did not wait for backoff
    assert player.available
    await player.disconnect()


@pytest.mark.asyncio
async def test_command_joins_running_connect(player):
    gate = asyncio.Event()
    ok = _ok_pyblu()

    async def slow_sync():
        await gate.wait()

    ok.sync_status = AsyncMock(side_effect=slow_sync)
    with patch("bluos.Player", return_value=ok):
        connect_task = asyncio.create_task(player.connect())
        await asyncio.sleep(0.01)
        assert player._connecting
        waiter = asyncio.create_task(player.ensure_connected(timeout=3.0))
        await asyncio.sleep(0.1)
        assert not waiter.done()
        gate.set()
        assert await waiter is True
        await connect_task
    await player.disconnect()


@pytest.mark.asyncio
async def test_command_starts_connect_when_nothing_running(player):
    """Command handled before the exit_standby handler: ensure_connected connects."""
    with patch("bluos.Player", return_value=_ok_pyblu()):
        assert not player.is_reconnecting and not player._connecting
        assert await player.ensure_connected(timeout=3.0) is True
    await player.disconnect()


@pytest.mark.asyncio
async def test_command_times_out_with_false(player):
    bad = MagicMock()
    bad.sync_status = AsyncMock(side_effect=PlayerUnreachableError("timeout"))
    bad.close = AsyncMock()
    with patch("bluos.Player", return_value=bad):
        assert await player.ensure_connected(timeout=0.3) is False
    player.cancel_reconnect()


@pytest.mark.asyncio
async def test_ensure_connected_noop_when_available(player):
    player._available = True
    with patch("bluos.Player") as ctor:
        assert await player.ensure_connected() is True
        ctor.assert_not_called()


@pytest.mark.asyncio
async def test_network_unreachable_retried_fast(player):
    """ENETUNREACH right after wake-up retries after ~50ms, not 1s, and connects."""
    bad = MagicMock()
    bad.sync_status = AsyncMock(side_effect=_net_unreachable())
    bad.close = AsyncMock()
    start = asyncio.get_running_loop().time()
    with patch("bluos.Player", side_effect=[bad, _ok_pyblu()]):
        assert await player.connect() is False
        assert player.is_reconnecting
        for _ in range(40):
            if player.available:
                break
            await asyncio.sleep(0.025)
    assert player.available
    assert asyncio.get_running_loop().time() - start < bluos.MIN_RECONNECT_DELAY
    assert player._reconnect_delay == bluos.MIN_RECONNECT_DELAY  # backoff untouched
    await player.disconnect()


@pytest.mark.asyncio
async def test_network_unreachable_outside_window_uses_backoff(player):
    bad = MagicMock()
    bad.sync_status = AsyncMock(side_effect=_net_unreachable())
    bad.close = AsyncMock()

    player._connect_series_start = time.monotonic() - (bluos.NETWORK_RETRY_WINDOW + 1)
    with patch("bluos.Player", return_value=bad), patch.object(player, "_schedule_reconnect") as sched:
        await player.connect()
        sched.assert_called_once_with()  # regular backoff, no fast-retry args


@pytest.mark.asyncio
async def test_other_unreachable_errors_use_backoff(player):
    bad = MagicMock()
    bad.sync_status = AsyncMock(side_effect=PlayerUnreachableError("timeout"))
    bad.close = AsyncMock()
    with patch("bluos.Player", return_value=bad), patch.object(player, "_schedule_reconnect") as sched:
        await player.connect()
        sched.assert_called_once_with()


@pytest.mark.asyncio
async def test_failed_reconnect_attempt_chains_next_retry(player):
    """A failing attempt inside the reconnect task must schedule the next retry."""
    bad = MagicMock()
    bad.sync_status = AsyncMock(side_effect=_net_unreachable())
    bad.close = AsyncMock()
    with patch("bluos.Player", side_effect=[bad, bad, _ok_pyblu()]):
        await player.connect()
        for _ in range(60):
            if player.available:
                break
            await asyncio.sleep(0.025)
    assert player.available
    await player.disconnect()
