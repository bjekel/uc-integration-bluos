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

        disconnected_received = []
        player.events.on(Events.DISCONNECTED, lambda: disconnected_received.append(True))

        with patch("bluos.Player", return_value=mock_pyblu_player):
            result = await player.connect()

            assert result is False
            assert player.available is False
            assert player.state == States.UNAVAILABLE
            assert len(disconnected_received) == 1

    @pytest.mark.asyncio
    async def test_connect_unexpected_error(self, player):
        """Test connection when an unexpected exception escapes pyblu's error wrappers."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock(side_effect=RuntimeError("unexpected"))

        disconnected_received = []
        player.events.on(Events.DISCONNECTED, lambda: disconnected_received.append(True))

        with patch("bluos.Player", return_value=mock_pyblu_player):
            result = await player.connect()

            assert result is False
            assert player.available is False
            assert len(disconnected_received) == 1
            assert player._connecting is False

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
    async def test_reconnect_does_not_leak_workers_or_sessions(self, player):
        """Reconnecting (connect without an intervening disconnect) must tear down
        the previous workers and session rather than orphaning them."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.close = AsyncMock()

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            vol1, mute1 = player._volume_worker_task, player._mute_worker_task
            assert not vol1.done() and not mute1.done()

            # Reconnect: connect() again with no disconnect (the reconnect path).
            await player.connect()
            await asyncio.sleep(0)
            vol2, mute2 = player._volume_worker_task, player._mute_worker_task

            # New workers replaced the old ones, and the old ones were torn down
            # (not left running) — i.e. no leak.
            assert vol2 is not vol1 and mute2 is not mute1
            assert vol1.done() and mute1.done()
            # The prior pyblu session was closed during teardown.
            assert mock_pyblu_player.close.call_count == 1

            await player.disconnect()
            await asyncio.sleep(0)
            assert vol2.done() and mute2.done()

    @pytest.mark.asyncio
    async def test_play(self, player):
        """Test play command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.play = AsyncMock()
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.play()
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            mock_pyblu_player.play.assert_called_once()
            # Verify poll_status was called (triggers status with no etag)
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_pause(self, player):
        """Test pause command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.pause = AsyncMock()
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "pause"
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.pause()
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            mock_pyblu_player.pause.assert_called_once()
            # Verify poll_status was called (triggers status with no etag)
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_stop(self, player):
        """Test stop command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.stop = AsyncMock()
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "stop"
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.stop()
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            mock_pyblu_player.stop.assert_called_once()
            # Verify poll_status was called (triggers status with no etag)
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

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
        mock_status.sleep = 0
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
        mock_status.sleep = 0
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

        # Mock status for poll_status call
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.set_volume(50)
            # Wait for volume worker to process the queue
            await player._volume_queue.join()

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

        # Mock status for poll_status call
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()

            await player.set_volume(-10)
            await player._volume_queue.join()
            mock_pyblu_player.volume.assert_called_with(level=0)

            await player.set_volume(150)
            await player._volume_queue.join()
            mock_pyblu_player.volume.assert_called_with(level=100)

    @pytest.mark.asyncio
    async def test_mute(self, player):
        """Test mute command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.volume = AsyncMock()

        # Mock status for poll_status call
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "play"
        mock_status.volume = 50
        mock_status.mute = True
        mock_status.name = ""
        mock_status.artist = ""
        mock_status.album = ""
        mock_status.image = ""
        mock_status.total_seconds = 0
        mock_status.seconds = 0
        mock_status.shuffle = False
        mock_status.input_id = ""
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.mute(True)
            # Wait for mute worker to process the queue
            await player._mute_queue.join()

            assert result is True
            mock_pyblu_player.volume.assert_called_once_with(mute=True)

    @pytest.mark.asyncio
    async def test_set_shuffle(self, player):
        """Test set shuffle command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.base_url = "http://192.168.1.100:11000"

        # Mock the HTTP session for direct API call (async context manager)
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value="<playlist shuffle='1'/>")

        mock_context = MagicMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_context.__aexit__ = AsyncMock(return_value=None)

        mock_pyblu_player._session = MagicMock()
        mock_pyblu_player._session.get = MagicMock(return_value=mock_context)

        # Mock status for poll_status call
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
        mock_status.shuffle = True
        mock_status.input_id = ""
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.set_shuffle(True)
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            mock_pyblu_player._session.get.assert_called_once()
            call_args = mock_pyblu_player._session.get.call_args
            assert "/Shuffle" in call_args[0][0]
            assert call_args[1]["params"] == {"state": "1"}
            # Verify poll_status was called
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_set_repeat(self, player):
        """Test set repeat mode command."""
        from bluos import RepeatMode

        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.base_url = "http://192.168.1.100:11000"

        # Mock the HTTP session for direct API call
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value="")

        mock_context = MagicMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_context.__aexit__ = AsyncMock(return_value=None)

        mock_pyblu_player._session = MagicMock()
        mock_pyblu_player._session.get = MagicMock(return_value=mock_context)

        # Mock status for poll_status call
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.set_repeat(RepeatMode.ALL)
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            assert player.repeat_mode == RepeatMode.ALL
            call_args = mock_pyblu_player._session.get.call_args
            assert "/Repeat" in call_args[0][0]
            assert call_args[1]["params"] == {"state": "0"}  # ALL = 0
            # Verify poll_status was called
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_toggle_repeat(self, player):
        """Test toggle repeat cycles through modes."""
        from bluos import RepeatMode

        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.base_url = "http://192.168.1.100:11000"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value="")

        mock_context = MagicMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_context.__aexit__ = AsyncMock(return_value=None)

        mock_pyblu_player._session = MagicMock()
        mock_pyblu_player._session.get = MagicMock(return_value=mock_context)

        # Mock status for poll_status call
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()

            # OFF -> ALL
            assert player.repeat_mode == RepeatMode.OFF
            await player.toggle_repeat()
            assert player.repeat_mode == RepeatMode.ALL

            # ALL -> ONE
            await player.toggle_repeat()
            assert player.repeat_mode == RepeatMode.ONE

            # ONE -> OFF
            await player.toggle_repeat()
            assert player.repeat_mode == RepeatMode.OFF

    @pytest.mark.asyncio
    async def test_seek(self, player):
        """Test seek command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.base_url = "http://192.168.1.100:11000"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value="")

        mock_context = MagicMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_context.__aexit__ = AsyncMock(return_value=None)

        mock_pyblu_player._session = MagicMock()
        mock_pyblu_player._session.get = MagicMock(return_value=mock_context)

        # Mock status for poll_status call
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
        mock_status.seconds = 120
        mock_status.shuffle = False
        mock_status.input_id = ""
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.seek(120)
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            call_args = mock_pyblu_player._session.get.call_args
            assert "/Play" in call_args[0][0]
            assert call_args[1]["params"] == {"seek": "120"}
            # Verify poll_status was called
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_toggle_sleep_timer(self, player):
        """Test sleep timer toggle."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.sleep_timer = AsyncMock(return_value=15)

        # Mock status for poll_status call
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
        mock_status.sleep = 15
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.toggle_sleep_timer()
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result == 15
            assert player.sleep_timer == 15
            mock_pyblu_player.sleep_timer.assert_called_once()
            # Verify poll_status was called
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

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
        mock_status.sleep = 30

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

    @pytest.mark.asyncio
    async def test_select_source_preset_by_name(self, player):
        """Test select source with preset name triggers poll_status."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_preset = MagicMock()
        mock_preset.id = 1
        mock_preset.name = "My Radio"
        mock_pyblu_player.presets = AsyncMock(return_value=[mock_preset])
        mock_pyblu_player.load_preset = AsyncMock()

        # Mock status for poll_status call
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "stream"
        mock_status.volume = 50
        mock_status.mute = False
        mock_status.name = "My Radio"
        mock_status.artist = ""
        mock_status.album = ""
        mock_status.image = ""
        mock_status.total_seconds = 0
        mock_status.seconds = 0
        mock_status.shuffle = False
        mock_status.input_id = ""
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.select_source("My Radio")
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            mock_pyblu_player.load_preset.assert_called_once_with(1)
            assert player.current_preset_name == "My Radio"
            # Verify poll_status was called
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_select_source_legacy_preset(self, player):
        """Test select source with legacy preset:N format triggers poll_status."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_preset = MagicMock()
        mock_preset.id = 2
        mock_preset.name = "Jazz FM"
        mock_pyblu_player.presets = AsyncMock(return_value=[mock_preset])
        mock_pyblu_player.load_preset = AsyncMock()

        # Mock status for poll_status call
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "stream"
        mock_status.volume = 50
        mock_status.mute = False
        mock_status.name = "Jazz FM"
        mock_status.artist = ""
        mock_status.album = ""
        mock_status.image = ""
        mock_status.total_seconds = 0
        mock_status.seconds = 0
        mock_status.shuffle = False
        mock_status.input_id = ""
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.select_source("preset:2")
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            mock_pyblu_player.load_preset.assert_called_once_with(2)
            assert player.current_preset_name == "Jazz FM"
            # Verify poll_status was called
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_select_source_input(self, player):
        """Test select source with input triggers poll_status."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_input = MagicMock()
        mock_input.id = "hdmi1"
        mock_input.text = "HDMI 1"
        mock_input.url = "Capture:hw:1,0/48000/16/2"
        mock_pyblu_player.inputs = AsyncMock(return_value=[mock_input])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.play_url = AsyncMock()

        # Mock status for poll_status call
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "stream"
        mock_status.volume = 50
        mock_status.mute = False
        mock_status.name = "HDMI 1"
        mock_status.artist = ""
        mock_status.album = ""
        mock_status.image = ""
        mock_status.total_seconds = 0
        mock_status.seconds = 0
        mock_status.shuffle = False
        mock_status.input_id = "hdmi1"
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.select_source("hdmi1")
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            mock_pyblu_player.play_url.assert_called_once_with(mock_input.url)
            assert player.current_preset_name is None
            # Verify poll_status was called
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_load_preset_by_command(self, player):
        """Test load preset by command triggers poll_status."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_preset = MagicMock()
        mock_preset.id = 3
        mock_preset.name = "Classic Rock"
        mock_pyblu_player.presets = AsyncMock(return_value=[mock_preset])
        mock_pyblu_player.load_preset = AsyncMock()

        # Mock status for poll_status call
        mock_status = MagicMock()
        mock_status.etag = "test-etag"
        mock_status.state = "stream"
        mock_status.volume = 50
        mock_status.mute = False
        mock_status.name = "Classic Rock"
        mock_status.artist = ""
        mock_status.album = ""
        mock_status.image = ""
        mock_status.total_seconds = 0
        mock_status.seconds = 0
        mock_status.shuffle = False
        mock_status.input_id = ""
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.load_preset_by_command("PRESET_3")
            # Allow scheduled poll task to run
            await asyncio.sleep(0.2)

            assert result is True
            mock_pyblu_player.load_preset.assert_called_once_with(3)
            assert player.current_preset_name == "Classic Rock"
            # Verify poll_status was called
            mock_pyblu_player.status.assert_called_once()
            call_kwargs = mock_pyblu_player.status.call_args[1]
            assert call_kwargs.get("etag") is None

    @pytest.mark.asyncio
    async def test_volume_up(self, player):
        """Test volume up command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.volume = AsyncMock()

        # Mock status for getting current volume and poll_status
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            # Seed _last_known_volume via an initial poll (mirrors real usage)
            await player.poll_status(use_etag=False)
            result = await player.volume_up()
            # Wait for volume worker to process the queue
            await player._volume_queue.join()

            assert result is True
            # Volume step is 5 by default, so 50 + 5 = 55
            mock_pyblu_player.volume.assert_called_with(level=55)

    @pytest.mark.asyncio
    async def test_volume_down(self, player):
        """Test volume down command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.volume = AsyncMock()

        # Mock status for getting current volume and poll_status
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
        mock_status.sleep = 0
        mock_pyblu_player.status = AsyncMock(return_value=mock_status)

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            # Seed _last_known_volume via an initial poll (mirrors real usage)
            await player.poll_status(use_etag=False)
            result = await player.volume_down()
            # Wait for volume worker to process the queue
            await player._volume_queue.join()

            assert result is True
            # Volume step is 5 by default, so 50 - 5 = 45
            mock_pyblu_player.volume.assert_called_with(level=45)

    @pytest.mark.asyncio
    async def test_refresh_presets(self, player):
        """Test refresh presets command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])

        # Initial presets
        mock_preset1 = MagicMock()
        mock_preset1.id = 1
        mock_preset1.name = "Radio 1"
        mock_pyblu_player.presets = AsyncMock(return_value=[mock_preset1])

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            assert len(player.presets) == 1

            # Update presets mock for refresh
            mock_preset2 = MagicMock()
            mock_preset2.id = 2
            mock_preset2.name = "Radio 2"
            mock_pyblu_player.presets = AsyncMock(return_value=[mock_preset1, mock_preset2])

            result = await player.refresh_presets()

            assert result is True
            assert len(player.presets) == 2

    def test_get_simple_commands(self, player):
        """Test getting simple commands list."""
        # Add some mock presets
        mock_preset1 = MagicMock()
        mock_preset1.id = 1
        mock_preset1.name = "Radio 1"
        mock_preset2 = MagicMock()
        mock_preset2.id = 2
        mock_preset2.name = "Radio 2"
        player._presets = [mock_preset1, mock_preset2]

        commands = player.get_simple_commands()

        assert "PRESET_1" in commands
        assert "PRESET_2" in commands
        assert "REFRESH_PRESETS" in commands
        assert "SHUFFLE_TOGGLE" in commands
        assert "REPEAT_TOGGLE" in commands
        assert "SLEEP_TIMER" in commands

    @pytest.mark.asyncio
    async def test_play_error(self, player):
        """Test play command handles errors gracefully."""
        from pyblu.errors import PlayerError

        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.play = AsyncMock(side_effect=PlayerError("Connection failed"))

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.play()

            assert result is False

    @pytest.mark.asyncio
    async def test_set_volume_error(self, player):
        """Test set volume command handles errors gracefully in worker."""
        from pyblu.errors import PlayerError

        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])
        mock_pyblu_player.volume = AsyncMock(side_effect=PlayerError("Volume failed"))

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            # set_volume now queues the command and returns True immediately
            result = await player.set_volume(50)
            # Wait for volume worker to process (and handle) the error
            await player._volume_queue.join()

            # Command is queued successfully, error is handled in worker
            assert result is True
            mock_pyblu_player.volume.assert_called_once_with(level=50)

    @pytest.mark.asyncio
    async def test_select_source_not_found(self, player):
        """Test select source with non-existent source."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.select_source("nonexistent_source")

            assert result is False

    @pytest.mark.asyncio
    async def test_load_preset_by_command_invalid(self, player):
        """Test load preset by command with invalid command."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            result = await player.load_preset_by_command("INVALID_COMMAND")

            assert result is False

    def test_is_available_false_when_no_player(self, player):
        """Test _is_available returns False when no player."""
        assert player._is_available() is False

    @pytest.mark.asyncio
    async def test_is_available_true_when_connected(self, player):
        """Test _is_available returns True when connected."""
        mock_pyblu_player = MagicMock()
        mock_pyblu_player.sync_status = AsyncMock()
        mock_pyblu_player.inputs = AsyncMock(return_value=[])
        mock_pyblu_player.presets = AsyncMock(return_value=[])

        with patch("bluos.Player", return_value=mock_pyblu_player):
            await player.connect()
            assert player._is_available() is True
