import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dbus_next import Variant

from pandrator_manager.desktop import (
    host_process_environment,
    open_desktop_url,
)
from pandrator_manager.tray import dialogs
from pandrator_manager.tray.menu import (
    EngineMenuItem,
    EngineMenuSnapshot,
    McpMenuItem,
)
from pandrator_manager.tray.status_notifier import (
    DBusMenu,
    StatusNotifierItem,
    _ActionDispatcher,
)


class HostDesktopLaunchTests(unittest.TestCase):
    def test_frozen_library_paths_are_removed_without_losing_host_paths(self):
        temporary = Path(tempfile.gettempdir())
        first = temporary / "_MEIfirst"
        second = temporary / "_MEIsecond"
        host = temporary / "host-libraries"
        environment = {
            "LD_LIBRARY_PATH": os.pathsep.join((str(first), str(second))),
            "LD_LIBRARY_PATH_ORIG": os.pathsep.join((str(first), str(host))),
            "UNCHANGED": "yes",
            "_MEIPASS2": str(first),
            "PYTHONHOME": str(first),
        }

        cleaned = host_process_environment(environment)

        self.assertEqual(
            host.resolve(strict=False),
            Path(cleaned["LD_LIBRARY_PATH"]).resolve(strict=False),
        )
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", cleaned)
        self.assertNotIn("_MEIPASS2", cleaned)
        self.assertNotIn("PYTHONHOME", cleaned)
        self.assertEqual("yes", cleaned["UNCHANGED"])

    def test_linux_url_open_uses_xdg_open_with_a_clean_detached_environment(self):
        process = mock.Mock(pid=4123)
        process.wait.return_value = 0
        with (
            mock.patch("pandrator_manager.desktop.sys.platform", "linux"),
            mock.patch(
                "pandrator_manager.desktop.shutil.which",
                return_value="/usr/bin/xdg-open",
            ),
            mock.patch(
                "pandrator_manager.desktop.host_process_environment",
                return_value={"SAFE": "1"},
            ),
            mock.patch(
                "pandrator_manager.desktop.subprocess.Popen",
                return_value=process,
            ) as popen,
            mock.patch("pandrator_manager.desktop.threading.Thread") as thread,
        ):
            opened = open_desktop_url("http://127.0.0.1:8098/recovery")

        self.assertTrue(opened)
        self.assertEqual(
            ["/usr/bin/xdg-open", "http://127.0.0.1:8098/recovery"],
            popen.call_args.args[0],
        )
        self.assertEqual({"SAFE": "1"}, popen.call_args.kwargs["env"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        thread.return_value.start.assert_called_once_with()

    def test_desktop_opener_rejects_non_http_urls(self):
        with mock.patch("pandrator_manager.desktop.webbrowser.open") as browser:
            self.assertFalse(open_desktop_url("file:///tmp/private"))
        browser.assert_not_called()


class StatusNotifierContractTests(unittest.TestCase):
    def setUp(self):
        self.quit_event = asyncio.Event()
        self.dispatcher = _ActionDispatcher({}, self.quit_event)

    def test_status_notifier_exposes_native_activation_and_pixmap_contract(self):
        notifier = StatusNotifierItem(self.dispatcher)
        interface = notifier.introspect()
        method_names = {member.name for member in interface.methods}
        property_names = {member.name for member in interface.properties}

        self.assertIn("Activate", method_names)
        self.assertIn("SecondaryActivate", method_names)
        self.assertIn("IconPixmap", property_names)
        self.assertIn("Menu", property_names)
        self.assertEqual("/MenuBar", notifier.Menu)
        pixmaps = notifier.IconPixmap
        self.assertEqual([32, 64], [entry[0] for entry in pixmaps])
        self.assertEqual(
            [32 * 32 * 4, 64 * 64 * 4],
            [len(entry[2]) for entry in pixmaps],
        )
        self.assertTrue(
            all(entry[2][0] == 0 for entry in pixmaps),
            "the packaged Pandrator mark should retain transparent corners",
        )

    def test_dbus_menu_preserves_all_existing_tray_actions(self):
        menu = DBusMenu(self.dispatcher)
        layout = menu._layout(0, -1, [])
        labels = [
            child.value[1]["label"].value
            for child in layout[2]
            if "label" in child.value[1]
        ]

        self.assertEqual(
            [
                "Open Pandrator",
                "Open setup / recovery",
                "Start Pandrator",
                "Stop Pandrator",
                "MCP server — Not available",
                "Speech engines",
                "Quit Pandrator",
                "Close tray only",
            ],
            labels,
        )

    def test_dbus_menu_keeps_close_tray_on_the_original_quit_id(self):
        menu = DBusMenu(self.dispatcher)

        self.assertEqual("quit", menu._actions[10])
        self.assertEqual("quit_pandrator", menu._actions[11])
        self.assertEqual([*range(1, 10), 11, 10], menu._children[0])
        with mock.patch.object(self.dispatcher, "dispatch") as dispatch:
            menu.Event(11, "clicked", Variant("s", ""), 0)
        dispatch.assert_called_once_with("quit_pandrator")

    def test_dbus_menu_exposes_engine_state_and_runtime_action(self):
        snapshot = EngineMenuSnapshot(
            items=(
                EngineMenuItem(
                    component_id="kokoro",
                    service_id="tts.kokoro",
                    label="Kokoro",
                    state="running",
                    state_label="Running",
                    action="stop",
                    action_label="Stop Kokoro",
                    enabled=True,
                    is_running=True,
                ),
            )
        )
        menu = DBusMenu(self.dispatcher, snapshot)

        engine_layout = menu._layout(8, -1, [])
        parent = engine_layout[2][0].value
        action = parent[2][0].value

        self.assertEqual("Kokoro — Running", parent[1]["label"].value)
        self.assertEqual("Stop Kokoro", action[1]["label"].value)
        with mock.patch.object(self.dispatcher, "dispatch") as dispatch:
            menu.Event(101, "clicked", Variant("s", ""), 0)
        dispatch.assert_called_once_with(("tts.kokoro", "stop"))

    def test_dbus_menu_updates_revision_only_when_engine_state_changes(self):
        menu = DBusMenu(self.dispatcher)
        initial = menu.revision

        self.assertFalse(
            menu.update_engine_snapshot(
                EngineMenuSnapshot(),
                emit=False,
            )
        )
        self.assertEqual(initial, menu.revision)

        changed = EngineMenuSnapshot(available=False, message="Unavailable")
        self.assertTrue(
            menu.update_engine_snapshot(changed, emit=False)
        )
        self.assertEqual(initial + 1, menu.revision)
        self.assertEqual(
            "Unavailable",
            menu._layout(8, -1, [])[2][0].value[1]["label"].value,
        )

    def test_dbus_menu_exposes_mcp_state_and_runtime_action(self):
        snapshot = EngineMenuSnapshot(
            mcp=McpMenuItem(
                available=True,
                state="stopped",
                state_label="Stopped",
                action="start",
                action_label="Start MCP",
                enabled=True,
                is_running=False,
            )
        )
        menu = DBusMenu(self.dispatcher, snapshot)

        mcp_layout = menu._layout(7, -1, [])
        action = mcp_layout[2][0].value

        self.assertEqual("MCP server — Stopped", mcp_layout[1]["label"].value)
        self.assertEqual("Start MCP", action[1]["label"].value)
        with mock.patch.object(self.dispatcher, "dispatch") as dispatch:
            menu.Event(90, "clicked", Variant("s", ""), 0)
        dispatch.assert_called_once_with(("pandrator.mcp", "start"))

    def test_status_notifier_tooltip_reports_engine_summary(self):
        notifier = StatusNotifierItem(self.dispatcher)
        notifier.update_engine_snapshot(
            EngineMenuSnapshot(
                items=(
                    EngineMenuItem(
                        component_id="kokoro",
                        service_id="tts.kokoro",
                        label="Kokoro",
                        state="stopped",
                        state_label="Stopped",
                        action="start",
                        action_label="Start Kokoro",
                        enabled=True,
                        is_running=False,
                    ),
                )
            ),
            emit=False,
        )

        self.assertEqual("0/1 engine running", notifier.ToolTip[3])

    def test_disabled_engine_action_cannot_be_dispatched(self):
        snapshot = EngineMenuSnapshot(
            items=(
                EngineMenuItem(
                    component_id="kokoro",
                    service_id="tts.kokoro",
                    label="Kokoro",
                    state="stopped",
                    state_label="Stopped",
                    action="start",
                    action_label="Start Kokoro",
                    enabled=False,
                    is_running=False,
                ),
            ),
            busy=True,
        )
        menu = DBusMenu(self.dispatcher, snapshot)

        with mock.patch.object(self.dispatcher, "dispatch") as dispatch:
            menu.Event(101, "clicked", Variant("s", ""), 0)

        dispatch.assert_not_called()


class StatusNotifierActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepted_quit_pandrator_also_ends_the_tray(self):
        quit_event = asyncio.Event()
        dispatcher = _ActionDispatcher({"quit_pandrator": lambda: True}, quit_event)

        await dispatcher._invoke("quit_pandrator")

        self.assertTrue(quit_event.is_set())

    async def test_declined_quit_pandrator_keeps_the_tray(self):
        quit_event = asyncio.Event()
        dispatcher = _ActionDispatcher({"quit_pandrator": lambda: False}, quit_event)

        await dispatcher._invoke("quit_pandrator")

        self.assertFalse(quit_event.is_set())

    async def test_failing_quit_pandrator_keeps_the_tray(self):
        quit_event = asyncio.Event()

        def failing() -> bool:
            raise RuntimeError("fixture")

        dispatcher = _ActionDispatcher({"quit_pandrator": failing}, quit_event)

        with self.assertLogs(level="ERROR"):
            await dispatcher._invoke("quit_pandrator")

        self.assertFalse(quit_event.is_set())

    async def test_close_tray_only_still_ends_just_the_tray(self):
        quit_event = asyncio.Event()
        quit_pandrator = mock.Mock(return_value=True)
        dispatcher = _ActionDispatcher({"quit_pandrator": quit_pandrator}, quit_event)

        dispatcher.dispatch("quit")

        self.assertTrue(quit_event.is_set())
        quit_pandrator.assert_not_called()

    async def test_engine_action_is_forwarded_off_the_dbus_event_loop(self):
        quit_event = asyncio.Event()
        engine_action = mock.Mock()
        dispatcher = _ActionDispatcher(
            {},
            quit_event,
            engine_action=engine_action,
        )

        await dispatcher._invoke(("tts.kokoro", "stop"))

        engine_action.assert_called_once_with("tts.kokoro", "stop")


class NativeDialogTests(unittest.TestCase):
    def test_windows_question_defaults_to_no_and_accepts_only_yes(self):
        calls = []

        def box(title, message, flags):
            calls.append((title, message, flags))
            return 6

        self.assertTrue(
            dialogs.ask_yes_no("T", "M", system="windows", message_box=box)
        )
        flags = calls[0][2]
        self.assertTrue(flags & 0x4)  # MB_YESNO
        self.assertTrue(flags & 0x100)  # MB_DEFBUTTON2, the "No" button
        self.assertFalse(
            dialogs.ask_yes_no("T", "M", system="windows", message_box=lambda *_: 7)
        )

    def test_zenity_question_command_defaults_to_cancel(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=0, stdout=""))
        answer = dialogs.ask_yes_no(
            "Title",
            "Message & more",
            system="linux",
            environ={"DISPLAY": ":0"},
            which=lambda name: "/usr/bin/zenity" if name == "zenity" else None,
            runner=runner,
        )

        self.assertTrue(answer)
        command = runner.call_args.args[0]
        self.assertEqual("/usr/bin/zenity", command[0])
        self.assertIn("--question", command)
        self.assertIn("--default-cancel", command)
        self.assertIn("--no-markup", command)
        self.assertIn("--text=Message & more", command)

    def test_question_with_only_kdialog_answers_no_but_a_message_still_uses_it(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=0, stdout=""))
        only_kdialog = lambda name: "/usr/bin/kdialog" if name == "kdialog" else None  # noqa: E731

        with self.assertLogs(level="WARNING"):
            answer = dialogs.ask_yes_no(
                "Title",
                "Message",
                system="linux",
                environ={"WAYLAND_DISPLAY": "wayland-0"},
                which=only_kdialog,
                runner=runner,
            )

        self.assertFalse(answer)
        # The question never ran; kdialog only showed the notice.
        self.assertNotIn("--yesno", runner.call_args.args[0])
        self.assertIn("--msgbox", runner.call_args.args[0])
        runner.reset_mock()
        dialogs.show_message(
            "Title",
            "Message",
            system="linux",
            environ={"WAYLAND_DISPLAY": "wayland-0"},
            which=only_kdialog,
            runner=runner,
        )
        self.assertEqual(
            ["/usr/bin/kdialog", "--title", "Title", "--msgbox", "Message"],
            runner.call_args.args[0],
        )

    def test_linux_without_a_tool_or_display_answers_no(self):
        runner = mock.Mock()
        self.assertFalse(
            dialogs.ask_yes_no(
                "T",
                "M",
                system="linux",
                environ={"DISPLAY": ":0"},
                which=lambda _name: None,
                runner=runner,
            )
        )
        self.assertFalse(
            dialogs.ask_yes_no(
                "T",
                "M",
                system="linux",
                environ={},
                which=lambda _name: "/usr/bin/zenity",
                runner=runner,
            )
        )
        runner.assert_not_called()

    def test_macos_question_defaults_to_no_and_escapes_the_text(self):
        runner = mock.Mock(
            return_value=mock.Mock(returncode=0, stdout="button returned:Yes\n")
        )
        answer = dialogs.ask_yes_no(
            "Ti\"tle",
            "Line one\nLine \"two\"",
            system="macos",
            which=lambda name: "/usr/bin/osascript",
            runner=runner,
        )

        self.assertTrue(answer)
        script = runner.call_args.args[0][2]
        self.assertIn('default button "No"', script)
        self.assertIn('cancel button "No"', script)
        self.assertIn('\\"two\\"', script)
        self.assertIn('" & return & "', script)

    def test_macos_cancel_is_no(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=1, stdout=""))
        self.assertFalse(
            dialogs.ask_yes_no(
                "T",
                "M",
                system="macos",
                which=lambda _name: "/usr/bin/osascript",
                runner=runner,
            )
        )

    def test_a_failing_runner_answers_no(self):
        runner = mock.Mock(side_effect=OSError("fixture"))
        with self.assertLogs(level="ERROR"):
            self.assertFalse(
                dialogs.ask_yes_no(
                    "T",
                    "M",
                    system="linux",
                    environ={"DISPLAY": ":0"},
                    which=lambda _name: "/usr/bin/zenity",
                    runner=runner,
                )
            )

    def test_message_uses_the_native_tool_or_logs_without_one(self):
        runner = mock.Mock()
        dialogs.show_message(
            "T",
            "M",
            system="linux",
            environ={"DISPLAY": ":0"},
            which=lambda name: "/usr/bin/zenity",
            runner=runner,
        )
        self.assertIn("--info", runner.call_args.args[0])

        runner.reset_mock()
        with self.assertLogs(level="WARNING"):
            dialogs.show_message(
                "T",
                "M",
                system="linux",
                environ={"DISPLAY": ":0"},
                which=lambda _name: None,
                runner=runner,
            )
        runner.assert_not_called()

    def test_a_question_with_no_safe_dialog_tells_the_user_the_answer_is_no(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=0, stdout=""))
        only_kdialog = lambda name: "/usr/bin/kdialog" if name == "kdialog" else None  # noqa: E731

        with self.assertLogs(level="WARNING"):
            answer = dialogs.ask_yes_no(
                "Title",
                "Quit?",
                system="linux",
                environ={"DISPLAY": ":0"},
                which=only_kdialog,
                runner=runner,
            )

        self.assertFalse(answer)
        command = runner.call_args.args[0]
        self.assertEqual("--msgbox", command[3])
        self.assertIn("Quit?", command[4])
        self.assertIn("install zenity", command[4])

    def test_a_message_tool_that_fails_is_logged(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=1, stdout=""))
        with self.assertLogs(level="WARNING") as logged:
            dialogs.show_message(
                "Title",
                "Body",
                system="linux",
                environ={"DISPLAY": ":0"},
                which=lambda _name: "/usr/bin/zenity",
                runner=runner,
            )
        self.assertIn("Title: Body", logged.output[0])

    def test_windows_message_uses_an_ok_box(self):
        box = mock.Mock(return_value=1)
        dialogs.show_message("T", "M", system="windows", message_box=box)
        self.assertEqual(("T", "M"), box.call_args.args[:2])


if __name__ == "__main__":
    unittest.main()
