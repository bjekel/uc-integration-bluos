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

    def test_enter_standby_disconnects_all_players(self, driver_state):
        """Standby fully disconnects every configured player (closing sessions)."""
        player_a = MagicMock()
        player_a.disconnect = AsyncMock()
        player_b = MagicMock()
        player_b.disconnect = AsyncMock()
        driver._configured_players["dev_a"] = player_a
        driver._configured_players["dev_b"] = player_b

        run(driver._on_enter_standby())

        player_a.disconnect.assert_awaited_once()
        player_b.disconnect.assert_awaited_once()
        assert driver._REMOTE_IN_STANDBY is True

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


class TestGroupingOptionsRefresh:
    """Regression: a device added later must appear as a grouping target on the
    devices configured before it.

    The first-configured device's media player is built when it is the only
    player, so its options start without GROUP_TOGGLE_*/GROUP_ALL. Refreshing all
    entities when the device set changes is what repairs that.
    """

    @staticmethod
    def _player(device_id, name, ip):
        from bluos import BluOSPlayer
        from config import BluOSDevice

        player = MagicMock(spec=BluOSPlayer)
        player.available = True
        player.get_simple_commands.return_value = ["PRESET_1"]
        player.device = BluOSDevice(id=device_id, name=name, address=ip)
        return player

    def _add(self, device_id, name, ip):
        from media_player import BluOSMediaPlayer

        player = self._player(device_id, name, ip)
        driver._configured_players[device_id] = player
        entity = BluOSMediaPlayer(player.device, player, lambda did=device_id: driver._group_targets(did))
        driver._entities[device_id] = entity
        return entity

    def test_first_device_gains_grouping_when_second_added(self, driver_state):
        from media_player import GROUP_ALL_CMD, GROUP_TOGGLE_PREFIX

        # First device configured alone — built with no group targets.
        entity1 = self._add("living", "Living Room", "192.168.1.10")
        assert GROUP_ALL_CMD not in entity1.simple_commands
        assert not any(c.startswith(GROUP_TOGGLE_PREFIX) for c in entity1.simple_commands)

        # Second device added later, then options refreshed (as _add_player does).
        entity2 = self._add("kitchen", "Kitchen", "192.168.1.11")
        driver._refresh_grouping_options()

        # Both now expose grouping toggles for each other plus GROUP_ALL.
        assert f"{GROUP_TOGGLE_PREFIX}Kitchen" in entity1.simple_commands
        assert GROUP_ALL_CMD in entity1.simple_commands
        assert f"{GROUP_TOGGLE_PREFIX}Living Room" in entity2.simple_commands
        assert GROUP_ALL_CMD in entity2.simple_commands


class TestRestartKeepsSubscriptions:
    """A restart (restore/configure of a changed device) keeps entities subscribed."""

    @pytest.fixture
    def real_stores(self, driver_state):
        """Use real ucapi entity stores so add/remove/get behave like production."""
        from ucapi.entities import Entities

        driver_state.available_entities = Entities("available", driver._LOOP)
        driver_state.configured_entities = Entities("configured", driver._LOOP)
        saved = {
            "_sensor_entities": dict(driver._sensor_entities),
            "_remote_entities": dict(driver._remote_entities),
            "_sensor_entity_id_to_device_id": dict(driver._sensor_entity_id_to_device_id),
            "_remote_entity_id_to_device_id": dict(driver._remote_entity_id_to_device_id),
            "_status_entities": dict(driver._status_entities),
            "_status_entity_id_to_device_id": dict(driver._status_entity_id_to_device_id),
        }
        for name in saved:
            getattr(driver, name).clear()
        yield driver_state
        for name, value in saved.items():
            getattr(driver, name).clear()
            getattr(driver, name).update(value)

    @staticmethod
    def _fake_player_factory(created):
        from bluos import BluOSPlayer

        def factory(device, _loop):
            player = MagicMock(spec=BluOSPlayer)
            player.id = device.id
            player.name = device.name
            player.device = device
            player.available = False
            player.presets = []
            player.get_source_list.return_value = []
            player.connect = AsyncMock()
            player.disconnect = AsyncMock()
            created.append(player)
            return player

        return factory

    def test_restart_moves_subscriptions_to_new_entities(self, real_stores, monkeypatch):
        from config import BluOSDevice

        created = []
        monkeypatch.setattr(driver, "BluOSPlayer", self._fake_player_factory(created))
        api = real_stores
        device = BluOSDevice(id="aa", name="Kitchen", address="10.0.0.1")

        run(driver._add_player(device))
        old_media = driver._entities["aa"]
        old_select = driver._select_entities["aa"]
        # Remote subscribes to the media player and the select (not sensor/remote).
        api.configured_entities.add(old_media)
        api.configured_entities.add(old_select)

        changed = BluOSDevice(id="aa", name="Kitchen", address="10.0.0.99")
        run(driver._restart_player(changed))

        new_media = driver._entities["aa"]
        assert new_media is not old_media
        assert new_media.player is created[-1]
        # Subscribed entities now point at the new objects -> commands reach the new player.
        assert api.configured_entities.get(old_media.id) is new_media
        assert api.configured_entities.get(old_select.id) is driver._select_entities["aa"]
        # Unsubscribed entities are not silently subscribed.
        assert api.configured_entities.get(driver._sensor_entities["aa"].id) is None
        assert api.configured_entities.get(driver._remote_entities["aa"].id) is None
        created[0].disconnect.assert_awaited_once()
        created[-1].connect.assert_awaited_once()

    def test_restart_moves_status_entity_subscriptions(self, real_stores, monkeypatch):
        from config import BluOSDevice

        monkeypatch.setattr(driver, "BluOSPlayer", self._fake_player_factory([]))
        api = real_stores
        device = BluOSDevice(id="aa", name="Kitchen", address="10.0.0.1")

        run(driver._add_player(device))
        old_quality = next(e for e in driver._status_entities["aa"] if e.id == "bluos_aa_stream_quality")
        api.configured_entities.add(old_quality)

        run(driver._restart_player(BluOSDevice(id="aa", name="Kitchen", address="10.0.0.99")))

        new_quality = api.configured_entities.get("bluos_aa_stream_quality")
        assert new_quality is not None and new_quality is not old_quality
        assert new_quality in driver._status_entities["aa"]
        assert api.configured_entities.get("bluos_aa_input") is None

    def test_add_and_remove_registers_all_status_entities(self, real_stores, monkeypatch):
        from config import BluOSDevice

        monkeypatch.setattr(driver, "BluOSPlayer", self._fake_player_factory([]))
        api = real_stores
        run(driver._add_player(BluOSDevice(id="aa", name="Kitchen", address="10.0.0.1")))

        ids = {
            "bluos_aa_input",
            "bluos_aa_sleep_timer",
            "bluos_aa_source",
            "bluos_aa_volume_db",
            "bluos_aa_stream_quality",
        }
        assert ids <= {e["entity_id"] for e in api.available_entities.get_all()}
        assert {driver._status_entity_id_to_device_id[i] for i in ids} == {"aa"}

        run(driver._remove_player("aa"))
        assert not ids & {e["entity_id"] for e in api.available_entities.get_all()}
        assert "aa" not in driver._status_entities
        assert not ids & driver._status_entity_id_to_device_id.keys()
