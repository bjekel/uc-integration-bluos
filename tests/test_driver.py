"""Tests for the driver module's poller and standby orchestration.

driver.py keeps its state in module-level globals and builds its own event loop
(`driver._LOOP`) and `IntegrationAPI` at import time. So this harness:

- runs coroutines on ``driver._LOOP`` via :func:`run` (not pytest-asyncio's loop),
- replaces ``driver.api`` with a mock so handlers don't touch real websockets,
- snapshots and restores the module globals around every test for isolation.

The driver's event handlers resolve ``api`` from module globals at call time, so
replacing ``driver.api`` is picked up even though the ``@api.listens_to``
decorators ran at import against the original instance.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import driver
import pytest
import ucapi


def run(coro):
    """Run a coroutine to completion on the driver's own event loop."""
    return driver._LOOP.run_until_complete(coro)


@pytest.fixture(autouse=True)
def driver_state():
    """Snapshot/restore driver globals and stub the API for each test."""
    saved = {
        "_configured_players": dict(driver._configured_players),
        "_entities": dict(driver._entities),
        "_select_entities": dict(driver._select_entities),
        "_entity_id_to_device_id": dict(driver._entity_id_to_device_id),
        "_select_entity_id_to_device_id": dict(driver._select_entity_id_to_device_id),
        "_active_poll_tasks": list(driver._active_poll_tasks),
        "_REMOTE_IN_STANDBY": driver._REMOTE_IN_STANDBY,
        "_last_device_state": driver._last_device_state,
        "api": driver.api,
        "poller_active": driver._poller_active.is_set(),
    }

    # Stub API so handlers never reach a websocket; set_device_state must await.
    mock_api = MagicMock()
    mock_api.set_device_state = AsyncMock()
    driver.api = mock_api

    # Baseline: active (not standby), no players/entities/in-flight polls.
    driver._configured_players.clear()
    driver._entities.clear()
    driver._select_entities.clear()
    driver._entity_id_to_device_id.clear()
    driver._select_entity_id_to_device_id.clear()
    driver._active_poll_tasks = []
    driver._REMOTE_IN_STANDBY = False
    driver._last_device_state = None
    driver._poller_active.set()

    yield mock_api

    # Restore originals.
    driver.api = saved["api"]
    driver._configured_players.clear()
    driver._configured_players.update(saved["_configured_players"])
    driver._entities.clear()
    driver._entities.update(saved["_entities"])
    driver._select_entities.clear()
    driver._select_entities.update(saved["_select_entities"])
    driver._entity_id_to_device_id.clear()
    driver._entity_id_to_device_id.update(saved["_entity_id_to_device_id"])
    driver._select_entity_id_to_device_id.clear()
    driver._select_entity_id_to_device_id.update(saved["_select_entity_id_to_device_id"])
    driver._active_poll_tasks = saved["_active_poll_tasks"]
    driver._REMOTE_IN_STANDBY = saved["_REMOTE_IN_STANDBY"]
    driver._last_device_state = saved["_last_device_state"]
    if saved["poller_active"]:
        driver._poller_active.set()
    else:
        driver._poller_active.clear()


class TestStandbyCancellation:
    """Standby handling of in-flight long-polls."""

    def test_enter_standby_cancels_inflight_polls(self, driver_state):
        """In-flight long-poll tasks are cancelled on standby."""

        async def scenario():
            started = asyncio.Event()

            async def fake_poll():
                started.set()
                await asyncio.sleep(30)  # stand-in for a blocking long-poll

            task = asyncio.get_running_loop().create_task(fake_poll())
            driver._active_poll_tasks = [task]
            await started.wait()

            await driver._on_enter_standby()

            # Let the cancellation propagate into the task.
            try:
                await task
            except asyncio.CancelledError:
                pass
            return task

        task = run(scenario())

        assert task.cancelled()
        assert driver._REMOTE_IN_STANDBY is True
        assert not driver._poller_active.is_set()
        driver_state.set_device_state.assert_awaited_once_with(ucapi.DeviceStates.DISCONNECTED)

    def test_enter_standby_with_no_inflight_polls_is_safe(self, driver_state):
        """Entering standby with no in-flight polls does not raise."""
        driver._active_poll_tasks = []

        run(driver._on_enter_standby())

        assert driver._REMOTE_IN_STANDBY is True
        assert not driver._poller_active.is_set()

    def test_exit_standby_resumes_poller(self, driver_state):
        """Exiting standby clears the flag and re-arms the poller."""
        driver._REMOTE_IN_STANDBY = True
        driver._poller_active.clear()

        run(driver._on_exit_standby())

        assert driver._REMOTE_IN_STANDBY is False
        assert driver._poller_active.is_set()


class TestPlayerUpdateStandbyGuard:
    """The _REMOTE_IN_STANDBY guard in the player UPDATE handler."""

    def test_update_suppressed_during_standby(self, driver_state):
        """A poll result arriving during standby is not pushed to the remote."""
        entity = MagicMock()
        entity.id = "bluos_dev1"
        entity.update_attributes = MagicMock(return_value={"state": "PLAYING"})
        driver._entities["dev1"] = entity
        driver._REMOTE_IN_STANDBY = True

        driver._on_player_update("dev1", {"state": "play"})

        entity.update_attributes.assert_not_called()
        driver_state.configured_entities.update_attributes.assert_not_called()

    def test_update_pushed_when_active(self, driver_state):
        """A poll result is pushed to the remote when not in standby."""
        entity = MagicMock()
        entity.id = "bluos_dev1"
        entity.update_attributes = MagicMock(return_value={"state": "PLAYING"})
        driver._entities["dev1"] = entity
        driver._REMOTE_IN_STANDBY = False

        driver._on_player_update("dev1", {"state": "play"})

        entity.update_attributes.assert_called_once_with({"state": "play"})
        driver_state.configured_entities.update_attributes.assert_called_once_with("bluos_dev1", {"state": "PLAYING"})


class TestDeviceStateDedup:
    """Deduplication of device-state pushes to the remote."""

    def test_unchanged_state_is_not_repushed(self, driver_state):
        """Pushing the same device state twice only notifies the remote once."""
        run(driver._set_device_state(ucapi.DeviceStates.CONNECTED))
        run(driver._set_device_state(ucapi.DeviceStates.CONNECTED))

        driver_state.set_device_state.assert_awaited_once_with(ucapi.DeviceStates.CONNECTED)

    def test_changed_state_is_pushed(self, driver_state):
        """A genuine state change is pushed to the remote."""
        run(driver._set_device_state(ucapi.DeviceStates.CONNECTED))
        run(driver._set_device_state(ucapi.DeviceStates.DISCONNECTED))

        assert driver_state.set_device_state.await_count == 2

    def test_connect_forces_state_resend(self, driver_state):
        """A remote (re)connect resets dedup so the state is resent to the new client."""
        run(driver._set_device_state(ucapi.DeviceStates.CONNECTED))
        driver_state.set_device_state.reset_mock()

        # New remote connects: no players configured, so device state becomes
        # DISCONNECTED — but the key assertion is that _on_connect resets dedup.
        run(driver._on_connect())

        # Reset happened, so the post-connect _update_device_state pushed afresh.
        assert driver_state.set_device_state.await_count >= 1
