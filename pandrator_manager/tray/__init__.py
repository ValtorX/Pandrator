"""Optional non-Qt tray client."""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import subprocess
import sys
import threading
from typing import NotRequired, TypedDict

from ..client import ManagerApiError, ManagerClient
from ..context import WorkspaceLayout
from ..desktop import host_process_environment, open_desktop_url
from ..tray_autostart import _tray_command as _tray_command
from ..tray_autostart import configure_tray_autostart as configure_tray_autostart
from ..tray_lifecycle import (
    _claim_tray_instance as _claim_tray_instance,
)
from ..tray_lifecycle import (
    _tray_instance_path as _tray_instance_path,
)
from ..tray_lifecycle import (
    stop_tray_background as stop_tray_background,
)
from ..workspace_selection import default_workspace
from . import dialogs
from .menu import (
    EngineMenuSnapshot,
    build_engine_menu_snapshot,
    unavailable_engine_snapshot,
)


def tray_available() -> tuple[bool, str]:
    if sys.platform.startswith("linux") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        try:
            from .status_notifier import run_status_notifier

            del run_status_notifier
            return True, ""
        except ImportError:
            pass
    # pystray selects and initializes its X11 backend during import.  Check
    # session availability first so a headless Linux host gets a normal
    # optional-feature result instead of an Xlib traceback.
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return False, "No graphical desktop session is available."
    try:
        import pystray
        from PIL import Image

        del pystray, Image
    except ImportError:
        return False, "Install the optional pandrator-manager[tray] extra."
    except Exception as error:
        return (
            False,
            f"The desktop tray backend could not initialize ({type(error).__name__}).",
        )
    return True, ""


class _TrayProcessOptions(TypedDict):
    env: dict[str, str]
    stdin: int
    stdout: int
    stderr: int
    close_fds: bool
    creationflags: NotRequired[int]
    start_new_session: NotRequired[bool]


def _tray_process_options() -> _TrayProcessOptions:
    kwargs: _TrayProcessOptions = {
        "env": host_process_environment(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    return kwargs


def launch_tray_background(layout: WorkspaceLayout) -> tuple[bool, str]:
    available, reason = tray_available()
    if not available:
        return False, reason
    kwargs = _tray_process_options()
    try:
        process = subprocess.Popen(_tray_command(layout), **kwargs)
    except OSError as error:
        return False, str(error)
    threading.Thread(
        target=process.wait,
        name=f"pandrator-tray-reaper-{process.pid}",
        daemon=True,
    ).start()
    return True, ""


_QUIT_QUESTIONS = {
    "application_busy": (
        "Pandrator has queued or running work. Quitting stops it.\n\n"
        "Quit Pandrator anyway?"
    ),
    "application_activity_unknown": (
        "Pandrator could not check whether it has queued or running work. "
        "Quitting would stop any it has.\n\nQuit Pandrator anyway?"
    ),
}
_QUIT_UNCONFIRMED = (
    "Pandrator could not confirm the quit. If it is still running, try again."
)
_QUIT_MESSAGES = {
    "maintenance_in_progress": (
        "The Manager is busy starting services or running maintenance. "
        "Try again in a moment."
    ),
    "manager_stopping": "The Manager is already shutting down.",
}


def _error_code(error: ManagerApiError) -> str:
    payload = error.payload if isinstance(error.payload, dict) else {}
    detail = payload.get("error")
    return str(detail.get("code") or "") if isinstance(detail, dict) else ""


class TrayApplication:
    def __init__(self, client: ManagerClient) -> None:
        self.client = client
        self.icon = None
        self._engine_snapshot = unavailable_engine_snapshot("Loading engine status")
        self._menu_refresh_stop = threading.Event()
        self._quit_lock = threading.Lock()

    def _image(self):
        from .icon import load_tray_icon

        return load_tray_icon()

    def _status_label(self, _item=None) -> str:
        return f"Pandrator Manager — {self._engine_snapshot.summary}"

    def engine_snapshot(self) -> EngineMenuSnapshot:
        try:
            status = self.client.status()
            snapshot = build_engine_menu_snapshot(
                self.client.components(),
                self.client.services(),
                busy=bool(status.get("active_operation_id")),
            )
        except Exception:
            logging.exception("Could not refresh desktop tray engine status.")
            snapshot = unavailable_engine_snapshot()
        self._engine_snapshot = snapshot
        return snapshot

    def _engine_group_label(self, _item=None) -> str:
        snapshot = self._engine_snapshot
        if not snapshot.available or not snapshot.items:
            return "Speech engines"
        return f"Speech engines — {snapshot.running_count}/{len(snapshot.items)} running"

    def _mcp_group_label(self, _item=None) -> str:
        mcp = self._engine_snapshot.mcp
        return f"MCP server — {mcp.state_label}"

    def control_engine(
        self,
        service_id: str,
        action: str,
        icon=None,
        _item=None,
    ) -> None:
        try:
            self.client.runtime(action, (service_id,))
        except Exception:
            logging.exception(
                "Desktop tray could not %s engine %s.",
                action,
                service_id,
            )
        self.engine_snapshot()
        selected = icon or self.icon
        if selected is not None:
            selected.title = self._status_label()
            selected.update_menu()

    def _pystray_engine_items(self):
        import pystray

        snapshot = self._engine_snapshot
        if not snapshot.available:
            return (
                pystray.MenuItem(
                    snapshot.message or "Engine status unavailable",
                    None,
                    enabled=False,
                ),
            )
        if not snapshot.items:
            return (
                pystray.MenuItem(
                    "No optional engines installed",
                    None,
                    enabled=False,
                ),
            )

        result = []
        for engine in snapshot.items:
            if engine.action is None:
                actions = pystray.Menu(
                    pystray.MenuItem(
                        engine.action_label,
                        None,
                        enabled=False,
                    )
                )
            else:
                callback = functools.partial(
                    self.control_engine,
                    engine.service_id,
                    engine.action,
                )
                actions = pystray.Menu(
                    pystray.MenuItem(
                        engine.action_label,
                        callback,
                        enabled=engine.enabled,
                    )
                )
            result.append(
                pystray.MenuItem(
                    f"{engine.label} — {engine.state_label}",
                    actions,
                )
            )
        return tuple(result)

    def _pystray_mcp_items(self):
        import pystray

        mcp = self._engine_snapshot.mcp
        callback = (
            functools.partial(
                self.control_engine,
                mcp.service_id,
                mcp.action,
            )
            if mcp.action is not None
            else None
        )
        return (
            pystray.MenuItem(
                mcp.action_label,
                callback,
                enabled=mcp.enabled,
            ),
        )

    def _pystray_menu_items(self):
        import pystray

        return (
            pystray.MenuItem(self._status_label, None, enabled=False),
            pystray.MenuItem(
                "Open Pandrator",
                self.open_pandrator,
                default=True,
            ),
            pystray.MenuItem(
                "Open setup / recovery",
                self.open_recovery,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start Pandrator", self.start_pandrator),
            pystray.MenuItem("Stop Pandrator", self.stop_pandrator),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                self._mcp_group_label,
                pystray.Menu(self._pystray_mcp_items),
            ),
            pystray.MenuItem(
                self._engine_group_label,
                pystray.Menu(self._pystray_engine_items),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Pandrator", self.quit_pandrator_async),
            pystray.MenuItem("Close tray only", self.quit_tray),
        )

    def _refresh_pystray_menu(self, icon) -> None:
        icon.visible = True
        while not self._menu_refresh_stop.is_set():
            self.engine_snapshot()
            icon.title = self._status_label()
            icon.update_menu()
            if self._menu_refresh_stop.wait(2.5):
                break

    def open_pandrator(self, _icon=None, _item=None) -> None:
        for service in self.client.services():
            if service["id"] == "pandrator.api" and service.get("endpoint"):
                open_desktop_url(service["endpoint"])
                return
        self.open_recovery()

    def open_recovery(self, _icon=None, _item=None) -> None:
        open_desktop_url(self.client.recovery_url())

    def start_pandrator(self, _icon=None, _item=None) -> None:
        self.client.application("start")

    def stop_pandrator(self, _icon=None, _item=None) -> None:
        self.client.application("stop")

    def quit_pandrator_async(self, icon=None, _item=None) -> None:
        # The question can stay open for a long time, so keep the menu thread
        # free while it is asked.
        threading.Thread(
            target=self.quit_pandrator,
            args=(icon,),
            name="pandrator-tray-quit",
            daemon=True,
        ).start()

    def quit_pandrator(self, icon=None, _item=None) -> bool:
        """Ask the Manager to stop every service and itself, then close the tray.

        Returns True when the Manager accepted the quit. It asks first when
        work is active or could not be checked, and stays open on any refusal.
        """

        if not self._quit_lock.acquire(blocking=False):
            return False
        try:
            stop_active_work = False
            try:
                # The Manager may have crashed or been replaced since the tray
                # started, so find the live one before asking it to quit.
                found = ManagerClient.discover(self.client.layout.workspace)
                if found is not self.client:
                    try:
                        self.client.session.close()
                    except Exception:
                        logging.debug("Could not close the replaced Manager session.")
                self.client = found
            except Exception:
                logging.exception("Desktop tray could not find the Manager.")
                dialogs.show_message("Quit Pandrator", _QUIT_UNCONFIRMED)
                return False
            while True:
                try:
                    self.client.quit_manager(stop_active_work=stop_active_work)
                except ManagerApiError as error:
                    code = _error_code(error)
                    if not stop_active_work and code in _QUIT_QUESTIONS:
                        if not dialogs.ask_yes_no(
                            "Quit Pandrator",
                            _QUIT_QUESTIONS[code],
                        ):
                            return False
                        stop_active_work = True
                        continue
                    reason = error.args[0] if error.args else None
                    dialogs.show_message(
                        "Quit Pandrator",
                        _QUIT_MESSAGES.get(
                            code,
                            f"Pandrator could not quit: {reason}"
                            if reason
                            else f"Pandrator could not quit ({code or error.status_code}).",
                        ),
                    )
                    return False
                except Exception:
                    logging.exception("Desktop tray could not ask the Manager to quit.")
                    dialogs.show_message("Quit Pandrator", _QUIT_UNCONFIRMED)
                    return False
                break
            self._menu_refresh_stop.set()
            selected = icon or self.icon
            if selected is not None:
                selected.stop()
            return True
        finally:
            self._quit_lock.release()

    def quit_tray(self, icon=None, _item=None) -> None:
        self._menu_refresh_stop.set()
        selected = icon or self.icon
        if selected is not None:
            selected.stop()

    def run(self) -> None:
        import pystray

        self.icon = pystray.Icon(
            "pandrator",
            self._image(),
            "Pandrator Manager",
            menu=pystray.Menu(self._pystray_menu_items),
        )
        self.icon.run(setup=self._refresh_pystray_menu)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pandrator-tray")
    parser.add_argument("--workspace", default=str(default_workspace()))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--remove-autostart", action="store_true")
    args = parser.parse_args(argv)
    layout = WorkspaceLayout.from_value(args.workspace)
    if args.install_autostart:
        print(configure_tray_autostart(layout, enabled=True))
        return 0
    if args.remove_autostart:
        print(configure_tray_autostart(layout, enabled=False))
        return 0
    available, reason = tray_available()
    if args.check:
        print("available" if available else f"unavailable: {reason}")
        return 0 if available else 1
    if not available:
        print(f"Pandrator tray is unavailable: {reason}", file=sys.stderr)
        return 1
    instance = _claim_tray_instance(layout)
    if instance is None:
        return 0
    try:
        client = ManagerClient.ensure_running(layout.workspace)
        application = TrayApplication(client)
        if sys.platform.startswith("linux") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            try:
                from .status_notifier import run_status_notifier

                run_status_notifier(
                    {
                        "open_pandrator": application.open_pandrator,
                        "open_recovery": application.open_recovery,
                        "start_pandrator": application.start_pandrator,
                        "stop_pandrator": application.stop_pandrator,
                        "quit_pandrator": application.quit_pandrator,
                    },
                    engine_provider=application.engine_snapshot,
                    engine_action=application.control_engine,
                )
                return 0
            except (ImportError, RuntimeError) as error:
                if (
                    os.environ.get("WAYLAND_DISPLAY")
                    or os.environ.get("XDG_SESSION_TYPE", "").casefold() == "wayland"
                    or not os.environ.get("DISPLAY")
                ):
                    print(
                        f"Pandrator native tray is unavailable: {error}",
                        file=sys.stderr,
                    )
                    return 1
        application.run()
    finally:
        try:
            identity = json.loads(instance.read_text(encoding="ascii"))
            if isinstance(identity, dict) and int(identity.get("pid", 0)) == os.getpid():
                instance.unlink()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    return 0
