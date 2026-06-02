"""UC Media Player entity for BluOS devices."""

import hashlib
import logging
from collections import OrderedDict
from typing import Any, Callable

import ucapi
from bluos import BluOSPlayer
from bluos import RepeatMode as BluOSRepeatMode
from bluos import States as BluOSStates
from config import BluOSDevice
from ucapi.api_definitions import StatusCodes
from ucapi.media_player import (
    Attributes,
    BrowseMediaItem,
    BrowseOptions,
    BrowseResults,
    Commands,
    DeviceClasses,
    Features,
    Options,
    Pagination,
    RepeatMode,
    SearchOptions,
    SearchResults,
    States,
)

_LOG = logging.getLogger(__name__)

_UC_STATE_MAP: dict[BluOSStates, States] = {
    BluOSStates.UNKNOWN: States.UNKNOWN,
    BluOSStates.UNAVAILABLE: States.UNAVAILABLE,
    BluOSStates.OFF: States.OFF,
    BluOSStates.ON: States.ON,
    BluOSStates.PLAYING: States.PLAYING,
    BluOSStates.PAUSED: States.PAUSED,
    BluOSStates.STOPPED: States.ON,
    BluOSStates.BUFFERING: States.BUFFERING,
}

_UC_REPEAT_MAP: dict[BluOSRepeatMode, RepeatMode] = {
    BluOSRepeatMode.OFF: RepeatMode.OFF,
    BluOSRepeatMode.ALL: RepeatMode.ALL,
    BluOSRepeatMode.ONE: RepeatMode.ONE,
}

_REPEAT_COMMAND_MAP: dict[RepeatMode, BluOSRepeatMode] = {
    RepeatMode.OFF: BluOSRepeatMode.OFF,
    RepeatMode.ALL: BluOSRepeatMode.ALL,
    RepeatMode.ONE: BluOSRepeatMode.ONE,
}

# Multi-room grouping simple commands. GROUP_TOGGLE_<room name> is generated per
# other configured player and toggles that room in/out of this player's group.
GROUP_TOGGLE_PREFIX = "GROUP_TOGGLE_"
GROUP_ALL_CMD = "GROUP_ALL"
UNGROUP_ALL_CMD = "UNGROUP_ALL"
LEAVE_GROUP_CMD = "LEAVE_GROUP"

# Features supported by BluOS players
BLUOS_FEATURES = [
    Features.ON_OFF,
    Features.TOGGLE,
    Features.VOLUME,
    Features.VOLUME_UP_DOWN,
    Features.MUTE_TOGGLE,
    Features.MUTE,
    Features.UNMUTE,
    Features.PLAY_PAUSE,
    Features.STOP,
    Features.NEXT,
    Features.PREVIOUS,
    Features.FAST_FORWARD,
    Features.REWIND,
    Features.SHUFFLE,
    Features.REPEAT,
    Features.SEEK,
    Features.SELECT_SOURCE,
    Features.MEDIA_TITLE,
    Features.MEDIA_ARTIST,
    Features.MEDIA_ALBUM,
    Features.MEDIA_IMAGE_URL,
    Features.MEDIA_DURATION,
    Features.MEDIA_POSITION,
    Features.BROWSE_MEDIA,
    Features.SEARCH_MEDIA,
    Features.PLAY_MEDIA,
    Features.CLEAR_PLAYLIST,
]

# Mapping from BluOS item type to UC MediaClass
_BLUOS_TYPE_TO_MEDIA_CLASS = {
    "link": "directory",
    "audio": "track",
    "artist": "artist",
    "composer": "composer",
    "album": "album",
    "playlist": "playlist",
    "track": "track",
    "folder": "directory",
    "section": "directory",
    "category": "directory",
    "text": "directory",
}

# Mapping from BluOS item type to UC MediaContentType
_BLUOS_TYPE_TO_CONTENT_TYPE = {
    "link": "music",
    "audio": "radio",
    "artist": "artist",
    "composer": "artist",
    "album": "album",
    "playlist": "playlist",
    "track": "track",
    "folder": "music",
    "section": "music",
    "category": "music",
    "text": "music",
}

# Seek step for fast forward/rewind in seconds
SEEK_STEP = 10

# Maximum entries in browse-related caches. Prevents unbounded memory growth
# when browsing large libraries on the embedded Remote hardware.
_BROWSE_CACHE_MAX = 500


class _LRUCache(OrderedDict):
    """OrderedDict-based LRU cache with a fixed maximum size."""

    def __init__(self, maxsize: int):
        super().__init__()
        self._maxsize = maxsize

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self._maxsize:
            self.popitem(last=False)


class BluOSMediaPlayer(ucapi.MediaPlayer):
    """Media player entity for BluOS devices."""

    def __init__(
        self,
        device: BluOSDevice,
        player: BluOSPlayer,
        group_targets: Callable[[], list[BluOSPlayer]] | None = None,
    ):
        """
        Initialize BluOS media player entity.

        Args:
            device: Device configuration
            player: BluOS player wrapper
            group_targets: Callable returning the other configured players that
                this player can be grouped with. Used to generate the per-room
                GROUP_TOGGLE_* simple commands. Defaults to no targets.
        """
        entity_id = f"bluos_{device.id}"
        name = device.name

        # group_targets must be set before building options, which include the
        # per-room grouping commands derived from the other configured players.
        self._group_targets = group_targets

        # Build options with simple commands for presets and grouping
        options = {Options.SIMPLE_COMMANDS: self._build_simple_commands(player)}

        super().__init__(
            entity_id,
            name,
            features=BLUOS_FEATURES,
            attributes={
                Attributes.STATE: States.UNAVAILABLE,
                Attributes.VOLUME: 0,
                Attributes.MUTED: False,
                Attributes.MEDIA_TITLE: "",
                Attributes.MEDIA_ARTIST: "",
                Attributes.MEDIA_ALBUM: "",
                Attributes.MEDIA_IMAGE_URL: "",
                Attributes.MEDIA_DURATION: 0,
                Attributes.MEDIA_POSITION: 0,
                Attributes.SHUFFLE: False,
                Attributes.REPEAT: RepeatMode.OFF,
                Attributes.SOURCE: "",
                Attributes.SOURCE_LIST: [],
            },
            device_class=DeviceClasses.SPEAKER,
            options=options,
        )

        self._device = device
        self._player = player
        # When True, the next update_attributes() call pushes every computed
        # value regardless of the diff. Set by clear_cached_attributes() after a
        # (re)subscribe or standby exit, when the Remote may have dropped state.
        self._force_update: bool = False
        self._last_search_key: str | None = None
        # Maps browseKey → playURL for items that are both browsable and playable
        self._play_url_cache: _LRUCache = _LRUCache(_BROWSE_CACHE_MAX)
        # Maps short hash ID → full browse key for keys that exceed the 255-char media_id limit
        self._browse_id_cache: _LRUCache = _LRUCache(_BROWSE_CACHE_MAX)

    @property
    def player(self) -> BluOSPlayer:
        """Return the BluOS player wrapper."""
        return self._player

    @property
    def simple_commands(self) -> list[str]:
        """Current preset/utility/grouping simple commands (excludes transport)."""
        return list(self.options.get(Options.SIMPLE_COMMANDS, []))

    def update_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        Compute entity attributes from a BluOS status update and return only
        those that differ from the current entity state.

        State is diffed against ``self.attributes`` — the dict ucapi keeps in
        sync with the Remote whenever an update is pushed — so there is no
        separate shadow cache that can drift out of sync. The diff is written
        back into ``self.attributes`` so later reads (command handling,
        ``set_unavailable``) observe the current state; the driver still pushes
        the returned diff to the Remote. When ``_force_update`` is set (after a
        resubscribe or standby exit) every computed value is returned.

        Args:
            attributes: New attributes from BluOS player

        Returns:
            Dictionary of changed attributes
        """
        state = self._map_state(attributes.get("state", BluOSStates.UNKNOWN))

        computed: dict[str, Any] = {
            Attributes.STATE: state,
            Attributes.SOURCE_LIST: self._player.get_source_list(),
        }

        attr_mapping = {
            "volume": Attributes.VOLUME,
            "muted": Attributes.MUTED,
            "media_title": Attributes.MEDIA_TITLE,
            "media_artist": Attributes.MEDIA_ARTIST,
            "media_album": Attributes.MEDIA_ALBUM,
            "media_image_url": Attributes.MEDIA_IMAGE_URL,
            "media_duration": Attributes.MEDIA_DURATION,
            "media_position": Attributes.MEDIA_POSITION,
            "shuffle": Attributes.SHUFFLE,
            "source": Attributes.SOURCE,
        }
        for bluos_attr, uc_attr in attr_mapping.items():
            value = attributes.get(bluos_attr)
            if value is not None:
                computed[uc_attr] = value

        # Map repeat mode from BluOS to UC
        repeat = attributes.get("repeat")
        if repeat is not None:
            computed[Attributes.REPEAT] = self._map_repeat_mode(repeat)

        # Clear media info when not playing
        if state in (States.OFF, States.STANDBY, States.UNAVAILABLE):
            for attr in (
                Attributes.MEDIA_TITLE,
                Attributes.MEDIA_ARTIST,
                Attributes.MEDIA_ALBUM,
                Attributes.MEDIA_IMAGE_URL,
            ):
                computed[attr] = ""

        # An explicit skip/seek command pops MEDIA_POSITION from self.attributes
        # to force a refresh; capture that (and the forced-resync flag, which
        # _diff_attributes consumes) before diffing.
        position_invalidated = Attributes.MEDIA_POSITION not in self.attributes
        force_full = self._force_update

        changed = self._diff_attributes(computed)

        # Force a position update on track change, even when the numeric value
        # happens to match, so the Remote restarts its progress bar.
        if Attributes.MEDIA_TITLE in changed:
            position = attributes.get("media_position")
            if position is not None:
                changed[Attributes.MEDIA_POSITION] = position

        # Progress-bar throttle: media_position advances on every poll, and
        # pushing it each time needlessly wakes the Remote from low-power — the
        # Remote interpolates the bar itself between updates. Drop a bare
        # position advance so a position-only poll produces no push at all; only
        # let it through when the Remote genuinely needs to reposition the bar:
        # a forced resync, a track change, a play/pause/stop transition, or an
        # explicit skip/seek. self.attributes still tracks the real position so
        # FAST_FORWARD/REWIND/SEEK math stays correct.
        if Attributes.MEDIA_POSITION in changed and not (
            force_full or Attributes.MEDIA_TITLE in changed or Attributes.STATE in changed or position_invalidated
        ):
            self.attributes[Attributes.MEDIA_POSITION] = changed.pop(Attributes.MEDIA_POSITION)

        # Persist the diff locally so command handling and set_unavailable read
        # current state; the driver pushes `changed` to the Remote, which writes
        # the same values again, harmlessly.
        self.attributes.update(changed)
        return changed

    def _diff_attributes(self, computed: dict[str, Any]) -> dict[str, Any]:
        """
        Return the subset of ``computed`` that differs from ``self.attributes``.

        When ``_force_update`` is set, every computed value is returned and the
        flag is reset, forcing a full resync to the Remote.
        """
        if self._force_update:
            self._force_update = False
            return dict(computed)
        return {key: value for key, value in computed.items() if self.attributes.get(key) != value}

    def set_unavailable(self) -> dict[str, Any]:
        """Mark entity as unavailable and return changed attributes."""
        if self.attributes.get(Attributes.STATE) != States.UNAVAILABLE:
            self.attributes[Attributes.STATE] = States.UNAVAILABLE
            return {Attributes.STATE: States.UNAVAILABLE}
        return {}

    def clear_cached_attributes(self) -> None:
        """
        Force the next update_attributes() call to push all values.

        Used after a (re)subscribe or standby exit, when the Remote may have
        dropped our state. State is diffed against ``self.attributes`` directly,
        so there is no separate cache to clear.
        """
        self._force_update = True

    def update_options(self) -> dict[str, Any]:
        """Update and return entity options with current simple commands."""
        self.options = {Options.SIMPLE_COMMANDS: self._build_simple_commands(self._player)}
        return self.options

    def _build_simple_commands(self, player: BluOSPlayer) -> list[str]:
        """Combine the player's preset/utility commands with grouping commands."""
        commands = player.get_simple_commands() + self._grouping_commands()
        if _LOG.isEnabledFor(logging.DEBUG):
            targets = self._get_targets()
            _LOG.debug(
                "Simple commands for %s: %d group target(s) %s -> %s",
                player.device.name,
                len(targets),
                [t.device.name for t in targets],
                commands,
            )
        return commands

    def _get_targets(self) -> list[BluOSPlayer]:
        """Other configured players this one can be grouped with."""
        return list(self._group_targets()) if self._group_targets else []

    def _grouping_commands(self) -> list[str]:
        """Generate the multi-room grouping simple commands."""
        targets = self._get_targets()
        commands = [f"{GROUP_TOGGLE_PREFIX}{target.device.name}" for target in targets]
        if targets:
            commands.append(GROUP_ALL_CMD)
        commands.append(UNGROUP_ALL_CMD)
        commands.append(LEAVE_GROUP_CMD)
        return commands

    def _find_target_by_name(self, name: str) -> BluOSPlayer | None:
        """Resolve a room name to one of the groupable target players."""
        for target in self._get_targets():
            if target.device.name == name:
                return target
        return None

    def _find_target_by_endpoint(self, ip: str, port: int) -> BluOSPlayer | None:
        """Resolve an ip:port endpoint to one of the groupable target players."""
        for target in self._get_targets():
            if target.device.address == ip and target.device.port == port:
                return target
        return None

    async def _handle_group_command(self, cmd_id: str) -> ucapi.StatusCodes | None:
        """
        Handle a multi-room grouping command.

        Returns a status code if ``cmd_id`` is a grouping command, or None if it
        is not (so the caller can continue normal dispatch).
        """
        if cmd_id == GROUP_ALL_CMD:
            ok = True
            for target in self._get_targets():
                if target.available:
                    ok = await self._player.group_with(target) and ok
            return ucapi.StatusCodes.OK if ok else ucapi.StatusCodes.SERVER_ERROR

        if cmd_id == UNGROUP_ALL_CMD:
            ok = await self._player.ungroup_all()
            return ucapi.StatusCodes.OK if ok else ucapi.StatusCodes.SERVER_ERROR

        if cmd_id == LEAVE_GROUP_CMD:
            sync = self._player.sync_status
            if not sync or not sync.leader:
                return ucapi.StatusCodes.OK  # not a follower, nothing to do
            leader = self._find_target_by_endpoint(sync.leader.ip, sync.leader.port)
            if leader is None:
                _LOG.warning("LEAVE_GROUP: leader %s is not managed by this integration", sync.leader.ip)
                return ucapi.StatusCodes.SERVICE_UNAVAILABLE
            ok = await self._player.leave_group(leader)
            return ucapi.StatusCodes.OK if ok else ucapi.StatusCodes.SERVER_ERROR

        if cmd_id.startswith(GROUP_TOGGLE_PREFIX):
            room = cmd_id[len(GROUP_TOGGLE_PREFIX) :]
            target = self._find_target_by_name(room)
            if target is None:
                _LOG.warning("GROUP_TOGGLE: unknown room '%s'", room)
                return ucapi.StatusCodes.BAD_REQUEST
            if await self._player.is_grouped_with(target):
                ok = await self._player.ungroup(target)
            else:
                ok = await self._player.group_with(target)
            return ucapi.StatusCodes.OK if ok else ucapi.StatusCodes.SERVER_ERROR

        return None

    @staticmethod
    def _map_state(bluos_state: BluOSStates) -> States:
        """Map BluOS state to UC state."""
        return _UC_STATE_MAP.get(bluos_state, States.UNKNOWN)

    @staticmethod
    def _map_repeat_mode(bluos_repeat: BluOSRepeatMode) -> RepeatMode:
        """Map BluOS repeat mode to UC repeat mode."""
        return _UC_REPEAT_MAP.get(bluos_repeat, RepeatMode.OFF)

    def _media_id_for_browse_key(self, browse_key: str) -> str:
        """Return a media_id for a browse key, shortening it if it exceeds 255 chars."""
        if len(browse_key) <= 255:
            return browse_key
        short_id = "_bk_" + hashlib.sha256(browse_key.encode()).hexdigest()[:16]
        self._browse_id_cache[short_id] = browse_key
        return short_id

    def _bluos_item_to_browse_item(self, item: dict[str, Any]) -> BrowseMediaItem:
        """Convert a BluOS browse item dict to a UC BrowseMediaItem."""
        bluos_type = item.get("type", "link")
        media_class = _BLUOS_TYPE_TO_MEDIA_CLASS.get(bluos_type, "directory")
        media_type = _BLUOS_TYPE_TO_CONTENT_TYPE.get(bluos_type, "music")

        browse_key = item.get("browse_key")
        play_url = item.get("play_url") or item.get("autoplay_url")

        # Use browseKey as media_id for browsable items, playURL for play-only items
        if browse_key:
            media_id = self._media_id_for_browse_key(browse_key)
            # When an item can both be browsed AND played, cache its play URL so that
            # PLAY_MEDIA can resolve the correct endpoint (browseKey ≠ a play URL).
            if play_url:
                self._play_url_cache[browse_key] = play_url
        elif play_url:
            media_id = play_url
        else:
            media_id = item.get("text", "")

        thumbnail = self._player.get_absolute_image_url(item.get("image")) or None

        # Convert nested items (categories)
        sub_items = None
        if "items" in item and item["items"]:
            sub_items = [self._bluos_item_to_browse_item(sub) for sub in item["items"]]

        return BrowseMediaItem(
            title=item.get("text", ""),
            media_class=media_class,
            media_type=media_type,
            media_id=media_id,
            can_browse=browse_key is not None,
            can_play=play_url is not None,
            subtitle=item.get("text2"),
            thumbnail=thumbnail,
            items=sub_items,
        )

    async def browse(self, options: BrowseOptions) -> BrowseResults | StatusCodes:
        """Browse BluOS music content."""
        _LOG.debug("Browse request: media_id=%s, paging=%s", options.media_id, options.paging)

        browse_key = self._browse_id_cache.get(options.media_id, options.media_id)
        raw = await self._player.browse(key=browse_key)

        if "error" in raw and raw["error"]:
            _LOG.warning("Browse error: %s", raw["error"])
            return StatusCodes.SERVER_ERROR

        items = [self._bluos_item_to_browse_item(item) for item in raw.get("items", [])]

        # Store search_key for later use by search()
        if raw.get("search_key"):
            self._last_search_key = raw["search_key"]

        # Build the container item
        service_name = raw.get("service_name") or "BluOS"
        container = BrowseMediaItem(
            title=service_name,
            media_class="directory",
            media_type="music",
            media_id=options.media_id or "root",
            can_browse=True,
            items=items,
        )

        return BrowseResults(
            media=container,
            pagination=Pagination(page=1, limit=len(items), count=len(items)),
        )

    async def _find_search_key(self) -> str | None:
        """Auto-discover a search key by browsing into available services."""
        root = await self._player.browse(key=None)
        if "error" in root:
            return None
        for item in root.get("items", []):
            browse_key = item.get("browse_key")
            if not browse_key:
                continue
            service_result = await self._player.browse(key=browse_key)
            if search_key := service_result.get("search_key"):
                _LOG.debug("Auto-discovered search key from '%s': %s", item.get("text"), search_key)
                return search_key
        return None

    async def search(self, options: SearchOptions) -> SearchResults | StatusCodes:
        """Search BluOS music content."""
        _LOG.debug("Search request: query=%s, media_id=%s", options.query, options.media_id)

        # Use provided media_id as search_key, fall back to last known, or auto-discover
        search_key = options.media_id or self._last_search_key
        if not search_key:
            _LOG.debug("No search key cached, auto-discovering from available services")
            search_key = await self._find_search_key()
        if not search_key:
            _LOG.warning("No search key available — no searchable services found")
            return SearchResults(media=[], pagination=Pagination(page=1, limit=0, count=0))

        raw = await self._player.search(search_key=search_key, query=options.query)

        if "error" in raw and raw["error"]:
            _LOG.warning("Search error: %s", raw["error"])
            return StatusCodes.SERVER_ERROR

        items = [self._bluos_item_to_browse_item(item) for item in raw.get("items", [])]

        return SearchResults(
            media=items,
            pagination=Pagination(page=1, limit=len(items), count=len(items)),
        )

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any = None
    ) -> ucapi.StatusCodes:
        """
        Handle media player commands.

        Args:
            cmd_id: Command identifier
            params: Command parameters

        Returns:
            Status code indicating success or failure
        """
        params = params or {}
        _LOG.debug("Command %s with params %s for %s", cmd_id, params, self._device.name)

        if not self._player.available:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        # Check if it's a simple preset command
        if cmd_id.startswith("PRESET_"):
            result = await self._player.load_preset_by_command(cmd_id)
            return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR

        # Handle refresh presets command
        if cmd_id == "REFRESH_PRESETS":
            result = await self._player.refresh_presets()
            if result:
                # Update options with new preset commands
                self.update_options()
                # Clear cached source list so next poll sends update to UC Remote
                self.attributes.pop(Attributes.SOURCE_LIST, None)
                return ucapi.StatusCodes.OK
            return ucapi.StatusCodes.SERVER_ERROR

        # Handle shuffle toggle command
        if cmd_id == "SHUFFLE_TOGGLE":
            current = self.attributes.get(Attributes.SHUFFLE, False)
            result = await self._player.set_shuffle(not current)
            return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR

        # Handle repeat toggle command (cycles: OFF -> ALL -> ONE -> OFF)
        if cmd_id == "REPEAT_TOGGLE":
            result = await self._player.toggle_repeat()
            return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR

        # Handle sleep timer command (cycles: 15 -> 30 -> 45 -> 60 -> 90 -> off)
        if cmd_id == "SLEEP_TIMER":
            new_timer = await self._player.toggle_sleep_timer()
            _LOG.info("Sleep timer set to %d minutes", new_timer)
            return ucapi.StatusCodes.OK

        # Handle multi-room grouping commands
        group_result = await self._handle_group_command(cmd_id)
        if group_result is not None:
            return group_result

        result = False

        match cmd_id:
            case Commands.ON:
                # BluOS has no power control, start playback instead
                result = await self._player.play()

            case Commands.OFF:
                # BluOS has no power control, stop playback instead
                result = await self._player.stop()

            case Commands.TOGGLE | Commands.PLAY_PAUSE:
                if self.attributes.get(Attributes.STATE) == States.PLAYING:
                    result = await self._player.pause()
                else:
                    result = await self._player.play()

            case Commands.STOP:
                result = await self._player.stop()

            case Commands.NEXT:
                result = await self._player.next_track()
                # Clear position cache to force update on next poll
                self.attributes.pop(Attributes.MEDIA_POSITION, None)

            case Commands.PREVIOUS:
                result = await self._player.previous_track()
                # Clear position cache to force update on next poll
                self.attributes.pop(Attributes.MEDIA_POSITION, None)

            case Commands.FAST_FORWARD:
                current_pos = self.attributes.get(Attributes.MEDIA_POSITION, 0)
                duration = self.attributes.get(Attributes.MEDIA_DURATION, 0)
                if duration:
                    new_pos = min(current_pos + SEEK_STEP, duration)
                    result = await self._player.seek(int(new_pos))
                    self.attributes.pop(Attributes.MEDIA_POSITION, None)
                else:
                    result = True  # no-op for streams with unknown duration

            case Commands.REWIND:
                current_pos = self.attributes.get(Attributes.MEDIA_POSITION, 0)
                duration = self.attributes.get(Attributes.MEDIA_DURATION, 0)
                if duration:
                    new_pos = max(current_pos - SEEK_STEP, 0)
                    result = await self._player.seek(int(new_pos))
                    self.attributes.pop(Attributes.MEDIA_POSITION, None)
                else:
                    result = True  # no-op for streams with unknown duration

            case Commands.VOLUME:
                volume = params.get("volume")
                if volume is not None:
                    result = await self._player.set_volume(int(volume))

            case Commands.VOLUME_UP:
                result = await self._player.volume_up()

            case Commands.VOLUME_DOWN:
                result = await self._player.volume_down()

            case Commands.MUTE_TOGGLE:
                result = await self._player.toggle_mute()

            case Commands.MUTE:
                result = await self._player.mute(True)

            case Commands.UNMUTE:
                result = await self._player.mute(False)

            case Commands.SHUFFLE:
                # Set shuffle mode from parameter
                shuffle = params.get("shuffle", False)
                result = await self._player.set_shuffle(bool(shuffle))

            case Commands.REPEAT:
                repeat_param = params.get("repeat", "OFF")
                bluos_mode = _REPEAT_COMMAND_MAP.get(repeat_param, BluOSRepeatMode.OFF)
                result = await self._player.set_repeat(bluos_mode)

            case Commands.SEEK:
                position = params.get("media_position")
                if position is not None:
                    result = await self._player.seek(int(position))
                    # Clear position cache to force update on next poll
                    self.attributes.pop(Attributes.MEDIA_POSITION, None)

            case Commands.SELECT_SOURCE:
                source = params.get("source")
                if source:
                    result = await self._player.select_source(source)

            case Commands.PLAY_MEDIA:
                media_id = params.get("media_id")
                if media_id:
                    # Resolve any short hash ID back to the full browse key first
                    resolved_id = self._browse_id_cache.get(media_id, media_id)
                    # Browsable items (albums, playlists) use their browseKey as media_id
                    # for navigation, but need a different URL to actually play.
                    play_url = self._play_url_cache.get(resolved_id, resolved_id)
                    result = await self._player.play_browse_item(play_url)
                else:
                    result = False

            case Commands.CLEAR_PLAYLIST:
                result = await self._player.clear_queue()

            case _:
                _LOG.warning("Unsupported command: %s", cmd_id)
                return ucapi.StatusCodes.NOT_IMPLEMENTED

        return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR
