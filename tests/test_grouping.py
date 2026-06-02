"""Tests for multi-room grouping: bluos helpers, media_player commands, sensor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import ucapi
from bluos import BluOSPlayer
from config import BluOSDevice
from media_player import (
    GROUP_ALL_CMD,
    GROUP_TOGGLE_PREFIX,
    LEAVE_GROUP_CMD,
    UNGROUP_ALL_CMD,
    BluOSMediaPlayer,
)
from sensor_entity import BluOSGroupSensor
from ucapi.sensor import Attributes as SensorAttributes
from ucapi.sensor import States as SensorStates


def _device(device_id: str, name: str, ip: str, port: int = 11000) -> BluOSDevice:
    return BluOSDevice(id=device_id, name=name, address=ip, port=port)


def _paired(ip: str, port: int = 11000) -> MagicMock:
    """A pyblu PairedPlayer stand-in with ip/port."""
    p = MagicMock()
    p.ip = ip
    p.port = port
    return p


def _sync(leader=None, followers=None) -> MagicMock:
    """A pyblu SyncStatus stand-in exposing leader/followers."""
    s = MagicMock()
    s.leader = leader
    s.followers = followers
    return s


# --------------------------------------------------------------------------- #
# bluos.py grouping helpers
# --------------------------------------------------------------------------- #


class TestBluOSPlayerGrouping:
    """Grouping action helpers on BluOSPlayer."""

    @pytest.fixture
    def loop(self):
        return asyncio.new_event_loop()

    @pytest.fixture
    def leader(self, loop):
        player = BluOSPlayer(_device("living", "Living Room", "192.168.1.10"), loop)
        player._player = MagicMock()
        player._player.add_follower = AsyncMock()
        player._player.remove_follower = AsyncMock()
        player._player.sync_status = AsyncMock(return_value=_sync())
        player._available = True
        player._schedule_poll = MagicMock()  # avoid real poll task creation
        return player

    @pytest.fixture
    def kitchen(self, loop):
        player = BluOSPlayer(_device("kitchen", "Kitchen", "192.168.1.11"), loop)
        player._available = True
        return player

    async def test_group_with_adds_follower(self, leader, kitchen):
        ok = await leader.group_with(kitchen)
        assert ok is True
        leader._player.add_follower.assert_awaited_once_with("192.168.1.11", 11000)
        leader._schedule_poll.assert_called_once()

    async def test_ungroup_removes_follower(self, leader, kitchen):
        ok = await leader.ungroup(kitchen)
        assert ok is True
        leader._player.remove_follower.assert_awaited_once_with("192.168.1.11", 11000)

    async def test_ungroup_all_removes_every_follower(self, leader):
        leader._player.sync_status = AsyncMock(
            return_value=_sync(followers=[_paired("192.168.1.11"), _paired("192.168.1.12")])
        )
        ok = await leader.ungroup_all()
        assert ok is True
        assert leader._player.remove_follower.await_count == 2

    async def test_ungroup_all_when_standalone_is_noop(self, leader):
        leader._player.sync_status = AsyncMock(return_value=_sync(followers=None))
        ok = await leader.ungroup_all()
        assert ok is True
        leader._player.remove_follower.assert_not_awaited()

    async def test_leave_group_asks_leader_to_drop_self(self, leader, kitchen):
        # kitchen leaves a group led by `leader`
        kitchen._schedule_poll = MagicMock()
        ok = await kitchen.leave_group(leader)
        assert ok is True
        leader._player.remove_follower.assert_awaited_once_with("192.168.1.11", 11000)

    async def test_is_grouped_with_true_when_follower_present(self, leader, kitchen):
        leader._player.sync_status = AsyncMock(return_value=_sync(followers=[_paired("192.168.1.11")]))
        assert await leader.is_grouped_with(kitchen) is True

    async def test_is_grouped_with_false_when_absent(self, leader, kitchen):
        leader._player.sync_status = AsyncMock(return_value=_sync(followers=[_paired("192.168.1.99")]))
        assert await leader.is_grouped_with(kitchen) is False

    def test_group_info_leader(self, leader):
        leader._sync_status = _sync(followers=[_paired("192.168.1.11")])
        status = MagicMock()
        status.group_name = "Living Room + 1"
        info = leader._group_info(status)
        assert info["group_role"] == "leader"
        assert info["group_followers"] == [("192.168.1.11", 11000)]
        assert info["group_leader"] is None
        assert info["group_name"] == "Living Room + 1"

    def test_group_info_follower(self, leader):
        leader._sync_status = _sync(leader=_paired("192.168.1.10"))
        status = MagicMock()
        status.group_name = None
        info = leader._group_info(status)
        assert info["group_role"] == "follower"
        assert info["group_leader"] == ("192.168.1.10", 11000)

    def test_group_info_standalone(self, leader):
        leader._sync_status = None
        status = MagicMock()
        status.group_name = None
        info = leader._group_info(status)
        assert info["group_role"] == "standalone"


# --------------------------------------------------------------------------- #
# media_player.py grouping commands
# --------------------------------------------------------------------------- #


class TestMediaPlayerGroupingCommands:
    """GROUP_* command dispatch on the media player entity."""

    @pytest.fixture
    def kitchen_target(self):
        target = MagicMock(spec=BluOSPlayer)
        target.device = _device("kitchen", "Kitchen", "192.168.1.11")
        target.available = True
        return target

    @pytest.fixture
    def office_target(self):
        target = MagicMock(spec=BluOSPlayer)
        target.device = _device("office", "Office", "192.168.1.12")
        target.available = True
        return target

    @pytest.fixture
    def player(self):
        player = MagicMock(spec=BluOSPlayer)
        player.available = True
        player.get_simple_commands.return_value = ["PRESET_1", "SLEEP_TIMER"]
        player.group_with = AsyncMock(return_value=True)
        player.ungroup = AsyncMock(return_value=True)
        player.ungroup_all = AsyncMock(return_value=True)
        player.leave_group = AsyncMock(return_value=True)
        player.is_grouped_with = AsyncMock(return_value=False)
        player.sync_status = None
        return player

    @pytest.fixture
    def entity(self, player, kitchen_target, office_target):
        device = _device("living", "Living Room", "192.168.1.10")
        targets = [kitchen_target, office_target]
        return BluOSMediaPlayer(device, player, lambda: targets)

    def test_simple_commands_include_grouping(self, entity):
        commands = entity.options[ucapi.media_player.Options.SIMPLE_COMMANDS]
        assert f"{GROUP_TOGGLE_PREFIX}Kitchen" in commands
        assert f"{GROUP_TOGGLE_PREFIX}Office" in commands
        assert GROUP_ALL_CMD in commands
        assert UNGROUP_ALL_CMD in commands
        assert LEAVE_GROUP_CMD in commands
        # Existing preset/utility commands are preserved
        assert "PRESET_1" in commands

    async def test_toggle_adds_when_not_grouped(self, entity, player, kitchen_target):
        player.is_grouped_with = AsyncMock(return_value=False)
        result = await entity.command(f"{GROUP_TOGGLE_PREFIX}Kitchen")
        assert result == ucapi.StatusCodes.OK
        player.group_with.assert_awaited_once_with(kitchen_target)
        player.ungroup.assert_not_awaited()

    async def test_toggle_removes_when_grouped(self, entity, player, kitchen_target):
        player.is_grouped_with = AsyncMock(return_value=True)
        result = await entity.command(f"{GROUP_TOGGLE_PREFIX}Kitchen")
        assert result == ucapi.StatusCodes.OK
        player.ungroup.assert_awaited_once_with(kitchen_target)
        player.group_with.assert_not_awaited()

    async def test_toggle_unknown_room_is_bad_request(self, entity, player):
        result = await entity.command(f"{GROUP_TOGGLE_PREFIX}Bedroom")
        assert result == ucapi.StatusCodes.BAD_REQUEST
        player.group_with.assert_not_awaited()

    async def test_group_all_adds_every_available_target(self, entity, player, kitchen_target, office_target):
        result = await entity.command(GROUP_ALL_CMD)
        assert result == ucapi.StatusCodes.OK
        assert player.group_with.await_count == 2

    async def test_ungroup_all(self, entity, player):
        result = await entity.command(UNGROUP_ALL_CMD)
        assert result == ucapi.StatusCodes.OK
        player.ungroup_all.assert_awaited_once()

    async def test_leave_group_when_follower(self, entity, player, kitchen_target):
        # This player is following Kitchen (leader endpoint matches kitchen_target)
        player.sync_status = _sync(leader=_paired("192.168.1.11"))
        result = await entity.command(LEAVE_GROUP_CMD)
        assert result == ucapi.StatusCodes.OK
        player.leave_group.assert_awaited_once_with(kitchen_target)

    async def test_leave_group_when_not_grouped_is_noop(self, entity, player):
        player.sync_status = _sync(leader=None)
        result = await entity.command(LEAVE_GROUP_CMD)
        assert result == ucapi.StatusCodes.OK
        player.leave_group.assert_not_awaited()

    async def test_leave_group_unmanaged_leader(self, entity, player):
        player.sync_status = _sync(leader=_paired("10.0.0.5"))  # not a configured target
        result = await entity.command(LEAVE_GROUP_CMD)
        assert result == ucapi.StatusCodes.SERVICE_UNAVAILABLE
        player.leave_group.assert_not_awaited()


# --------------------------------------------------------------------------- #
# sensor_entity.py group sensor
# --------------------------------------------------------------------------- #


class TestGroupSensor:
    """The group state sensor."""

    @pytest.fixture
    def kitchen_target(self):
        target = MagicMock(spec=BluOSPlayer)
        target.device = _device("kitchen", "Kitchen", "192.168.1.11")
        return target

    @pytest.fixture
    def player(self):
        player = MagicMock(spec=BluOSPlayer)
        player.available = True
        return player

    @pytest.fixture
    def sensor(self, player, kitchen_target):
        device = _device("living", "Living Room", "192.168.1.10")
        return BluOSGroupSensor(device, player, lambda: [kitchen_target])

    def test_standalone(self, sensor):
        changed = sensor.update_attributes({"group_role": "standalone"})
        assert changed[SensorAttributes.VALUE] == "Not grouped"
        assert changed[SensorAttributes.STATE] == SensorStates.ON

    def test_follower_resolves_leader_name(self, sensor):
        changed = sensor.update_attributes({"group_role": "follower", "group_leader": ("192.168.1.11", 11000)})
        assert changed[SensorAttributes.VALUE] == "Following Kitchen"

    def test_leader_lists_follower_names(self, sensor):
        changed = sensor.update_attributes({"group_role": "leader", "group_followers": [("192.168.1.11", 11000)]})
        assert changed[SensorAttributes.VALUE] == "Leader (Kitchen)"

    def test_unknown_endpoint_falls_back_to_ip(self, sensor):
        changed = sensor.update_attributes({"group_role": "follower", "group_leader": ("10.0.0.9", 11000)})
        assert changed[SensorAttributes.VALUE] == "Following 10.0.0.9"

    def test_unavailable_player(self, sensor, player):
        # Establish an available, grouped state first
        sensor.update_attributes({"group_role": "leader", "group_followers": [("192.168.1.11", 11000)]})
        # Then the player goes away
        player.available = False
        changed = sensor.update_attributes({"group_role": "leader", "group_followers": [("192.168.1.11", 11000)]})
        assert changed[SensorAttributes.STATE] == SensorStates.UNAVAILABLE
        assert changed[SensorAttributes.VALUE] == ""

    def test_no_change_returns_empty(self, sensor):
        sensor.update_attributes({"group_role": "standalone"})
        changed = sensor.update_attributes({"group_role": "standalone"})
        assert changed == {}
