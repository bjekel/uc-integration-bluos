"""Tests for reconfigure actions: configure/remove and Integration Manager backup/restore."""

import json
from unittest.mock import MagicMock

import pytest
import setup_flow
from config import BluOSDevice, Devices
from setup_flow import SetupSteps, driver_setup_handler, get_configured_device
from ucapi import (
    AbortDriverSetup,
    DriverSetupRequest,
    IntegrationSetupError,
    RequestUserInput,
    SetupComplete,
    SetupError,
    UserDataResponse,
)


def _setting(result: RequestUserInput, setting_id: str) -> dict | None:
    return next((s for s in result.settings if s["id"] == setting_id), None)


def _dropdown_ids(result: RequestUserInput, setting_id: str) -> list[str]:
    setting = _setting(result, setting_id)
    assert setting is not None, f"missing setting {setting_id}"
    return [item["id"] for item in setting["field"]["dropdown"]["items"]]


@pytest.fixture
def devices(tmp_path):
    """Real Devices store with mock add/remove callbacks."""
    store = Devices(str(tmp_path), add_handler=MagicMock(), remove_handler=MagicMock())
    store.add_or_update(BluOSDevice(id="aa", name="Kitchen", address="192.168.1.10"), trigger_callbacks=False)
    store.add_or_update(
        BluOSDevice(id="bb", name="Living", address="192.168.1.11", volume_step=3), trigger_callbacks=False
    )
    store._add_handler.reset_mock()
    store._remove_handler.reset_mock()
    return store


@pytest.fixture(autouse=True)
def wire_devices(devices):
    setup_flow._reset_state()
    setup_flow._configured_device = None
    setup_flow.set_devices(devices)
    yield
    setup_flow.set_devices(None)
    setup_flow._reset_state()


async def _start_reconfigure() -> RequestUserInput:
    result = await driver_setup_handler(DriverSetupRequest(reconfigure=True, setup_data={}))
    assert isinstance(result, RequestUserInput)
    return result


class TestConfigurationModeScreen:
    @pytest.mark.asyncio
    async def test_choice_and_action_dropdowns(self, devices):
        result = await _start_reconfigure()

        # Official/ucapi-framework convention: `choice` first, then `action`.
        assert [s["id"] for s in result.settings] == ["choice", "action"]
        assert _dropdown_ids(result, "choice") == ["aa", "bb"]
        assert _dropdown_ids(result, "action") == ["add", "configure", "remove", "reset", "backup", "restore"]
        assert _setting(result, "choice")["field"]["dropdown"]["value"] == "aa"

    @pytest.mark.asyncio
    async def test_no_devices_offers_add_and_restore_only(self, devices):
        devices.clear()
        result = await _start_reconfigure()

        assert _dropdown_ids(result, "choice") == [""]
        assert _dropdown_ids(result, "action") == ["add", "restore"]

    @pytest.mark.asyncio
    async def test_unknown_action_is_error(self):
        await _start_reconfigure()
        result = await driver_setup_handler(UserDataResponse(input_values={"choice": "aa", "action": "bogus"}))
        assert isinstance(result, SetupError)


class TestConfigureDevice:
    @pytest.mark.asyncio
    async def test_configure_prefills_existing_values(self):
        await _start_reconfigure()
        result = await driver_setup_handler(UserDataResponse(input_values={"choice": "bb", "action": "configure"}))

        assert isinstance(result, RequestUserInput)
        assert setup_flow._setup_step == SetupSteps.DEVICE_CONFIGURE
        assert _setting(result, "name")["field"]["text"]["value"] == "Living"
        assert _setting(result, "address")["field"]["text"]["value"] == "192.168.1.11"
        assert _setting(result, "port")["field"]["number"]["value"] == 11000
        assert _setting(result, "volume_step")["field"]["number"]["value"] == 3

    @pytest.mark.asyncio
    async def test_configure_keeps_id_and_applies_changes(self):
        await _start_reconfigure()
        await driver_setup_handler(UserDataResponse(input_values={"choice": "bb", "action": "configure"}))
        result = await driver_setup_handler(
            UserDataResponse(
                input_values={
                    "name": "Lounge",
                    "address": "192.168.1.99",
                    "port": "11000",
                    "volume_step": "7",
                    "timeout": "4",
                    "standby_timeout": "90",
                    "active_poll_timeout": "20",
                }
            )
        )

        assert isinstance(result, SetupComplete)
        device = get_configured_device()
        assert device == BluOSDevice(
            id="bb",
            name="Lounge",
            address="192.168.1.99",
            port=11000,
            volume_step=7,
            timeout=4.0,
            standby_timeout=90,
            active_poll_timeout=20,
        )

    @pytest.mark.asyncio
    async def test_configure_invalid_address_reshown_with_error(self):
        await _start_reconfigure()
        await driver_setup_handler(UserDataResponse(input_values={"choice": "bb", "action": "configure"}))
        result = await driver_setup_handler(UserDataResponse(input_values={"name": "X", "address": "not-an-ip"}))

        assert isinstance(result, RequestUserInput)
        assert _setting(result, "error") is not None
        assert setup_flow._setup_step == SetupSteps.DEVICE_CONFIGURE
        assert get_configured_device() is None

    @pytest.mark.asyncio
    async def test_configure_unknown_device_is_error(self):
        await _start_reconfigure()
        result = await driver_setup_handler(UserDataResponse(input_values={"choice": "zz", "action": "configure"}))
        assert isinstance(result, SetupError)

    @pytest.mark.asyncio
    async def test_add_flow_after_configure_does_not_reuse_edited_device(self):
        """Starting a new setup clears the device that was being edited."""
        await _start_reconfigure()
        await driver_setup_handler(UserDataResponse(input_values={"choice": "bb", "action": "configure"}))
        await driver_setup_handler(AbortDriverSetup(error=IntegrationSetupError.NONE))
        assert setup_flow._editing_device is None


class TestRemoveDevice:
    @pytest.mark.asyncio
    async def test_remove_selected_device(self, devices):
        await _start_reconfigure()
        result = await driver_setup_handler(UserDataResponse(input_values={"choice": "aa", "action": "remove"}))

        assert isinstance(result, SetupComplete)
        assert [d.id for d in devices.all()] == ["bb"]
        devices._remove_handler.assert_called_once_with("aa")

    @pytest.mark.asyncio
    async def test_remove_unknown_device_is_error(self, devices):
        await _start_reconfigure()
        result = await driver_setup_handler(UserDataResponse(input_values={"choice": "zz", "action": "remove"}))

        assert isinstance(result, SetupError)
        assert len(devices) == 2


class TestIntegrationManagerBackup:
    """Mirrors uc-intg-manager's backup_service.backup_integration()."""

    @pytest.mark.asyncio
    async def test_single_put_backup_then_abort_changes_nothing(self, devices, tmp_path):
        config_before = (tmp_path / "config.json").read_text()

        # 1. POST /intg/setup reconfigure=true
        first = await _start_reconfigure()
        # 2. read the `choice` dropdown value
        choice = _setting(first, "choice")["field"]["dropdown"]["value"]
        assert choice
        # 3. one PUT with choice + action=backup + backup_data
        result = await driver_setup_handler(
            UserDataResponse(input_values={"choice": choice, "action": "backup", "backup_data": "[]"})
        )
        # 4. the response holds a `backup_data` textarea with the config
        assert isinstance(result, RequestUserInput)
        backup = _setting(result, "backup_data")["field"]["textarea"]["value"]
        data = json.loads(backup)
        assert [d["id"] for d in data["devices"]] == ["aa", "bb"]
        # 5. the manager aborts the setup (DELETE)
        assert isinstance(await driver_setup_handler(AbortDriverSetup(error=IntegrationSetupError.NONE)), SetupComplete)

        assert (tmp_path / "config.json").read_text() == config_before
        devices._add_handler.assert_not_called()
        devices._remove_handler.assert_not_called()
        assert get_configured_device() is None

    @pytest.mark.asyncio
    async def test_submitting_backup_screen_completes_without_changes(self, devices):
        await _start_reconfigure()
        await driver_setup_handler(UserDataResponse(input_values={"choice": "aa", "action": "backup"}))
        result = await driver_setup_handler(UserDataResponse(input_values={"backup_data": "edited garbage"}))

        assert isinstance(result, SetupComplete)
        assert len(devices) == 2
        devices._remove_handler.assert_not_called()


class TestRestore:
    async def _open_restore(self) -> RequestUserInput:
        await _start_reconfigure()
        result = await driver_setup_handler(UserDataResponse(input_values={"choice": "aa", "action": "restore"}))
        assert isinstance(result, RequestUserInput)
        assert _setting(result, "restore_data") is not None
        return result

    @pytest.mark.asyncio
    async def test_round_trip(self, devices):
        backup = devices.export()
        devices.clear()

        await self._open_restore()
        result = await driver_setup_handler(UserDataResponse(input_values={"restore_data": backup}))

        assert isinstance(result, SetupComplete)
        assert [d.id for d in devices.all()] == ["aa", "bb"]
        assert devices.get("bb").volume_step == 3

    @pytest.mark.asyncio
    async def test_accepts_bare_device_list(self, devices):
        """Backups in the bare-list format (as other integrations export) load too."""
        await self._open_restore()
        payload = json.dumps([{"id": "cc", "name": "Office", "address": "192.168.1.12"}])
        result = await driver_setup_handler(UserDataResponse(input_values={"restore_data": payload}))

        assert isinstance(result, SetupComplete)
        assert [d.id for d in devices.all()] == ["cc"]

    @pytest.mark.asyncio
    async def test_restore_with_no_devices_configured(self, devices):
        backup = devices.export()
        devices.clear()
        result = await _start_reconfigure()
        choice = _setting(result, "choice")["field"]["dropdown"]["value"]

        await driver_setup_handler(UserDataResponse(input_values={"choice": choice, "action": "restore"}))
        result = await driver_setup_handler(UserDataResponse(input_values={"restore_data": backup}))

        assert isinstance(result, SetupComplete)
        assert len(devices) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        ["", "   ", "{not json", '"a string"', '{"devices": "nope"}', '[{"name": "no id"}]', "42"],
    )
    async def test_invalid_input_reshown_with_error(self, devices, payload):
        await self._open_restore()
        result = await driver_setup_handler(UserDataResponse(input_values={"restore_data": payload}))

        assert isinstance(result, RequestUserInput)
        assert _setting(result, "error") is not None
        assert setup_flow._setup_step == SetupSteps.RESTORE
        assert [d.id for d in devices.all()] == ["aa", "bb"]
        devices._remove_handler.assert_not_called()
