"""Tests for select_entity module."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import ucapi
from config import BluOSDevice
from pyblu import Preset
from select_entity import BluOSPresetSelect
from ucapi.select import Attributes, Commands, States


class TestBluOSPresetSelect:
    """Tests for BluOSPresetSelect entity."""

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
    def mock_presets(self):
        """Create mock presets."""
        preset1 = MagicMock(spec=Preset)
        preset1.id = 1
        preset1.name = "Radio Paradise"
        preset2 = MagicMock(spec=Preset)
        preset2.id = 2
        preset2.name = "Jazz FM"
        preset3 = MagicMock(spec=Preset)
        preset3.id = 3
        preset3.name = "Classical"
        return [preset1, preset2, preset3]

    @pytest.fixture
    def mock_player(self, mock_presets):
        """Create a mock BluOSPlayer."""
        player = MagicMock()
        player.id = "test_device"
        player.name = "Test Player"
        player.available = True
        player.presets = mock_presets
        return player

    @pytest.fixture
    def entity(self, device, mock_player):
        """Create a BluOSPresetSelect entity."""
        return BluOSPresetSelect(device, mock_player)

    def test_initialization(self, entity):
        """Test entity initialization."""
        assert entity.id == "bluos_test_device_presets"
        assert entity.name == {"en": "Test Player Presets"}

    def test_initial_attributes(self, entity):
        """Test initial attribute values."""
        assert entity.attributes[Attributes.STATE] == States.UNAVAILABLE
        assert entity.attributes[Attributes.CURRENT_OPTION] == ""
        assert entity.attributes[Attributes.OPTIONS] == ["Radio Paradise", "Jazz FM", "Classical"]

    def test_update_attributes_state_change(self, entity, mock_player):
        """Test updating attributes changes state."""
        mock_player.available = True
        attributes = {"current_preset": None}

        changed = entity.update_attributes(attributes)

        assert Attributes.STATE in changed
        assert changed[Attributes.STATE] == States.ON

    def test_update_attributes_current_option(self, entity, mock_player):
        """Test updating attributes with current preset."""
        mock_player.available = True
        attributes = {"current_preset": "Jazz FM"}

        changed = entity.update_attributes(attributes)

        assert Attributes.CURRENT_OPTION in changed
        assert changed[Attributes.CURRENT_OPTION] == "Jazz FM"

    def test_update_attributes_no_current_preset(self, entity, mock_player):
        """Test updating attributes with no current preset."""
        mock_player.available = True
        # First set state to ON
        entity.update_attributes({"current_preset": None})

        attributes = {"current_preset": None}
        changed = entity.update_attributes(attributes)

        # Should set current_option to empty since no preset is selected
        assert changed.get(Attributes.CURRENT_OPTION, "") == ""

    def test_update_attributes_no_change(self, entity, mock_player):
        """Test updating attributes with same values returns empty."""
        mock_player.available = True
        attributes = {"current_preset": "Jazz FM"}

        # First update
        entity.update_attributes(attributes)

        # Second update with same values
        changed = entity.update_attributes(attributes)

        assert Attributes.CURRENT_OPTION not in changed

    def test_update_attributes_options_change(self, entity, mock_player):
        """Test updating attributes when presets change."""
        mock_player.available = True

        # First update to set initial state
        entity.update_attributes({"current_preset": None})

        # Change presets
        new_preset = MagicMock(spec=Preset)
        new_preset.id = 4
        new_preset.name = "New Station"
        mock_player.presets = [new_preset]

        changed = entity.update_attributes({"current_preset": None})

        assert Attributes.OPTIONS in changed
        assert changed[Attributes.OPTIONS] == ["New Station"]

    def test_refresh_options(self, entity, mock_player):
        """Test refreshing options from player."""
        mock_player.available = True
        new_preset = MagicMock(spec=Preset)
        new_preset.id = 5
        new_preset.name = "Fresh Station"
        mock_player.presets = [new_preset]

        changed = entity.refresh_options()

        assert Attributes.OPTIONS in changed
        assert changed[Attributes.OPTIONS] == ["Fresh Station"]
        assert Attributes.STATE in changed
        assert changed[Attributes.STATE] == States.ON

    def test_set_unavailable(self, entity, mock_player):
        """Test setting entity as unavailable."""
        mock_player.available = True
        # First set to ON
        entity.update_attributes({"current_preset": None})

        changed = entity.set_unavailable()

        assert Attributes.STATE in changed
        assert changed[Attributes.STATE] == States.UNAVAILABLE

    def test_set_unavailable_already_unavailable(self, entity):
        """Test set_unavailable when already unavailable returns empty."""
        changed = entity.set_unavailable()
        assert changed == {}

    def test_clear_cached_attributes(self, entity, mock_player):
        """Test that clear_cached_attributes forces a full re-push on the next update."""
        mock_player.available = True
        entity.update_attributes({"current_preset": "Jazz FM"})
        # A repeat update with unchanged state normally yields no changes.
        assert entity.update_attributes({"current_preset": "Jazz FM"}) == {}

        entity.clear_cached_attributes()

        # After clearing, the next update re-pushes every value even though
        # nothing changed.
        changed = entity.update_attributes({"current_preset": "Jazz FM"})
        assert changed[Attributes.CURRENT_OPTION] == "Jazz FM"
        assert Attributes.STATE in changed
        assert Attributes.OPTIONS in changed

    @pytest.mark.asyncio
    async def test_command_select_option(self, entity, mock_player):
        """Test SELECT_OPTION command."""
        mock_player.select_source = AsyncMock(return_value=True)

        result = await entity.command(Commands.SELECT_OPTION, {"option": "Jazz FM"})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Jazz FM")

    @pytest.mark.asyncio
    async def test_command_select_option_missing_param(self, entity, mock_player):
        """Test SELECT_OPTION command without option parameter."""
        result = await entity.command(Commands.SELECT_OPTION, {})

        assert result == ucapi.StatusCodes.BAD_REQUEST

    @pytest.mark.asyncio
    async def test_command_select_first(self, entity, mock_player):
        """Test SELECT_FIRST command."""
        mock_player.select_source = AsyncMock(return_value=True)

        result = await entity.command(Commands.SELECT_FIRST, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Radio Paradise")

    @pytest.mark.asyncio
    async def test_command_select_last(self, entity, mock_player):
        """Test SELECT_LAST command."""
        mock_player.select_source = AsyncMock(return_value=True)

        result = await entity.command(Commands.SELECT_LAST, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Classical")

    @pytest.mark.asyncio
    async def test_command_select_next(self, entity, mock_player):
        """Test SELECT_NEXT command with current option."""
        mock_player.select_source = AsyncMock(return_value=True)
        mock_player.available = True
        # Set current option
        entity.attributes[Attributes.CURRENT_OPTION] = "Radio Paradise"

        result = await entity.command(Commands.SELECT_NEXT, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Jazz FM")

    @pytest.mark.asyncio
    async def test_command_select_next_wrap_around(self, entity, mock_player):
        """Test SELECT_NEXT command wraps around to first."""
        mock_player.select_source = AsyncMock(return_value=True)
        mock_player.available = True
        # Set current option to last
        entity.attributes[Attributes.CURRENT_OPTION] = "Classical"

        result = await entity.command(Commands.SELECT_NEXT, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Radio Paradise")

    @pytest.mark.asyncio
    async def test_command_select_next_no_current(self, entity, mock_player):
        """Test SELECT_NEXT command with no current option selects first."""
        mock_player.select_source = AsyncMock(return_value=True)

        result = await entity.command(Commands.SELECT_NEXT, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Radio Paradise")

    @pytest.mark.asyncio
    async def test_command_select_previous(self, entity, mock_player):
        """Test SELECT_PREVIOUS command with current option."""
        mock_player.select_source = AsyncMock(return_value=True)
        mock_player.available = True
        # Set current option
        entity.attributes[Attributes.CURRENT_OPTION] = "Jazz FM"

        result = await entity.command(Commands.SELECT_PREVIOUS, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Radio Paradise")

    @pytest.mark.asyncio
    async def test_command_select_previous_wrap_around(self, entity, mock_player):
        """Test SELECT_PREVIOUS command wraps around to last."""
        mock_player.select_source = AsyncMock(return_value=True)
        mock_player.available = True
        # Set current option to first
        entity.attributes[Attributes.CURRENT_OPTION] = "Radio Paradise"

        result = await entity.command(Commands.SELECT_PREVIOUS, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Classical")

    @pytest.mark.asyncio
    async def test_command_select_previous_no_current(self, entity, mock_player):
        """Test SELECT_PREVIOUS command with no current option selects last."""
        mock_player.select_source = AsyncMock(return_value=True)

        result = await entity.command(Commands.SELECT_PREVIOUS, {})

        assert result == ucapi.StatusCodes.OK
        mock_player.select_source.assert_called_once_with("Classical")

    @pytest.mark.asyncio
    async def test_command_when_unavailable(self, entity, mock_player):
        """Test command returns error when player unavailable."""
        mock_player.available = False

        result = await entity.command(Commands.SELECT_OPTION, {"option": "test"})

        assert result == ucapi.StatusCodes.SERVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_command_when_no_presets(self, entity, mock_player):
        """Test command returns error when no presets available."""
        mock_player.presets = []

        result = await entity.command(Commands.SELECT_FIRST, {})

        assert result == ucapi.StatusCodes.SERVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_command_not_implemented(self, entity, mock_player):
        """Test unknown command returns NOT_IMPLEMENTED."""
        result = await entity.command("unknown_command", {})

        assert result == ucapi.StatusCodes.NOT_IMPLEMENTED

    @pytest.mark.asyncio
    async def test_command_failure(self, entity, mock_player):
        """Test command failure returns SERVER_ERROR."""
        mock_player.select_source = AsyncMock(return_value=False)

        result = await entity.command(Commands.SELECT_FIRST, {})

        assert result == ucapi.StatusCodes.SERVER_ERROR


class TestBluOSPresetSelectEmptyPresets:
    """Tests for BluOSPresetSelect with empty presets."""

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
    def mock_player_no_presets(self):
        """Create a mock BluOSPlayer with no presets."""
        player = MagicMock()
        player.id = "test_device"
        player.name = "Test Player"
        player.available = True
        player.presets = []
        return player

    @pytest.fixture
    def entity_no_presets(self, device, mock_player_no_presets):
        """Create a BluOSPresetSelect entity with no presets."""
        return BluOSPresetSelect(device, mock_player_no_presets)

    def test_initialization_empty_presets(self, entity_no_presets):
        """Test entity initialization with empty presets."""
        assert entity_no_presets.attributes[Attributes.OPTIONS] == []

    def test_get_current_preset_index_empty_presets(self, entity_no_presets):
        """Test _get_current_preset_index returns None with empty presets."""
        entity_no_presets.attributes[Attributes.CURRENT_OPTION] = "Something"
        assert entity_no_presets._get_current_preset_index() is None

    def test_get_current_preset_index_no_current(self, entity_no_presets):
        """Test _get_current_preset_index returns None with no current option."""
        assert entity_no_presets._get_current_preset_index() is None
