import threading
import unittest
from unittest import mock

import requests

from pandrator_manager.client import ManagerApiError, ManagerUnavailable
from pandrator_manager.tray import TrayApplication
from pandrator_manager.tray import main as tray_main
from pandrator_manager.tray.menu import build_engine_menu_snapshot


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self._items = items

    @property
    def items(self):
        if len(self._items) == 1 and callable(self._items[0]):
            return tuple(self._items[0]())
        return self._items


class FakeMenuItem:
    def __init__(self, text, action=None, **options):
        self._text = text
        self.action = action
        self.submenu = action if isinstance(action, FakeMenu) else None
        self.options = options

    @property
    def text(self):
        if callable(self._text):
            return self._text(None)
        return self._text


def component(
    component_id="kokoro",
    *,
    label="Kokoro",
    state="present",
    service_id="tts.kokoro",
):
    return {
        "definition": {
            "id": component_id,
            "label": label,
            "service_key": service_id,
            "supported_actions": [
                "install",
                "remove",
                "start",
                "stop",
            ],
        },
        "inspection": {"state": state},
    }


def service(
    *,
    process=None,
    desired_running=False,
    health="stopped",
):
    return {
        "id": "tts.kokoro",
        "component_id": "kokoro",
        "desired_running": desired_running,
        "process": process,
        "health": {
            "state": health,
            "service_id": "tts.kokoro",
        },
    }


def mcp_service(
    *,
    process=None,
    desired_running=False,
    health="stopped",
):
    return {
        "id": "pandrator.mcp",
        "component_id": "pandrator",
        "desired_running": desired_running,
        "process": process,
        "health": {
            "state": health,
            "service_id": "pandrator.mcp",
        },
    }


class EngineMenuSnapshotTests(unittest.TestCase):
    def test_running_engine_is_counted_and_can_be_stopped(self):
        snapshot = build_engine_menu_snapshot(
            [component()],
            [
                service(
                    process={"pid": 8123},
                    desired_running=True,
                    health="healthy",
                )
            ],
        )

        self.assertEqual("1/1 engine running", snapshot.summary)
        self.assertEqual("Running", snapshot.items[0].state_label)
        self.assertEqual("stop", snapshot.items[0].action)
        self.assertEqual("Stop Kokoro", snapshot.items[0].action_label)
        self.assertTrue(snapshot.items[0].enabled)

    def test_pending_restart_can_be_cancelled_from_the_tray(self):
        snapshot = build_engine_menu_snapshot(
            [component()],
            [service(desired_running=True, health="unhealthy")],
        )

        self.assertEqual("Restarting", snapshot.items[0].state_label)
        self.assertEqual("stop", snapshot.items[0].action)
        self.assertFalse(snapshot.items[0].is_running)

    def test_stopped_engine_can_be_started(self):
        snapshot = build_engine_menu_snapshot(
            [component()],
            [service()],
        )

        self.assertEqual("Stopped", snapshot.items[0].state_label)
        self.assertEqual("start", snapshot.items[0].action)
        self.assertEqual("Start Kokoro", snapshot.items[0].action_label)

    def test_failed_engine_is_not_described_as_stopped(self):
        snapshot = build_engine_menu_snapshot(
            [component()],
            [service(desired_running=True, health="failed")],
        )

        self.assertEqual("Failed", snapshot.items[0].state_label)
        self.assertEqual("stop", snapshot.items[0].action)

    def test_busy_manager_disables_runtime_actions(self):
        snapshot = build_engine_menu_snapshot(
            [component()],
            [service()],
            busy=True,
        )

        self.assertTrue(snapshot.busy)
        self.assertFalse(snapshot.items[0].enabled)
        self.assertEqual("start", snapshot.items[0].action)

    def test_only_installed_optional_engines_are_listed_and_sorted(self):
        snapshot = build_engine_menu_snapshot(
            [
                component(
                    "xtts",
                    label="XTTS v2",
                    state="absent",
                    service_id="tts.xtts",
                ),
                component(
                    "silero",
                    label="Silero",
                    service_id="tts.silero",
                ),
                component(),
            ],
            [
                service(),
                {
                    **service(),
                    "id": "tts.silero",
                    "component_id": "silero",
                },
            ],
        )

        self.assertEqual(
            ["Kokoro", "Silero"],
            [item.label for item in snapshot.items],
        )

    def test_degraded_engine_prompts_for_repair_instead_of_start(self):
        snapshot = build_engine_menu_snapshot(
            [component(state="degraded")],
            [],
        )

        self.assertEqual("Needs repair", snapshot.items[0].state_label)
        self.assertIsNone(snapshot.items[0].action)
        self.assertFalse(snapshot.items[0].enabled)

    def test_stopped_mcp_can_be_started(self):
        snapshot = build_engine_menu_snapshot(
            [component()],
            [service(), mcp_service()],
        )

        self.assertTrue(snapshot.mcp.available)
        self.assertEqual("Stopped", snapshot.mcp.state_label)
        self.assertEqual("start", snapshot.mcp.action)
        self.assertEqual("Start MCP", snapshot.mcp.action_label)
        self.assertTrue(snapshot.mcp.enabled)

    def test_running_mcp_can_be_restarted(self):
        snapshot = build_engine_menu_snapshot(
            [component()],
            [
                service(),
                mcp_service(
                    process={"pid": 8456},
                    desired_running=True,
                    health="healthy",
                ),
            ],
        )

        self.assertEqual("Running", snapshot.mcp.state_label)
        self.assertEqual("restart", snapshot.mcp.action)
        self.assertEqual("Restart MCP", snapshot.mcp.action_label)

    def test_busy_manager_disables_mcp_action(self):
        snapshot = build_engine_menu_snapshot(
            [],
            [mcp_service()],
            busy=True,
        )

        self.assertEqual("start", snapshot.mcp.action)
        self.assertFalse(snapshot.mcp.enabled)


class TrayApplicationEngineTests(unittest.TestCase):
    def test_pystray_backend_uses_the_packaged_pandrator_mark(self):
        image = TrayApplication(mock.Mock())._image()

        self.assertEqual((64, 64), image.size)
        self.assertEqual(0, image.getpixel((0, 0))[3])

    def test_tray_reads_components_services_and_operation_state(self):
        client = mock.Mock()
        client.status.return_value = {"active_operation_id": None}
        client.components.return_value = [component()]
        client.services.return_value = [service()]
        application = TrayApplication(client)

        snapshot = application.engine_snapshot()

        self.assertEqual("0/1 engine running", snapshot.summary)
        self.assertEqual(
            "Pandrator Manager — 0/1 engine running",
            application._status_label(),
        )

    def test_tray_engine_action_uses_manager_runtime_api(self):
        client = mock.Mock()
        client.status.return_value = {"active_operation_id": None}
        client.components.return_value = [component()]
        client.services.return_value = [service()]
        application = TrayApplication(client)

        application.control_engine("tts.kokoro", "start")

        client.runtime.assert_called_once_with(
            "start",
            ("tts.kokoro",),
        )

    def test_pystray_menu_exposes_engine_state_and_action(self):
        client = mock.Mock()
        client.status.return_value = {"active_operation_id": None}
        client.components.return_value = [component()]
        client.services.return_value = [service()]
        application = TrayApplication(client)
        application.engine_snapshot()

        fake_pystray = mock.Mock(Menu=FakeMenu, MenuItem=FakeMenuItem)
        with mock.patch.dict("sys.modules", {"pystray": fake_pystray}):
            top_level = application._pystray_menu_items()
            engine_group = next(
                item
                for item in top_level
                if getattr(item, "text", "").startswith("Speech engines")
            )
            engine = engine_group.submenu.items[0]

        self.assertEqual("Speech engines — 0/1 running", engine_group.text)
        self.assertEqual("Kokoro — Stopped", engine.text)
        self.assertEqual("Start Kokoro", engine.submenu.items[0].text)

    def test_pystray_menu_exposes_mcp_state_and_action(self):
        client = mock.Mock()
        client.status.return_value = {"active_operation_id": None}
        client.components.return_value = [component()]
        client.services.return_value = [service(), mcp_service()]
        application = TrayApplication(client)
        application.engine_snapshot()

        fake_pystray = mock.Mock(Menu=FakeMenu, MenuItem=FakeMenuItem)
        with mock.patch.dict("sys.modules", {"pystray": fake_pystray}):
            top_level = application._pystray_menu_items()
            mcp_group = next(
                item
                for item in top_level
                if getattr(item, "text", "").startswith("MCP server")
            )
            self.assertEqual("MCP server — Stopped", mcp_group.text)
            self.assertEqual("Start MCP", mcp_group.submenu.items[0].text)
            mcp_group.submenu.items[0].action()
        client.runtime.assert_called_once_with("start", ("pandrator.mcp",))


def refusal(code):
    return ManagerApiError(
        409,
        {"error": {"code": code, "message": f"fixture {code}"}},
    )


class TrayQuitPandratorTests(unittest.TestCase):
    def setUp(self):
        self.client = mock.Mock()
        self.tray = TrayApplication(self.client)
        self.icon = mock.Mock()
        discover = mock.patch(
            "pandrator_manager.tray.ManagerClient.discover",
            return_value=self.client,
        )
        self.discover = discover.start()
        self.addCleanup(discover.stop)
        dialogs = mock.patch("pandrator_manager.tray.dialogs")
        self.dialogs = dialogs.start()
        self.addCleanup(dialogs.stop)
        self.dialogs.ask_yes_no.return_value = False

    def test_idle_quit_asks_nothing_and_closes_the_tray(self):
        self.assertTrue(self.tray.quit_pandrator(self.icon))

        self.client.quit_manager.assert_called_once_with(stop_active_work=False)
        self.dialogs.ask_yes_no.assert_not_called()
        self.icon.stop.assert_called_once_with()
        self.assertTrue(self.tray._menu_refresh_stop.is_set())

    def test_active_work_answered_no_stops_nothing_and_keeps_the_tray(self):
        self.client.quit_manager.side_effect = refusal("application_busy")

        self.assertFalse(self.tray.quit_pandrator(self.icon))

        self.client.quit_manager.assert_called_once_with(stop_active_work=False)
        self.dialogs.ask_yes_no.assert_called_once()
        self.icon.stop.assert_not_called()
        self.assertFalse(self.tray._menu_refresh_stop.is_set())

    def test_active_work_answered_yes_retries_with_stop_active_work(self):
        self.client.quit_manager.side_effect = [refusal("application_busy"), None]
        self.dialogs.ask_yes_no.return_value = True

        self.assertTrue(self.tray.quit_pandrator(self.icon))

        self.assertEqual(
            [
                mock.call(stop_active_work=False),
                mock.call(stop_active_work=True),
            ],
            self.client.quit_manager.call_args_list,
        )
        self.icon.stop.assert_called_once_with()

    def test_unknown_activity_asks_with_the_could_not_check_wording(self):
        self.client.quit_manager.side_effect = [
            refusal("application_activity_unknown"),
            None,
        ]
        self.dialogs.ask_yes_no.return_value = True

        self.assertTrue(self.tray.quit_pandrator(self.icon))

        question = self.dialogs.ask_yes_no.call_args.args[1]
        self.assertIn("could not check", question)

    def test_maintenance_shows_a_message_and_keeps_the_tray(self):
        self.client.quit_manager.side_effect = refusal("maintenance_in_progress")

        self.assertFalse(self.tray.quit_pandrator(self.icon))

        self.dialogs.ask_yes_no.assert_not_called()
        self.assertEqual(
            "The Manager is busy starting services or running maintenance. "
            "Try again in a moment.",
            self.dialogs.show_message.call_args.args[1],
        )
        self.icon.stop.assert_not_called()

    def test_a_manager_already_stopping_shows_a_message_and_keeps_the_tray(self):
        self.client.quit_manager.side_effect = refusal("manager_stopping")

        self.assertFalse(self.tray.quit_pandrator(self.icon))

        self.dialogs.show_message.assert_called_once()
        self.icon.stop.assert_not_called()

    def test_a_refusal_after_yes_is_reported_not_retried_again(self):
        self.client.quit_manager.side_effect = [
            refusal("application_busy"),
            refusal("maintenance_in_progress"),
        ]
        self.dialogs.ask_yes_no.return_value = True

        self.assertFalse(self.tray.quit_pandrator(self.icon))

        self.assertEqual(2, self.client.quit_manager.call_count)
        self.dialogs.show_message.assert_called_once()
        self.icon.stop.assert_not_called()

    def test_a_refusal_without_a_message_still_gives_a_usable_message(self):
        self.client.quit_manager.side_effect = ManagerApiError(
            501,
            {"error": {"code": "unsupported"}},
        )

        self.assertFalse(self.tray.quit_pandrator(self.icon))

        text = self.dialogs.show_message.call_args.args[1]
        self.assertNotIn("None", text)
        self.assertIn("unsupported", text)

    def test_the_replaced_clients_session_is_closed(self):
        fresh = mock.Mock()
        self.discover.return_value = fresh

        self.tray.quit_pandrator(self.icon)

        self.client.session.close.assert_called_once_with()
        fresh.session.close.assert_not_called()

    def test_the_status_notifier_gets_the_synchronous_quit_action(self):
        captured = {}
        application = mock.Mock()
        with (
            mock.patch("pandrator_manager.tray.sys.platform", "linux"),
            mock.patch.dict(
                "pandrator_manager.tray.os.environ",
                {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/fixture"},
            ),
            mock.patch("pandrator_manager.tray.tray_available", return_value=(True, "")),
            mock.patch("pandrator_manager.tray._claim_tray_instance") as claim,
            mock.patch(
                "pandrator_manager.tray.ManagerClient.ensure_running",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "pandrator_manager.tray.TrayApplication",
                return_value=application,
            ),
            mock.patch(
                "pandrator_manager.tray.status_notifier.run_status_notifier",
                side_effect=lambda actions, **_options: captured.update(actions),
            ),
        ):
            claim.return_value = mock.Mock(
                read_text=mock.Mock(return_value="{}"),
            )
            self.assertEqual(0, tray_main(["--workspace", "fixture-workspace"]))

        self.assertIs(application.quit_pandrator, captured["quit_pandrator"])
        self.assertIsNot(application.quit_pandrator_async, captured["quit_pandrator"])

    def test_a_manager_that_cannot_be_found_asks_for_nothing_and_keeps_the_tray(self):
        self.discover.side_effect = ManagerUnavailable("Manager process is not running.")

        with self.assertLogs(level="ERROR"):
            self.assertFalse(self.tray.quit_pandrator(self.icon))

        self.client.quit_manager.assert_not_called()
        self.dialogs.show_message.assert_called_once()
        self.icon.stop.assert_not_called()

    def test_the_quit_goes_to_the_rediscovered_manager(self):
        fresh = mock.Mock()
        self.discover.return_value = fresh

        self.assertTrue(self.tray.quit_pandrator(self.icon))

        self.discover.assert_called_once_with(self.client.layout.workspace)
        fresh.quit_manager.assert_called_once_with(stop_active_work=False)
        self.client.quit_manager.assert_not_called()
        self.assertIs(fresh, self.tray.client)

    def test_the_unconfirmed_wording_makes_no_claim_about_what_stopped(self):
        self.client.quit_manager.side_effect = requests.ConnectionError("down")

        with self.assertLogs(level="ERROR"):
            self.tray.quit_pandrator(self.icon)

        self.assertEqual(
            "Pandrator could not confirm the quit. If it is still running, try again.",
            self.dialogs.show_message.call_args.args[1],
        )

    def test_an_unreachable_manager_shows_an_error_and_keeps_the_tray(self):
        self.client.quit_manager.side_effect = requests.ConnectionError("down")

        with self.assertLogs(level="ERROR"):
            self.assertFalse(self.tray.quit_pandrator(self.icon))

        self.dialogs.show_message.assert_called_once()
        self.icon.stop.assert_not_called()

    def test_a_second_click_during_the_question_is_ignored(self):
        results = []

        def second_click(*_args):
            results.append(self.tray.quit_pandrator(self.icon))
            return False

        self.client.quit_manager.side_effect = refusal("application_busy")
        self.dialogs.ask_yes_no.side_effect = second_click

        self.assertFalse(self.tray.quit_pandrator(self.icon))

        self.assertEqual([False], results)
        self.assertEqual(1, self.client.quit_manager.call_count)

    def test_pystray_menu_ends_with_quit_pandrator_above_close_tray_only(self):
        self.tray.engine_snapshot()
        fake_pystray = mock.Mock(Menu=FakeMenu, MenuItem=FakeMenuItem)
        with mock.patch.dict("sys.modules", {"pystray": fake_pystray}):
            top_level = self.tray._pystray_menu_items()

        self.assertEqual(
            ["Quit Pandrator", "Close tray only"],
            [item.text for item in top_level[-2:]],
        )
        self.assertEqual(self.tray.quit_pandrator_async, top_level[-2].action)
        self.assertEqual(self.tray.quit_tray, top_level[-1].action)

    def test_the_menu_callback_runs_the_quit_on_a_worker_thread(self):
        self.tray.quit_pandrator_async(self.icon)
        for thread in threading.enumerate():
            if thread.name == "pandrator-tray-quit":
                thread.join(timeout=5)

        self.client.quit_manager.assert_called_once_with(stop_active_work=False)
        self.icon.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
