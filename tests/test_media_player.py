"""Tests for media_player module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import ucapi
from bluos import BluOSPlayer
from bluos import States as BluOSStates
from config import BluOSDevice
from media_player import BLUOS_FEATURES, BluOSMediaPlayer
from ucapi.media_player import Attributes, Commands, DeviceClasses, Features, States


class TestBluOSFeatures:
    """Tests for feature list."""

    def test_all_required_features(self):
        """Test all required features are present."""
        assert Features.ON_OFF in BLUOS_FEATURES
        assert Features.TOGGLE in BLUOS_FEATURES
        assert Features.VOLUME in BLUOS_FEATURES
        assert Features.VOLUME_UP_DOWN in BLUOS_FEATURES
        assert Features.MUTE_TOGGLE in BLUOS_FEATURES
        assert Features.PLAY_PAUSE in BLUOS_FEATURES
        assert Features.STOP in BLUOS_FEATURES
        assert Features.NEXT in BLUOS_FEATURES
        assert Features.PREVIOUS in BLUOS_FEATURES
        assert Features.SHUFFLE in BLUOS_FEATURES
        assert Features.SELECT_SOURCE in BLUOS_FEATURES
        assert Features.MEDIA_TITLE in BLUOS_FEATURES
        assert Features.MEDIA_ARTIST in BLUOS_FEATURES
        assert Features.MEDIA_ALBUM in BLUOS_FEATURES
        assert Features.MEDIA_IMAGE_URL in BLUOS_FEATURES
        assert Features.MEDIA_DURATION in BLUOS_FEATURES
        assert Features.MEDIA_POSITION in BLUOS_FEATURES


class TestBluOSMediaPlayer:
    """Tests for BluOSMediaPlayer entity."""

    @pytest.fixture
    def device(self):
        """Create a test device configuration."""
        return BluOSDevice(
            id="test_device",
            name="Test Player",
            address="192.168.1.100",
            port=11000,
        )

    @pytest.fixture
    def mock_player(self):
        """Create a mock BluOSPlayer."""
        player = MagicMock(spec=BluOSPlayer)
        player.id = "test_device"
        player.name = "Test Player"
        player.available = True
        player.state = BluOSStates.ON
        player.get_source_list.return_value = ["radio", "preset:1"]
        return player

    @pytest.fixture
    def entity(self, device, mock_player):
        """Create a BluOSMediaPlayer entity."""
        return BluOSMediaPlayer(device, mock_player)

    def test_initialization(self, entity):
        """Test entity initialization."""
        assert entity.id == "bluos_test_device"
        # ucapi wraps name in dict with language keys
        assert entity.name == {"en": "Test Player"}
        assert entity.device_class == DeviceClasses.SPEAKER
        assert entity.features == BLUOS_FEATURES

    def test_initial_attributes(self, entity):
        """Test initial attribute values."""
        assert entity.attributes[Attributes.STATE] == States.UNAVAILABLE
        assert entity.attributes[Attributes.VOLUME] == 0
        assert entity.attributes[Attributes.MUTED] is False
        assert entity.attributes[Attributes.MEDIA_TITLE] == ""
        assert entity.attributes[Attributes.SOURCE_LIST] == []

    def test_player_property(self, entity, mock_player):
        """Test player property."""
        assert entity.player == mock_player

    def test_map_state_playing(self, entity):
        """Test mapping PLAYING state."""
        result = entity._map_state(BluOSStates.PLAYING)
        assert result == States.PLAYING

    def test_map_state_paused(self, entity):
        """Test mapping PAUSED state."""
        result = entity._map_state(BluOSStates.PAUSED)
        assert result == States.PAUSED

    def test_map_state_on(self, entity):
        """Test mapping ON state."""
        result = entity._map_state(BluOSStates.ON)
        assert result == States.ON

    def test_map_state_unavailable(self, entity):
        """Test mapping UNAVAILABLE state."""
        result = entity._map_state(BluOSStates.UNAVAILABLE)
        assert result == States.UNAVAILABLE

    def test_map_state_unknown(self, entity):
        """Test mapping UNKNOWN state."""
        result = entity._map_state(BluOSStates.UNKNOWN)
        assert result == States.UNKNOWN

    def test_update_attributes(self, entity, mock_player):
        """Test updating attributes."""
        attributes = {
            "state": BluOSStates.PLAYING,
            "volume": 50,
            "muted": False,
            "media_title": "Test Song",
            "media_artist": "Test Artist",
            "media_album": "Test Album",
        }

        changed = entity.update_attributes(attributes)

        assert Attributes.STATE in changed
        assert changed[Attributes.STATE] == States.PLAYING
        assert Attributes.VOLUME in changed
        assert changed[Attributes.VOLUME] == 50
        assert Attributes.MEDIA_TITLE in changed
        assert changed[Attributes.MEDIA_TITLE] == "Test Song"

    def test_update_attributes_shuffle_state(self, entity, mock_player):
        """Test shuffle state is updated from attributes."""
        # Initially shuffle should be False (default)
        attributes = {"state": BluOSStates.PLAYING, "shuffle": True}
        changed = entity.update_attributes(attributes)

        assert Attributes.SHUFFLE in changed
        assert changed[Attributes.SHUFFLE] is True

        # Update to False
        attributes = {"state": BluOSStates.PLAYING, "shuffle": False}
        changed = entity.update_attributes(attributes)

        assert Attributes.SHUFFLE in changed
        assert changed[Attributes.SHUFFLE] is False

    def test_update_attributes_no_change(self, entity, mock_player):
        """Test updating attributes with same values returns empty."""
        attributes = {
            "state": BluOSStates.PLAYING,
            "volume": 50,
        }

        # First update
        entity.update_attributes(attributes)

        # Second update with same values
        changed = entity.update_attributes(attributes)

        assert Attributes.STATE not in changed
        assert Attributes.VOLUME not in changed

    def test_update_attributes_source_list(self, entity, mock_player):
        """Test source list is updated from player."""
        mock_player.get_source_list.return_value = ["input1", "preset:1", "preset:2"]

        attributes = {"state": BluOSStates.ON}
        changed = entity.update_attributes(attributes)

        assert Attributes.SOURCE_LIST in changed
        assert changed[Attributes.SOURCE_LIST] == ["input1", "preset:1", "preset:2"]

    def test_set_unavailable(self, entity):
        """Test setting entity as unavailable."""
        # First set to playing
        entity.update_attributes({"state": BluOSStates.PLAYING})

        # Then set unavailable
        changed = entity.set_unavailable()

        assert Attributes.STATE in changed
        assert changed[Attributes.STATE] == States.UNAVAILABLE

    def test_set_unavailable_already_unavailable(self, entity):
        """Test set_unavailable when already unavailable returns empty."""
        # First call sets _last_attributes to UNAVAILABLE
        entity.set_unavailable()
        # Second call should return empty since already unavailable
        changed = entity.set_unavailable()
        assert changed == {}

    @pytest.mark.asyncio
    async def test_command_play_pause_when_playing(self, entity, mock_player):
        """Test play_pause command when playing."""
        entity._last_attributes[Attributes.STATE] = States.PLAYING
        mock_player.pause = AsyncMock(return_value=True)

        result = await entity.command(Commands.PLAY_PAUSE, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.pause.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_play_pause_when_paused(self, entity, mock_player):
        """Test play_pause command when paused."""
        entity._last_attributes[Attributes.STATE] = States.PAUSED
        mock_player.play = AsyncMock(return_value=True)

        result = await entity.command(Commands.PLAY_PAUSE, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.play.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_stop(self, entity, mock_player):
        """Test stop command."""
        mock_player.stop = AsyncMock(return_value=True)

        result = await entity.command(Commands.STOP, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_next(self, entity, mock_player):
        """Test next command."""
        mock_player.next_track = AsyncMock(return_value=True)

        result = await entity.command(Commands.NEXT, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.next_track.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_previous(self, entity, mock_player):
        """Test previous command."""
        mock_player.previous_track = AsyncMock(return_value=True)

        result = await entity.command(Commands.PREVIOUS, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.previous_track.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_volume(self, entity, mock_player):
        """Test volume command."""
        mock_player.set_volume = AsyncMock(return_value=True)

        result = await entity.command(Commands.VOLUME, {"volume": 75})

        assert result == ucapi.StatusCodes.OK
        mock_player.set_volume.assert_called_once_with(75)

    @pytest.mark.asyncio
    async def test_command_volume_up(self, entity, mock_player):
        """Test volume up command."""
        mock_player.volume_up = AsyncMock(return_value=True)

        result = await entity.command(Commands.VOLUME_UP, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.volume_up.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_volume_down(self, entity, mock_player):
        """Test volume down command."""
        mock_player.volume_down = AsyncMock(return_value=True)

        result = await entity.command(Commands.VOLUME_DOWN, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.volume_down.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_mute_toggle(self, entity, mock_player):
        """Test mute toggle command."""
        mock_player.toggle_mute = AsyncMock(return_value=True)

        result = await entity.command(Commands.MUTE_TOGGLE, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.toggle_mute.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_mute(self, entity, mock_player):
        """Test mute command."""
        mock_player.mute = AsyncMock(return_value=True)

        result = await entity.command(Commands.MUTE, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.mute.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_command_unmute(self, entity, mock_player):
        """Test unmute command."""
        mock_player.mute = AsyncMock(return_value=True)

        result = await entity.command(Commands.UNMUTE, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.mute.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_command_shuffle(self, entity, mock_player):
        """Test shuffle command."""
        mock_player.set_shuffle = AsyncMock(return_value=True)

        result = await entity.command(Commands.SHUFFLE, {"shuffle": True})

        assert result == ucapi.StatusCodes.OK
        mock_player.set_shuffle.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_command_select_source(self, entity, mock_player):
        """Test select source command."""
        mock_player.select_source = AsyncMock(return_value=True)

        result = await entity.command(Commands.SELECT_SOURCE, {"source": "radio"})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("radio")

    @pytest.mark.asyncio
    async def test_command_on(self, entity, mock_player):
        """Test on command (maps to play)."""
        mock_player.play = AsyncMock(return_value=True)

        result = await entity.command(Commands.ON, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.play.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_off(self, entity, mock_player):
        """Test off command (maps to stop)."""
        mock_player.stop = AsyncMock(return_value=True)

        result = await entity.command(Commands.OFF, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_when_unavailable(self, entity, mock_player):
        """Test command returns error when player unavailable."""
        mock_player.available = False

        result = await entity.command(Commands.PLAY_PAUSE, {})

        assert result == ucapi.StatusCodes.SERVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_command_not_implemented(self, entity, mock_player):
        """Test unknown command returns NOT_IMPLEMENTED."""
        result = await entity.command("unknown_command", {})

        assert result == ucapi.StatusCodes.NOT_IMPLEMENTED

    @pytest.mark.asyncio
    async def test_command_failure(self, entity, mock_player):
        """Test command failure returns SERVER_ERROR."""
        mock_player.play = AsyncMock(return_value=False)

        result = await entity.command(Commands.ON, {})

        assert result == ucapi.StatusCodes.SERVER_ERROR
