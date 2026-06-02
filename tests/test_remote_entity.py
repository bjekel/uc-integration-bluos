"""Tests for the Remote entity."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import ucapi
from bluos import BluOSPlayer
from config import BluOSDevice
from media_player import BluOSMediaPlayer
from remote_entity import BluOSRemote
from ucapi.remote import Attributes, Commands, Features, States


def _device() -> BluOSDevice:
    return BluOSDevice(id="dev1", name="Living Room", address="192.168.1.10", port=11000)


def _preset(preset_id: int, name: str) -> MagicMock:
    preset = MagicMock()
    preset.id = preset_id
    preset.name = name
    return preset


class TestBluOSRemote:
    """Tests for BluOSRemote."""

    @pytest.fixture
    def player(self):
        player = MagicMock(spec=BluOSPlayer)
        player.available = True
        player.presets = [_preset(1, "Jazz"), _preset(2, "Radio")]
        return player

    @pytest.fixture
    def media_entity(self):
        entity = MagicMock(spec=BluOSMediaPlayer)
        entity.simple_commands = ["PRESET_1", "PRESET_2", "GROUP_ALL", "UNGROUP_ALL"]
        entity.command = AsyncMock(return_value=ucapi.StatusCodes.OK)
        return entity

    @pytest.fixture
    def remote(self, player, media_entity):
        return BluOSRemote(_device(), player, media_entity)

    def test_construction(self, remote):
        assert remote.id == "bluos_dev1_remote"
        assert remote.name == {"en": "Living Room Remote"}
        assert Features.SEND_CMD in remote.features
        assert Features.ON_OFF in remote.features

    def test_simple_commands_combine_transport_and_dynamic(self, remote):
        commands = remote.options["simple_commands"]
        assert "PLAY_PAUSE" in commands  # transport, from the remote
        assert "VOLUME_UP" in commands
        assert "POWER_TOGGLE" in commands
        assert "PRESET_1" in commands  # dynamic, from the media player
        assert "GROUP_ALL" in commands

    def test_ui_pages_include_presets(self, remote):
        pages = remote.options["user_interface"]["pages"]
        page_ids = {p["page_id"] for p in pages}
        assert "transport" in page_ids
        assert "presets" in page_ids

    async def test_send_cmd_transport_is_translated(self, remote, media_entity):
        result = await remote.command(Commands.SEND_CMD, {"command": "PLAY_PAUSE"})
        assert result == ucapi.StatusCodes.OK
        media_entity.command.assert_awaited_once_with("play_pause")

    async def test_send_cmd_passthrough_for_presets(self, remote, media_entity):
        await remote.command(Commands.SEND_CMD, {"command": "PRESET_2"})
        media_entity.command.assert_awaited_once_with("PRESET_2")

    async def test_send_cmd_passthrough_for_grouping(self, remote, media_entity):
        await remote.command(Commands.SEND_CMD, {"command": "GROUP_ALL"})
        media_entity.command.assert_awaited_once_with("GROUP_ALL")

    async def test_power_commands_delegate(self, remote, media_entity):
        await remote.command(Commands.ON)
        await remote.command(Commands.OFF)
        await remote.command(Commands.TOGGLE)
        awaited = [c.args[0] for c in media_entity.command.await_args_list]
        assert awaited == ["on", "off", "toggle"]

    async def test_send_cmd_sequence(self, remote, media_entity):
        result = await remote.command(Commands.SEND_CMD_SEQUENCE, {"sequence": ["PLAY_PAUSE", "STOP"]})
        assert result == ucapi.StatusCodes.OK
        awaited = [c.args[0] for c in media_entity.command.await_args_list]
        assert awaited == ["play_pause", "stop"]

    async def test_send_cmd_missing_command(self, remote, media_entity):
        result = await remote.command(Commands.SEND_CMD, {})
        assert result == ucapi.StatusCodes.BAD_REQUEST
        media_entity.command.assert_not_awaited()

    async def test_unknown_command(self, remote):
        result = await remote.command("bogus")
        assert result == ucapi.StatusCodes.NOT_IMPLEMENTED

    async def test_unavailable_player(self, remote, player, media_entity):
        player.available = False
        result = await remote.command(Commands.SEND_CMD, {"command": "PLAY_PAUSE"})
        assert result == ucapi.StatusCodes.SERVICE_UNAVAILABLE
        media_entity.command.assert_not_awaited()

    def test_state_on_when_available(self, remote):
        changed = remote.update_attributes({})
        assert changed[Attributes.STATE] == States.ON

    def test_set_unavailable(self, remote):
        remote.update_attributes({})  # -> ON
        changed = remote.set_unavailable()
        assert changed[Attributes.STATE] == States.UNAVAILABLE
