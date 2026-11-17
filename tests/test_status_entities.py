"""Tests for our own /Status long-poll and the select/sensor entities built on it."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
import ucapi
from aiohttp import web
from aiohttp.test_utils import TestServer
from bluos import (
    SLEEP_TIMER_STEPS,
    BluOSPlayer,
    Events,
    States,
    format_stream_quality,
    parse_status_extras,
)
from config import BluOSDevice
from pyblu import Input, Player
from pyblu.errors import PlayerError, PlayerUnreachableError
from select_entity import BluOSInputSelect, BluOSSleepTimerSelect
from sensor_entity import BluOSSourceSensor, BluOSStreamQualitySensor, BluOSVolumeSensor
from ucapi.select import Attributes as SelectAttributes
from ucapi.select import Commands as SelectCommands
from ucapi.sensor import Attributes as SensorAttributes

# Trimmed real responses from a PULSE FLEX (paused, Tidal) and a NODE (stopped).
PULSE_FLEX_STATUS = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<status etag="863a80a0"><album>Greatest Hitz</album><artist>Limp Bizkit</artist>'
    b"<canSeek>1</canSeek><db>-44.3</db><image>/Artwork?service=Tidal</image><mute>0</mute>"
    b"<name>Break Stuff</name><quality>cd</quality><secs>7</secs><service>Tidal</service>"
    b"<serviceName>TIDAL</serviceName><shuffle>1</shuffle><sleep></sleep><state>pause</state>"
    b"<streamFormat>FLAC 16/44.1</streamFormat><totlen>167</totlen><volume>28</volume></status>"
)
NODE_STATUS = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<status etag="cfc1606b"><db>0</db><mute>0</mute><name>I Hate Everything About You</name>'
    b"<quality>cd</quality><secs>0</secs><service>Tidal</service><serviceName>TIDAL</serviceName>"
    b"<shuffle>1</shuffle><sleep></sleep><state>stop</state><totlen>231</totlen><volume>-1</volume></status>"
)


SYNC_STATUS = (
    b'<SyncStatus etag="s1" id="127.0.0.1:11000" mac="00:00" name="Test" icon="/i.png" initialized="true"'
    b' brand="Bluesound" model="N132" modelName="NODE" db="-30.5" volume="30"></SyncStatus>'
)


def _status_xml(etag: str, *, input_id: str = "", sleep: int = 0, state: str = "play") -> bytes:
    input_el = f"<inputId>{input_id}</inputId>" if input_id else ""
    return (
        f'<status etag="{etag}"><db>-30.5</db><mute>0</mute><name>Song</name>{input_el}'
        f"<quality>320000</quality><service>Capture</service><sleep>{sleep or ''}</sleep>"
        f"<state>{state}</state><volume>30</volume></status>"
    ).encode()


class TestParseExtras:
    def test_real_pulse_flex_status(self):
        assert parse_status_extras(PULSE_FLEX_STATUS) == {
            "quality": "cd",
            "stream_format": "FLAC 16/44.1",
            "service_name": "TIDAL",
        }

    def test_missing_fields_are_empty(self):
        assert parse_status_extras(b'<status etag="x"><state>stop</state></status>') == {
            "quality": "",
            "stream_format": "",
            "service_name": "",
        }

    def test_invalid_xml_never_raises(self):
        assert parse_status_extras(b"<status")["quality"] == ""

    @pytest.mark.parametrize(
        ("stream_format", "quality", "expected"),
        [
            ("FLAC 16/44.1", "cd", "FLAC 16/44.1"),
            ("", "cd", "CD"),
            ("", "hd", "Hi-Res"),
            ("", "mqaAuthored", "MQA Studio"),
            ("", "dolbyAudio", "Dolby Audio"),
            ("", "320000", "320 kbps"),
            ("", "somethingNew", "somethingNew"),
            ("", "", ""),
        ],
    )
    def test_format_stream_quality(self, stream_format, quality, expected):
        assert format_stream_quality(stream_format, quality) == expected


class FakeBluOS:
    """Minimal BluOS /Status server with real long-poll semantics."""

    def __init__(self):
        self.etag = "e1"
        self.body = _status_xml("e1")
        self.changed = asyncio.Event()
        self.requests: list[dict[str, str]] = []
        self.status_code = 200
        self.port = 0

    def update(self, body: bytes, etag: str) -> None:
        self.body, self.etag = body, etag
        self.changed.set()

    async def handle(self, request: web.Request) -> web.Response:
        self.requests.append(dict(request.query))
        if self.status_code != 200:
            return web.Response(status=self.status_code)
        etag = request.query.get("etag")
        if etag is not None and etag == self.etag:
            # Same etag: hold the request until something changes or timeout passes.
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), float(request.query.get("timeout", "30")))
            except asyncio.TimeoutError:
                pass
        return web.Response(body=self.body, content_type="text/xml")


@pytest.fixture
async def fake_bluos():
    fake = FakeBluOS()
    app = web.Application()
    app.router.add_get("/Status", fake.handle)
    app.router.add_get("/SyncStatus", lambda _r: web.Response(body=SYNC_STATUS, content_type="text/xml"))
    server = TestServer(app)
    await server.start_server()
    fake.port = server.port
    yield fake
    await server.close()


@pytest.fixture
async def connected(fake_bluos):
    """BluOSPlayer wired to a real pyblu Player that talks to the fake server."""
    device = BluOSDevice(id="t", name="Test", address="127.0.0.1", port=fake_bluos.port, active_poll_timeout=1)
    player = BluOSPlayer(device, asyncio.get_running_loop())
    pyblu_player = Player("127.0.0.1", fake_bluos.port)
    player._player = pyblu_player
    player._available = True
    player._inputs = [Input(id="input3", text="Bluetooth", image="", url="Capture:bluez:bluetooth")]
    yield player
    await pyblu_player.close()


@pytest.mark.real_status_fetch
class TestOwnStatusLongPoll:
    async def test_first_poll_has_no_etag_and_reads_extras(self, connected, fake_bluos):
        attrs = await connected.poll_status(use_etag=False)

        assert fake_bluos.requests == [{}]
        assert connected._last_etag == "e1"
        assert attrs["stream_quality"] == "320 kbps"
        assert attrs["volume_db"] == -30.5

    async def test_long_poll_waits_for_change(self, connected, fake_bluos):
        await connected.poll_status(use_etag=False)
        connected._state = States.PLAYING  # use the short active_poll_timeout

        poll = asyncio.create_task(connected.poll_status(use_etag=True))
        await asyncio.sleep(0.2)
        assert not poll.done(), "the request must be held open by the player"
        assert fake_bluos.requests[-1] == {"etag": "e1", "timeout": "1"}

        fake_bluos.update(_status_xml("e2", input_id="input3", sleep=30), "e2")
        attrs = await asyncio.wait_for(poll, 2)

        assert connected._last_etag == "e2"
        assert attrs["input_name"] == "Bluetooth"
        assert attrs["service_name"] == "Bluetooth"
        assert attrs["sleep_timer"] == 30

    async def test_long_poll_timeout_without_change_returns_same_status(self, connected, fake_bluos):
        await connected.poll_status(use_etag=False)
        connected._state = States.PLAYING

        attrs = await asyncio.wait_for(connected.poll_status(use_etag=True), 3)

        assert attrs is not None
        assert connected.available
        assert connected._last_etag == "e1"

    async def test_http_error_is_player_error_not_disconnect(self, connected, fake_bluos):
        fake_bluos.status_code = 500
        disconnected = MagicMock()
        connected.events.on(Events.DISCONNECTED, disconnected)

        assert await connected.poll_status(use_etag=False) is None
        assert connected.available
        disconnected.assert_not_called()

    async def test_connection_error_triggers_reconnect(self, connected, fake_bluos):
        connected._schedule_reconnect = MagicMock()
        disconnected = MagicMock()
        connected.events.on(Events.DISCONNECTED, disconnected)
        connected._player._session.get = MagicMock(side_effect=aiohttp.ClientConnectionError("refused"))

        assert await connected.poll_status(use_etag=False) is None
        assert not connected.available
        disconnected.assert_called_once()
        connected._schedule_reconnect.assert_called_once()


class TestFetchStatusErrorMapping:
    @staticmethod
    def _player_raising(exc):
        player = MagicMock()
        player.base_url = "http://h:11000"
        player._session.get = MagicMock(side_effect=exc)
        return player

    @pytest.mark.real_status_fetch
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (TimeoutError(), PlayerUnreachableError),
            (aiohttp.ClientConnectionError(), PlayerUnreachableError),
            (aiohttp.ClientPayloadError(), PlayerError),
        ],
    )
    async def test_errors(self, exc, expected):
        player = BluOSPlayer(BluOSDevice(id="t", name="T", address="h"), asyncio.get_running_loop())
        with pytest.raises(expected):
            await player._fetch_status(self._player_raising(exc), None, 30, 35)

    @pytest.mark.real_status_fetch
    async def test_poll_timeout_must_be_below_request_timeout(self):
        player = BluOSPlayer(BluOSDevice(id="t", name="T", address="h"), asyncio.get_running_loop())
        with pytest.raises(ValueError):
            await player._fetch_status(MagicMock(), "etag", 30, 30)


def _mock_player(**kwargs) -> MagicMock:
    player = MagicMock(spec=BluOSPlayer)
    player.available = True
    player.inputs = [
        Input(id="input3", text="Bluetooth", image="", url="bt"),
        Input(id="input2", text="USB C", image="", url="usb"),
    ]
    player.ensure_connected = AsyncMock(return_value=True)
    player.select_input = AsyncMock(return_value=True)
    player.set_sleep_timer = AsyncMock(return_value=True)
    for key, value in kwargs.items():
        setattr(player, key, value)
    return player


DEVICE = BluOSDevice(id="aa", name="Kitchen", address="10.0.0.1")


class TestStatusAttributes:
    def test_stream_quality_blank_when_stopped(self):
        player = BluOSPlayer(DEVICE, MagicMock())
        player._status_extras = parse_status_extras(NODE_STATUS)
        from pyblu.parse import parse_status

        attrs = player._status_to_attributes(parse_status(NODE_STATUS))
        assert attrs["stream_quality"] == ""
        assert attrs["service_name"] == "TIDAL"
        assert attrs["volume_db"] == 0

    def test_stream_quality_when_paused(self):
        from pyblu.parse import parse_status

        player = BluOSPlayer(DEVICE, MagicMock())
        player._status_extras = parse_status_extras(PULSE_FLEX_STATUS)
        attrs = player._status_to_attributes(parse_status(PULSE_FLEX_STATUS))
        assert attrs["stream_quality"] == "FLAC 16/44.1"
        assert attrs["volume_db"] == -44.3
        assert attrs["input_name"] == ""


class TestInputSelect:
    def test_options_and_current(self):
        entity = BluOSInputSelect(DEVICE, _mock_player())
        changed = entity.update_attributes({"input_name": "USB C"})

        assert entity.id == "bluos_aa_input"
        assert entity.attributes[SelectAttributes.OPTIONS] == ["Bluetooth", "USB C"]
        assert changed[SelectAttributes.CURRENT_OPTION] == "USB C"
        assert changed[SelectAttributes.STATE] == ucapi.select.States.ON

    def test_unchanged_status_pushes_nothing(self):
        entity = BluOSInputSelect(DEVICE, _mock_player())
        entity.update_attributes({"input_name": ""})
        assert entity.update_attributes({"input_name": "", "volume": 10}) == {}

    async def test_select_option_uses_select_input(self):
        player = _mock_player()
        entity = BluOSInputSelect(DEVICE, player)
        entity.update_attributes({})

        status = await entity.command(SelectCommands.SELECT_OPTION, {"option": "USB C"})
        assert status == ucapi.StatusCodes.OK
        player.select_input.assert_awaited_once_with("USB C")

    async def test_next_wraps(self):
        player = _mock_player()
        entity = BluOSInputSelect(DEVICE, player)
        entity.update_attributes({"input_name": "USB C"})

        await entity.command(SelectCommands.SELECT_NEXT)
        player.select_input.assert_awaited_once_with("Bluetooth")

    async def test_unknown_option_is_bad_request(self):
        entity = BluOSInputSelect(DEVICE, _mock_player())
        status = await entity.command(SelectCommands.SELECT_OPTION, {"option": "Optical"})
        assert status == ucapi.StatusCodes.BAD_REQUEST

    async def test_no_inputs_is_unavailable(self):
        entity = BluOSInputSelect(DEVICE, _mock_player(inputs=[]))
        assert await entity.command(SelectCommands.SELECT_FIRST) == ucapi.StatusCodes.SERVICE_UNAVAILABLE

    async def test_not_connected_is_unavailable(self):
        player = _mock_player()
        player.ensure_connected = AsyncMock(return_value=False)
        entity = BluOSInputSelect(DEVICE, player)
        assert await entity.command(SelectCommands.SELECT_FIRST) == ucapi.StatusCodes.SERVICE_UNAVAILABLE


class TestSleepTimerSelect:
    def test_options(self):
        entity = BluOSSleepTimerSelect(DEVICE, _mock_player())
        assert entity.attributes[SelectAttributes.OPTIONS] == ["Off", "15 min", "30 min", "45 min", "60 min", "90 min"]

    @pytest.mark.parametrize(
        ("minutes", "label"),
        [(0, "Off"), (None, "Off"), (15, "15 min"), (23, "30 min"), (90, "90 min"), (120, "90 min")],
    )
    def test_current_maps_to_covering_step(self, minutes, label):
        entity = BluOSSleepTimerSelect(DEVICE, _mock_player())
        assert entity.update_attributes({"sleep_timer": minutes})[SelectAttributes.CURRENT_OPTION] == label

    def test_countdown_within_step_pushes_nothing(self):
        entity = BluOSSleepTimerSelect(DEVICE, _mock_player())
        entity.update_attributes({"sleep_timer": 30})
        assert entity.update_attributes({"sleep_timer": 29}) == {}

    async def test_select_sets_minutes(self):
        player = _mock_player()
        entity = BluOSSleepTimerSelect(DEVICE, player)
        await entity.command(SelectCommands.SELECT_OPTION, {"option": "45 min"})
        player.set_sleep_timer.assert_awaited_once_with(45)

    async def test_select_off(self):
        player = _mock_player()
        entity = BluOSSleepTimerSelect(DEVICE, player)
        entity.update_attributes({"sleep_timer": 15})
        await entity.command(SelectCommands.SELECT_FIRST)
        player.set_sleep_timer.assert_awaited_once_with(0)


class TestPlayerSetters:
    @pytest.fixture
    def player(self):
        player = BluOSPlayer(DEVICE, MagicMock())
        player._player = MagicMock()
        player._available = True
        player._schedule_poll = MagicMock()
        return player

    @pytest.mark.parametrize("target", SLEEP_TIMER_STEPS)
    async def test_set_sleep_timer_steps_until_target(self, player, target):
        # The real cycle: 0 -> 15 -> 30 -> 45 -> 60 -> 90 -> 0
        cycle = iter([15, 30, 45, 60, 90, 0])
        player._player.sleep_timer = AsyncMock(side_effect=lambda: next(cycle))
        player._sleep_timer = 0

        assert await player.set_sleep_timer(target) is True
        assert player.sleep_timer == target
        assert player._player.sleep_timer.await_count == SLEEP_TIMER_STEPS.index(target)

    async def test_set_sleep_timer_from_countdown_value(self, player):
        # Status reported 23 left of 30; the next step after 30 is 45.
        cycle = iter([45, 60, 90, 0, 15])
        player._player.sleep_timer = AsyncMock(side_effect=lambda: next(cycle))
        player._sleep_timer = 23

        assert await player.set_sleep_timer(15) is True
        assert player._player.sleep_timer.await_count == 5

    async def test_set_sleep_timer_rejects_unknown_value(self, player):
        player._player.sleep_timer = AsyncMock()
        assert await player.set_sleep_timer(20) is False
        player._player.sleep_timer.assert_not_awaited()

    async def test_set_sleep_timer_gives_up_when_player_never_matches(self, player):
        player._player.sleep_timer = AsyncMock(return_value=15)
        assert await player.set_sleep_timer(30) is False
        assert player._player.sleep_timer.await_count == len(SLEEP_TIMER_STEPS)

    async def test_select_input_plays_input_url_not_preset(self, player):
        player._inputs = [Input(id="input3", text="Bluetooth", image="", url="Capture:bluez:bluetooth")]
        player._presets = [MagicMock(name="Bluetooth", id=1)]
        player._player.play_url = AsyncMock()
        player._player.load_preset = AsyncMock()

        assert await player.select_input("Bluetooth") is True
        player._player.play_url.assert_awaited_once_with("Capture:bluez:bluetooth")
        player._player.load_preset.assert_not_awaited()

    async def test_select_unknown_input(self, player):
        player._inputs = []
        assert await player.select_input("Optical") is False


class TestSensors:
    def test_source_sensor(self):
        entity = BluOSSourceSensor(DEVICE, _mock_player())
        assert entity.update_attributes({"service_name": "TIDAL"}) == {
            SensorAttributes.STATE: ucapi.sensor.States.ON,
            SensorAttributes.VALUE: "TIDAL",
        }
        assert entity.update_attributes({"service_name": "TIDAL", "media_position": 5}) == {}

    def test_volume_sensor_unit_and_value(self):
        entity = BluOSVolumeSensor(DEVICE, _mock_player())
        assert entity.attributes[SensorAttributes.UNIT] == "dB"
        assert entity.options["custom_unit"] == "dB"
        assert entity.update_attributes({"volume_db": -44.3})[SensorAttributes.VALUE] == -44.3

    def test_volume_sensor_ignores_non_numbers(self):
        entity = BluOSVolumeSensor(DEVICE, _mock_player())
        entity.update_attributes({"volume_db": -20.0})
        assert entity.update_attributes({"volume_db": None})[SensorAttributes.VALUE] == ""

    def test_stream_quality_sensor_only_pushes_on_change(self):
        entity = BluOSStreamQualitySensor(DEVICE, _mock_player())
        assert entity.update_attributes({"stream_quality": "FLAC 16/44.1"})[SensorAttributes.VALUE] == "FLAC 16/44.1"
        # Same track keeps polling: nothing to push.
        assert entity.update_attributes({"stream_quality": "FLAC 16/44.1", "media_position": 99}) == {}
        assert entity.update_attributes({"stream_quality": "MQA"}) == {SensorAttributes.VALUE: "MQA"}

    def test_unavailable_player(self):
        entity = BluOSStreamQualitySensor(DEVICE, _mock_player(available=False))
        changed = entity.update_attributes({"stream_quality": "MQA"})
        assert changed == {}  # already UNAVAILABLE / "" from construction
        entity.attributes[SensorAttributes.STATE] = ucapi.sensor.States.ON
        assert entity.set_unavailable() == {
            SensorAttributes.STATE: ucapi.sensor.States.UNAVAILABLE,
            SensorAttributes.VALUE: "",
        }

    def test_force_update_after_resubscribe(self):
        entity = BluOSSourceSensor(DEVICE, _mock_player())
        entity.update_attributes({"service_name": "TIDAL"})
        entity.clear_cached_attributes()
        assert entity.update_attributes({"service_name": "TIDAL"})[SensorAttributes.VALUE] == "TIDAL"
