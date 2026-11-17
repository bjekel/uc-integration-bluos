"""Tests for the translation catalogs and helpers."""

from unittest.mock import AsyncMock, patch

import i18n
import pytest
import setup_flow
from config import BluOSDevice, Devices
from discover import DiscoveredDevice
from i18n import LANGUAGES, _a, _am, catalogs, parse_po
from ucapi import DriverSetupRequest, UserDataResponse


class TestParsePo:
    def test_basic_and_continuation_lines(self):
        text = (
            'msgid ""\nmsgstr ""\n"Language: de\\n"\n\n'
            '# comment\nmsgid "Hello"\nmsgstr "Hallo"\n\n'
            'msgid "Long "\n"text"\nmsgstr "Langer "\n"Text"\n'
        )
        assert parse_po(text) == {"Hello": "Hallo", "Long text": "Langer Text"}

    def test_skips_fuzzy_and_empty(self):
        text = '#, fuzzy\nmsgid "A"\nmsgstr "X"\n\nmsgid "B"\nmsgstr ""\n\nmsgid "C"\nmsgstr "Z"\n'
        assert parse_po(text) == {"C": "Z"}

    def test_escapes(self):
        assert parse_po('msgid "a\\"b\\n"\nmsgstr "c\\"d\\n"\n') == {'a"b\n': 'c"d\n'}


class TestHelpers:
    def test_untranslated_falls_back_to_english_only(self):
        assert _a("definitely not translated") == {"en": "definitely not translated"}

    def test_translated_contains_all_languages(self):
        assert _a("Cancel") == {"en": "Cancel", "de": "Abbrechen", "nl": "Annuleren"}

    def test_am_concatenates_parts(self):
        result = _am("Cancel", " / ", "Error")
        assert result["en"] == "Cancel / Error"
        assert result["nl"] == "Annuleren / Fout"


async def _render_all_screens() -> None:
    """Drive every setup screen so each translatable string is requested."""
    store = Devices("/nonexistent-unused")
    store._devices = {"aa": BluOSDevice(id="aa", name="Kitchen", address="192.168.1.10")}
    setup_flow.set_devices(store)
    try:
        setup_flow.get_setup_data_schema()
        await setup_flow.driver_setup_handler(DriverSetupRequest(reconfigure=True, setup_data={}))
        for action in ("add", "configure", "backup", "restore"):
            setup_flow._setup_step = setup_flow.SetupSteps.CONFIGURATION_MODE
            await setup_flow.driver_setup_handler(UserDataResponse(input_values={"choice": "aa", "action": action}))
        setup_flow._show_restore(error=True)
        setup_flow._editing_device = store.get("aa")
        setup_flow._show_device_configure(address_error=True)
        setup_flow._editing_device = None
        setup_flow._selected_device = DiscoveredDevice(host="192.168.1.10", port=11000, name="X")
        setup_flow._show_device_configure()
        setup_flow._show_manual_entry()
        setup_flow._discovered_devices = [setup_flow._selected_device]
        setup_flow._show_device_choice()
        with patch("setup_flow.discover_bluos_players", new_callable=AsyncMock, return_value=[]):
            await setup_flow._start_discovery()
        setup_flow._setup_step = setup_flow.SetupSteps.DISCOVER
        await setup_flow.driver_setup_handler(UserDataResponse(input_values={"manual_address": "bad"}))
    finally:
        setup_flow.set_devices(None)
        setup_flow._reset_state()


@pytest.mark.asyncio
async def test_every_setup_string_is_translated():
    """Fails when a new setup text is added without updating every .po file."""
    requested: set[str] = set()
    real = i18n._a

    def recorder(message: str) -> dict[str, str]:
        requested.add(message)
        return real(message)

    with patch("setup_flow._a", side_effect=recorder), patch("i18n._a", side_effect=recorder):
        await _render_all_screens()

    requested -= {"\n\n"}  # formatting-only part
    assert len(requested) > 30
    for lang in LANGUAGES:
        missing = sorted(requested - catalogs()[lang].keys())
        assert not missing, f"{lang}.po is missing translations: {missing}"


def test_no_stale_translations():
    """Every catalog has the same msgids, so languages stay in step."""
    keys = [set(catalogs()[lang]) for lang in LANGUAGES]
    assert all(k == keys[0] for k in keys)
