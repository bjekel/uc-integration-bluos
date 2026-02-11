"""Tests for bluos module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bluos import (
    BACKOFF_FACTOR,
    MAX_RECONNECT_DELAY,
    MIN_RECONNECT_DELAY,
    BluOSPlayer,
    Events,
    States,
)
from config import BluOSDevice


class TestStates:
    """Tests for States enum."""

    def test_all_states_defined(self):
        """Test all expected states are defined."""
        assert States.UNKNOWN == "UNKNOWN"
        assert States.UNAVAILABLE == "UNAVAILABLE"
        assert States.OFF == "OFF"
        assert States.ON == "ON"
        assert States.PLAYING == "PLAYING"
        assert States.PAUSED == "PAUSED"
        assert States.STOPPED == "STOPPED"
        assert States.BUFFERING == "BUFFERING"


class TestEvents:
    """Tests for Events enum."""

    def test_all_events_defined(self):
        """Test all expected events are defined."""
        assert Events.CONNECTING == "connecting"
        assert Events.CONNECTED == "connected"
        assert Events.DISCONNECTED == "disconnected"
        assert Events.ERROR == "error"
        assert Events.UPDATE == "update"


class TestBluOSPlayer:
    """Tests for BluOSPlayer class."""

    @pytest.fixture
    def device(self):
        """Create a test device configuration."""
        return BluOSDevice(
            id="test_device",
            name="Test Player",
            address="192.168.1.100",
            port=11000,
            volume_step=5,
            timeout=5.0,
        )

    @pytest.fixture
    def loop(self):
        """Get or create an event loop."""
        return asyncio.new_event_loop()

    @pytest.fixture
    def player(self, device, loop):
        """Create a BluOSPlayer instance."""
        return BluOSPlayer(device, loop)

    def test_initialization(self, player, device):
        """Test player initialization."""
        assert player.id == device.id
        assert player.name == device.name
        assert player.device == device
        assert player.available is False
        assert player.state == States.UNKNOWN

    def test_properties(self, player):
        """Test player properties."""
        assert player.events is not None
        assert player.inputs == []
        assert player.presets == []

    def test_map_state_play(self, player):
        """Test mapping 'play' state."""
        assert player._map_state("play") == States.PLAYING

    def test_map_state_stream(self, player):
        """Test mapping 'stream' state."""
        assert player._map_state("stream") == States.PLAYING

    def test_map_state_pause(self, player):
        """Test mapping 'pause' state."""
        assert player._map_state("pause") == States.PAUSED

    def test_map_state_stop(self, player):
        """Test mapping 'stop' state."""
        assert player._map_state("stop") == States.ON

    def test_map_state_connecting(self, player):
        """Test mapping 'connecting' state."""
        assert player._map_state("connecting") == States.BUFFERING

    def test_map_state_unknown(self, player):
        """Test mapping unknown state."""
        assert player._map_state("unknown_state") == States.ON

    def test_map_state_none(self, player):
        """Test mapping None state."""
        assert player._map_state(None) == States.UNKNOWN

    @pytest.mark.asyncio
    async def test_connect_success(self, player):
        """Test successful connection."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])

        with patch("bluos.Player", return_value=mock_pyblu_player):
            connected_event = asyncio.Event()
            player.events.on(Events.CONNECTED, lambda: connected_event.set())

            result = await player.connect()

            assert result is True
            assert player.available is True

    @pytest.mark.asyncio
    async def test_connect_unreachable(self, player):
        """Test connection when player is unreachable."""
        from pyblu.errors import PlayerUnreachableError

        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock(side_effect=PlayerUnreachableError("Cannot reach"))

        # Register error handler to prevent pyee from raising unhandled error
        error_received = []
        player.events.on(Events.ERROR, lambda err: error_received.append(err))

        with patch("bluos.Player", return_value=mock_pyblu_player):
            result = await player.connect()

            assert result is False
            assert player.available is False
            assert player.state == States.UNAVAILABLE
            assert len(error_received) == 1
            assert "Cannot reach" in error_received[0]

    @pytest.mark.asyncio
    async def test_disconnect(self, player):
        """Test disconnection."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.close = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            await player.disconnect()

            assert player.available is False
            assert player.state == States.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_play(self, player):
        """Test play command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.play = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.play()

            assert result is True
            mock_pyblu_player.play.assert_called_once()

    @pytest.mark.asyncio
    async def test_pause(self, player):
        """Test pause command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.pause = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.pause()

            assert result is True
            mock_pyblu_player.pause.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop(self, player):
        """Test stop command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.stop = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.stop()

            assert result is True
            mock_pyblu_player.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_next_track(self, player):
        """Test next track command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.skip = AsyncMock()
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "play"
        mock_status.volume = 50
        mock_status.mute = False
        mock_status.name = ""
        mock_status.artist = ""
        mock_status.album = ""
        mock_status.image = ""
        mock_status.total_seconds = 0
        mock_status.seconds = 0
        mock_status.shuffle = False
        mock_status.input_id = ""
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.next_track()

            assert result is True
            mock_pyblu_player.skip.assert_called_once()

    @pytest.mark.asyncio
    async def test_previous_track(self, player):
        """Test previous track command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.back = AsyncMock()
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "play"
        mock_status.volume = 50
        mock_status.mute = False
        mock_status.name = ""
        mock_status.artist = ""
        mock_status.album = ""
        mock_status.image = ""
        mock_status.total_seconds = 0
        mock_status.seconds = 0
        mock_status.shuffle = False
        mock_status.input_id = ""
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.previous_track()

            assert result is True
            mock_pyblu_player.back.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_volume(self, player):
        """Test set volume command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.volume = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.set_volume(50)

            assert result is True
            mock_pyblu_player.volume.assert_called_once_with(level=50)

    @pytest.mark.asyncio
    async def test_set_volume_clamped(self, player):
        """Test volume is clamped to valid range."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.volume = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()

            await player.set_volume(-10)
            mock_pyblu_player.volume.assert_called_with(level=0)

            await player.set_volume(150)
            mock_pyblu_player.volume.assert_called_with(level=100)

    @pytest.mark.asyncio
    async def test_mute(self, player):
        """Test mute command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.volume = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.mute(True)

            assert result is True
            mock_pyblu_player.volume.assert_called_once_with(mute=True)

    @pytest.mark.asyncio
    async def test_set_shuffle(self, player):
        """Test set shuffle command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.shuffle = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.set_shuffle(True)

            assert result is True
            mock_pyblu_player.shuffle.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_command_when_unavailable(self, player):
        """Test command returns False when player is unavailable."""
        result = await player.play()
        assert result is False

    def test_get_source_list_empty(self, player):
        """Test getting empty source list."""
        sources = player.get_source_list()
        assert sources == []

    def test_reconnect_delay_constants(self):
        """Test reconnect delay constants."""
        assert MIN_RECONNECT_DELAY == 1.0
        assert MAX_RECONNECT_DELAY == 30.0
        assert BACKOFF_FACTOR == 2.0

    @pytest.mark.asyncio
    async def test_status_to_attributes(self, player):
        """Test status to attributes conversion."""
        mock_status = MagicMock()
        mock_status.state = "play"
        mock_status.volume = 50
        mock_status.mute = False
        mock_status.name = "Test Song"
        mock_status.artist = "Test Artist"
        mock_status.album = "Test Album"
        mock_status.image = "http://example.com/image.jpg"
        mock_status.total_seconds = 180
        mock_status.seconds = 60
        mock_status.shuffle = True
        mock_status.input_id = "radio"

        attrs = player._status_to_attributes(mock_status)

        assert attrs["state"] == States.PLAYING
        assert attrs["volume"] == 50
        assert attrs["muted"] is False
        assert attrs["media_title"] == "Test Song"
        assert attrs["media_artist"] == "Test Artist"
        assert attrs["media_album"] == "Test Album"
        assert attrs["media_image_url"] == "http://example.com/image.jpg"
        assert attrs["media_duration"] == 180
        assert attrs["media_position"] == 60
        assert attrs["shuffle"] is True
        assert attrs["source"] == "radio"
