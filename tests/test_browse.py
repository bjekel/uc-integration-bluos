"""Tests for browse and search media functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from bluos import BluOSPlayer
from config import BluOSDevice
from media_player import BluOSMediaPlayer
from ucapi.api_definitions import StatusCodes
from ucapi.media_player import BrowseOptions, BrowseResults, Pagination, SearchOptions, SearchResults

# Sample XML responses from BluOS API

TOP_LEVEL_BROWSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<browse sid="16" type="menu">
    <item image="/images/ci_myplaylists.png" browseKey="playlists" text="Playlists" type="link"/>
    <item image="/images/LibraryIcon.png" browseKey="LocalMusic:" text="Library" type="link"/>
    <item image="/images/InputIcon.png" text="Optical Input"
        playURL="/Play?url=Capture%3Ahw%3A1%2C0%2F1%2F25%2F2%2Finput1" inputType="spdif" type="audio"/>
    <item image="/Sources/images/TuneInIcon.png" browseKey="TuneIn:" text="TuneIn" type="link"/>
    <item image="/Sources/images/TidalIcon.png" browseKey="Tidal:" text="TIDAL" type="link"/>
</browse>
"""

SERVICE_BROWSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<browse sid="16" serviceIcon="/Sources/images/TidalIcon.png" serviceName="TIDAL"
    service="Tidal" searchKey="Tidal:Search" type="menu">
    <item browseKey="/Playlists?service=Tidal&amp;genre=0&amp;category=toplist" text="Popular Playlists" type="link"/>
    <item browseKey="/Artists?service=Tidal&amp;genre=0&amp;category=toplist" text="Popular Artists" type="link"/>
    <item browseKey="/Albums?service=Tidal&amp;genre=0&amp;category=toplist" text="Popular Albums" type="link"/>
</browse>
"""

ALBUMS_BROWSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<browse sid="16" serviceIcon="/Sources/images/TidalIcon.png" serviceName="TIDAL"
    service="Tidal" searchKey="Tidal:Search" type="albums"
    nextKey="/Albums?service=Tidal&amp;category=toplist&amp;start=30&amp;end=59">
    <item text="Album One" text2="Artist One" type="album"
        browseKey="Tidal:Album?albumid=123" playURL="/Add?service=Tidal&amp;albumid=123&amp;playnow=1"
        image="/Artwork?service=Tidal&amp;albumid=123"/>
    <item text="Album Two" text2="Artist Two" type="album"
        browseKey="Tidal:Album?albumid=456" playURL="/Add?service=Tidal&amp;albumid=456&amp;playnow=1"
        image="/Artwork?service=Tidal&amp;albumid=456"/>
</browse>
"""

TRACKS_BROWSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<browse sid="16" serviceName="TIDAL" type="tracks">
    <item text="Song One" text2="Artist One" type="track"
        playURL="/Add?service=Tidal&amp;songid=789&amp;playnow=1"
        autoplayURL="/Add?service=Tidal&amp;songid=789&amp;playnow=1&amp;autoplay=1"
        image="/Artwork?service=Tidal&amp;songid=789"/>
</browse>
"""

SEARCH_RESULTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<browse sid="16" serviceIcon="/Sources/images/TidalIcon.png" serviceName="TIDAL"
    service="Tidal" searchKey="Tidal:Search" type="menu">
    <item browseKey="/Artists?service=Tidal&amp;expr=test" text="Artists" type="link"/>
    <item browseKey="/Albums?service=Tidal&amp;expr=test" text="Albums" type="link"/>
    <item browseKey="/Songs?service=Tidal&amp;expr=test" text="Songs" type="link"/>
</browse>
"""

ERROR_BROWSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<error>
    <message>Service not available</message>
</error>
"""

CATEGORY_BROWSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<browse sid="16" type="menu">
    <category text="Rock">
        <item browseKey="genre:rock:1" text="Classic Rock" type="link"/>
        <item browseKey="genre:rock:2" text="Alternative Rock" type="link"/>
    </category>
    <category text="Jazz">
        <item browseKey="genre:jazz:1" text="Smooth Jazz" type="link"/>
    </category>
</browse>
"""


class TestBluOSPlayerBrowse:
    """Tests for BluOSPlayer browse methods."""

    @pytest.fixture
    def device(self):
        return BluOSDevice(
            id="test_device",
            name="Test Player",
            address="192.168.1.100",
            port=11000,
        )

    @pytest.fixture
    def loop(self):
        return asyncio.new_event_loop()

    @pytest.fixture
    def player(self, device, loop):
        return BluOSPlayer(device, loop)

    def test_parse_browse_xml_top_level(self, player):
        """Test parsing top-level browse XML."""
        result = player._parse_browse_xml(TOP_LEVEL_BROWSE_XML)
        assert len(result["items"]) == 5
        assert result["browse_type"] == "menu"
        assert result["next_key"] is None
        assert result["search_key"] is None

        # Check first item (browsable link)
        item = result["items"][0]
        assert item["text"] == "Playlists"
        assert item["type"] == "link"
        assert item["browse_key"] == "playlists"
        assert item["play_url"] is None

        # Check audio input item
        audio = result["items"][2]
        assert audio["text"] == "Optical Input"
        assert audio["type"] == "audio"
        assert audio["browse_key"] is None
        assert audio["play_url"] is not None
        assert audio["input_type"] == "spdif"

    def test_parse_browse_xml_service(self, player):
        """Test parsing service-level browse XML."""
        result = player._parse_browse_xml(SERVICE_BROWSE_XML)
        assert result["service_name"] == "TIDAL"
        assert result["search_key"] == "Tidal:Search"
        assert len(result["items"]) == 3

    def test_parse_browse_xml_with_next_key(self, player):
        """Test parsing browse XML with pagination."""
        result = player._parse_browse_xml(ALBUMS_BROWSE_XML)
        assert result["next_key"] is not None
        assert "start=30" in result["next_key"]
        assert len(result["items"]) == 2

        # Album items should have both browse_key and play_url
        album = result["items"][0]
        assert album["text"] == "Album One"
        assert album["text2"] == "Artist One"
        assert album["type"] == "album"
        assert album["browse_key"] is not None
        assert album["play_url"] is not None

    def test_parse_browse_xml_tracks(self, player):
        """Test parsing track browse XML."""
        result = player._parse_browse_xml(TRACKS_BROWSE_XML)
        track = result["items"][0]
        assert track["text"] == "Song One"
        assert track["type"] == "track"
        assert track["play_url"] is not None
        assert track["autoplay_url"] is not None

    def test_parse_browse_xml_error(self, player):
        """Test parsing error response."""
        result = player._parse_browse_xml(ERROR_BROWSE_XML)
        assert result["items"] == []
        assert "error" in result
        assert result["error"] == "Service not available"

    def test_parse_browse_xml_categories(self, player):
        """Test parsing browse XML with categories."""
        result = player._parse_browse_xml(CATEGORY_BROWSE_XML)
        assert len(result["items"]) == 2  # 2 categories
        rock = result["items"][0]
        assert rock["text"] == "Rock"
        assert rock["type"] == "category"
        assert "items" in rock
        assert len(rock["items"]) == 2

    async def test_browse_not_available(self, player):
        """Test browse when player is not available."""
        result = await player.browse()
        assert result["items"] == []
        assert "error" in result

    async def test_browse_top_level(self, player):
        """Test top-level browse request."""
        player._available = True
        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value=TOP_LEVEL_BROWSE_XML)
        mock_session.get = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock())
        )
        player._player = MagicMock()
        player._player.base_url = "http://192.168.1.100:11000"
        player._player._session = mock_session

        result = await player.browse()
        assert len(result["items"]) == 5
        mock_session.get.assert_called_once()

    async def test_browse_with_key(self, player):
        """Test browse with a specific key."""
        player._available = True
        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value=SERVICE_BROWSE_XML)
        mock_session.get = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock())
        )
        player._player = MagicMock()
        player._player.base_url = "http://192.168.1.100:11000"
        player._player._session = mock_session

        result = await player.browse(key="Tidal:")
        assert result["service_name"] == "TIDAL"
        assert result["search_key"] == "Tidal:Search"

    async def test_browse_complex_key_not_encoded(self, player):
        """Test that browse keys containing '?' and '&' are NOT percent-encoded."""
        player._available = True
        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value=ALBUMS_BROWSE_XML)
        mock_session.get = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock())
        )
        player._player = MagicMock()
        player._player.base_url = "http://192.168.1.100:11000"
        player._player._session = mock_session

        complex_key = "/Albums?service=Tidal&genre=0&category=toplist"
        await player.browse(key=complex_key)

        call_kwargs = mock_session.get.call_args.kwargs
        assert call_kwargs["params"] == {"key": complex_key}, "Key must be passed as a params dict"

    async def test_search_not_available(self, player):
        """Test search when player is not available."""
        result = await player.search("Tidal:Search", "test")
        assert result["items"] == []

    async def test_search(self, player):
        """Test search request."""
        player._available = True
        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value=SEARCH_RESULTS_XML)
        mock_session.get = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock())
        )
        player._player = MagicMock()
        player._player.base_url = "http://192.168.1.100:11000"
        player._player._session = mock_session

        result = await player.search("Tidal:Search", "test")
        assert len(result["items"]) == 3

    async def test_play_browse_item(self, player):
        """Test playing a browse item."""
        player._available = True
        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock())
        )
        player._player = MagicMock()
        player._player.base_url = "http://192.168.1.100:11000"
        player._player._session = mock_session

        result = await player.play_browse_item("/Add?service=Tidal&albumid=123&playnow=1")
        assert result is True

    async def test_play_browse_item_not_available(self, player):
        """Test playing browse item when not available."""
        result = await player.play_browse_item("/Add?service=Tidal&albumid=123")
        assert result is False

    async def test_clear_queue(self, player):
        """Test clearing the play queue."""
        player._available = True
        mock_session = MagicMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_session.get = MagicMock(
            return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock())
        )
        player._player = MagicMock()
        player._player.base_url = "http://192.168.1.100:11000"
        player._player._session = mock_session

        result = await player.clear_queue()
        assert result is True

    async def test_clear_queue_not_available(self, player):
        """Test clear queue when not available."""
        result = await player.clear_queue()
        assert result is False


class TestBluOSMediaPlayerBrowse:
    """Tests for BluOSMediaPlayer browse/search overrides."""

    @pytest.fixture
    def device(self):
        return BluOSDevice(
            id="test_device",
            name="Test Player",
            address="192.168.1.100",
            port=11000,
        )

    @pytest.fixture
    def loop(self):
        return asyncio.new_event_loop()

    @pytest.fixture
    def bluos_player(self, device, loop):
        player = BluOSPlayer(device, loop)
        player._available = True
        return player

    @pytest.fixture
    def media_player(self, device, bluos_player):
        return BluOSMediaPlayer(device, bluos_player)

    def test_features_include_browse(self, media_player):
        """Test that browse/search features are declared."""
        from ucapi.media_player import Features

        feature_list = media_player.features
        assert Features.BROWSE_MEDIA in feature_list
        assert Features.SEARCH_MEDIA in feature_list
        assert Features.PLAY_MEDIA in feature_list
        assert Features.CLEAR_PLAYLIST in feature_list

    async def test_browse_top_level(self, media_player):
        """Test browse returns BrowseResults."""
        mock_browse_result = {
            "items": [
                {
                    "text": "Playlists",
                    "type": "link",
                    "browse_key": "playlists",
                    "play_url": None,
                    "autoplay_url": None,
                    "image": None,
                    "text2": None,
                    "context_menu_key": None,
                    "action_url": None,
                    "input_type": None,
                },
                {
                    "text": "TIDAL",
                    "type": "link",
                    "browse_key": "Tidal:",
                    "play_url": None,
                    "autoplay_url": None,
                    "image": "/Sources/images/TidalIcon.png",
                    "text2": None,
                    "context_menu_key": None,
                    "action_url": None,
                    "input_type": None,
                },
            ],
            "next_key": None,
            "search_key": None,
            "parent_key": None,
            "service_name": None,
            "service_icon": None,
            "browse_type": "menu",
        }
        media_player._player.browse = AsyncMock(return_value=mock_browse_result)

        options = BrowseOptions()
        result = await media_player.browse(options)

        assert isinstance(result, BrowseResults)
        assert result.media is not None
        assert result.media.title == "BluOS"
        assert len(result.media.items) == 2
        assert result.media.items[0].title == "Playlists"
        assert result.media.items[0].can_browse is True
        assert result.media.items[0].can_play is False
        assert result.media.items[0].media_class == "directory"

    async def test_browse_with_media_id(self, media_player):
        """Test browse with a specific media_id (browseKey)."""
        mock_browse_result = {
            "items": [
                {
                    "text": "Popular Playlists",
                    "type": "link",
                    "browse_key": "/Playlists?service=Tidal",
                    "play_url": None,
                    "autoplay_url": None,
                    "image": None,
                    "text2": None,
                    "context_menu_key": None,
                    "action_url": None,
                    "input_type": None,
                },
            ],
            "next_key": None,
            "search_key": "Tidal:Search",
            "parent_key": None,
            "service_name": "TIDAL",
            "service_icon": None,
            "browse_type": "menu",
        }
        media_player._player.browse = AsyncMock(return_value=mock_browse_result)

        options = BrowseOptions(media_id="Tidal:")
        result = await media_player.browse(options)

        assert isinstance(result, BrowseResults)
        assert result.media.title == "TIDAL"
        media_player._player.browse.assert_called_once_with(key="Tidal:")

    async def test_browse_stores_search_key(self, media_player):
        """Test that browse stores the search_key for later search use."""
        mock_browse_result = {
            "items": [],
            "next_key": None,
            "search_key": "Tidal:Search",
            "parent_key": None,
            "service_name": "TIDAL",
            "service_icon": None,
            "browse_type": "menu",
        }
        media_player._player.browse = AsyncMock(return_value=mock_browse_result)

        await media_player.browse(BrowseOptions(media_id="Tidal:"))
        assert media_player._last_search_key == "Tidal:Search"

    async def test_browse_error(self, media_player):
        """Test browse with error response."""
        mock_browse_result = {"items": [], "error": "Service not available"}
        media_player._player.browse = AsyncMock(return_value=mock_browse_result)

        result = await media_player.browse(BrowseOptions())
        assert result == StatusCodes.SERVER_ERROR

    async def test_search(self, media_player):
        """Test search returns SearchResults."""
        mock_search_result = {
            "items": [
                {
                    "text": "Artists",
                    "type": "link",
                    "browse_key": "/Artists?expr=test",
                    "play_url": None,
                    "autoplay_url": None,
                    "image": None,
                    "text2": None,
                    "context_menu_key": None,
                    "action_url": None,
                    "input_type": None,
                },
                {
                    "text": "Albums",
                    "type": "link",
                    "browse_key": "/Albums?expr=test",
                    "play_url": None,
                    "autoplay_url": None,
                    "image": None,
                    "text2": None,
                    "context_menu_key": None,
                    "action_url": None,
                    "input_type": None,
                },
            ],
            "next_key": None,
            "search_key": "Tidal:Search",
            "parent_key": None,
            "service_name": "TIDAL",
            "service_icon": None,
            "browse_type": "menu",
        }
        media_player._player.search = AsyncMock(return_value=mock_search_result)

        options = SearchOptions(query="test", media_id="Tidal:Search")
        result = await media_player.search(options)

        assert isinstance(result, SearchResults)
        assert len(result.media) == 2
        assert result.media[0].title == "Artists"

    async def test_search_uses_stored_key(self, media_player):
        """Test search falls back to stored search_key."""
        media_player._last_search_key = "Tidal:Search"
        mock_search_result = {
            "items": [],
            "next_key": None,
            "search_key": "Tidal:Search",
            "parent_key": None,
            "service_name": "TIDAL",
            "service_icon": None,
            "browse_type": "menu",
        }
        media_player._player.search = AsyncMock(return_value=mock_search_result)

        options = SearchOptions(query="test")
        result = await media_player.search(options)
        assert isinstance(result, SearchResults)
        media_player._player.search.assert_called_once_with(search_key="Tidal:Search", query="test")

    async def test_search_no_key(self, media_player):
        """Test search with no search key and no discoverable service returns empty results."""
        media_player._last_search_key = None
        media_player._find_search_key = AsyncMock(return_value=None)
        options = SearchOptions(query="test")
        result = await media_player.search(options)
        assert isinstance(result, SearchResults)
        assert result.media == []

    async def test_command_play_media(self, media_player):
        """Test PLAY_MEDIA command with a direct play URL."""
        media_player._player.play_browse_item = AsyncMock(return_value=True)
        import ucapi

        result = await media_player.command("play_media", {"media_id": "/Add?service=Tidal&albumid=123"})
        assert result == ucapi.StatusCodes.OK
        media_player._player.play_browse_item.assert_called_once_with("/Add?service=Tidal&albumid=123")

    async def test_command_play_media_via_browse_key_cache(self, media_player):
        """Test PLAY_MEDIA resolves play URL from cache when media_id is a browseKey."""
        import ucapi

        # Simulate browsing an album, which populates the cache
        media_player._play_url_cache["Tidal:Album?albumid=123"] = "/Add?service=Tidal&albumid=123&playnow=1"

        media_player._player.play_browse_item = AsyncMock(return_value=True)
        result = await media_player.command("play_media", {"media_id": "Tidal:Album?albumid=123"})

        assert result == ucapi.StatusCodes.OK
        # Must use the cached play URL, not the browseKey
        media_player._player.play_browse_item.assert_called_once_with("/Add?service=Tidal&albumid=123&playnow=1")

    async def test_command_clear_playlist(self, media_player):
        """Test CLEAR_PLAYLIST command."""
        media_player._player.clear_queue = AsyncMock(return_value=True)
        import ucapi

        result = await media_player.command("clear_playlist")
        assert result == ucapi.StatusCodes.OK
        media_player._player.clear_queue.assert_called_once()

    def test_bluos_item_to_browse_item_album(self, media_player):
        """Test converting a BluOS album item to BrowseMediaItem."""
        item = {
            "text": "Album Name",
            "text2": "Artist Name",
            "type": "album",
            "browse_key": "Tidal:Album?albumid=123",
            "play_url": "/Add?service=Tidal&albumid=123&playnow=1",
            "autoplay_url": None,
            "image": "/Artwork?service=Tidal&albumid=123",
            "context_menu_key": None,
            "action_url": None,
            "input_type": None,
        }
        result = media_player._bluos_item_to_browse_item(item)
        assert result.title == "Album Name"
        assert result.subtitle == "Artist Name"
        assert result.media_class == "album"
        assert result.can_browse is True
        assert result.can_play is True
        # media_id should be browseKey when both are present
        assert result.media_id == "Tidal:Album?albumid=123"
        assert result.thumbnail is not None
        # Play URL should be cached for PLAY_MEDIA resolution
        assert media_player._play_url_cache.get("Tidal:Album?albumid=123") == "/Add?service=Tidal&albumid=123&playnow=1"

    def test_bluos_item_to_browse_item_audio_only(self, media_player):
        """Test converting an audio-only item (no browseKey)."""
        item = {
            "text": "Radio Station",
            "text2": None,
            "type": "audio",
            "browse_key": None,
            "play_url": "/Play?url=http://stream.example.com",
            "autoplay_url": None,
            "image": None,
            "context_menu_key": None,
            "action_url": None,
            "input_type": None,
        }
        result = media_player._bluos_item_to_browse_item(item)
        assert result.can_browse is False
        assert result.can_play is True
        assert result.media_id == "/Play?url=http://stream.example.com"
