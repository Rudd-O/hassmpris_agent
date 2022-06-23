import contextlib
import subprocess
import sys
import threading

from typing import Any, Tuple, cast, Generator, Callable, List, Optional

from dasbus.connection import SessionMessageBus
import dasbus.client.proxy
import dasbus.error

from hassmpris_agent import config as cfg

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gtk, GLib, GObject  # noqa: E402


def service_unknown(e: dasbus.error.DBusError) -> bool:
    unk = "org.freedesktop.DBus.Error.ServiceUnknown"
    return cast(bool, e.dbus_name == unk)


def run_and_supervise(
    cmd: List[str],
    failure_callback: Callable[[Exception], None],
    ok_return_values: List[Optional[int]],
    wait_for: float = 5.0,
) -> None:
    def prog() -> None:
        try:
            p = subprocess.Popen(cmd)
        except Exception as e:
            GLib.idle_add(failure_callback, e)
            return

        try:
            ret = p.wait(wait_for)  # handle subprocess.TimeoutExpired
        except subprocess.TimeoutExpired:
            ret = None

        if ret not in ok_return_values:
            try:
                raise subprocess.CalledProcessError(ret if ret else 0, cmd)
            except subprocess.CalledProcessError as e:
                GLib.idle_add(failure_callback, e)

    threading.Thread(target=prog, daemon=True).start()


# FIXME add tutorial with images, launchable via button.
# Should go to a website, perhaps Github-powered.


class ErrorDialog(Gtk.MessageDialog):
    def __init__(self, parent: Gtk.Window, title: str, markup: str) -> None:
        Gtk.MessageDialog.__init__(self)
        d = self
        d.set_parent(parent)
        d.set_transient_for(parent)
        d.set_modal(True)
        d.add_button("Close", 0)
        d.set_title(title)
        d.set_markup(markup)
        d.connect("response", lambda *unused: d.close())


class SettingsWindow(Gtk.Window):
    __gsignals__ = {
        "start-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "stop-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "reset-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(
        self,
        state: str,
        paired: bool,
    ) -> None:
        Gtk.Window.__init__(self)

        self.set_title("Multimedia remote control settings")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_spacing(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        slider_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        slider_box.set_spacing(12)
        switch = Gtk.Switch()
        label = Gtk.Label()
        label.set_text("Enable remote control of media")
        slider_box.set_tooltip_text(
            "If turned on, your computer will allow authorized devices "
            "(compatible with Home Assistant) to control multimedia playback "
            "remotely.  Turn it off to stop remote control of media "
            "altogether."
        )
        slider_box.append(switch)
        slider_box.append(label)
        self.slider_box = slider_box
        self.switch = switch

        reset_pairing_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        reset_pairing_button = Gtk.Button()
        reset_pairing_button.set_label("Unpair all Home Assistants")
        reset_pairing_button.set_tooltip_text(
            "This instructs the the remote control service to forget all "
            "paired Home Assistants.  After activating this option, you will "
            "need to pair your Home Assistant instance with this computer "
            "again."
        )
        reset_pairing_box.append(reset_pairing_button)
        reset_pairing_box.set_halign(Gtk.Align.CENTER)
        self.reset_pairing_button = reset_pairing_button

        box.append(slider_box)
        box.append(reset_pairing_box)

        self.set_child(box)

        self.__emit = True
        self.update_state(state, paired)

        self.switch.connect(
            "state-set",
            self.__switch_state_set,
        )
        self.reset_pairing_button.connect(
            "clicked",
            self.__reset_activated,
        )

    def __switch_state_set(self, switch: Gtk.Switch, state: str) -> None:
        if not self.__emit:
            return
        if switch.get_active():
            self.emit("start-requested")
        else:
            self.emit("stop-requested")

    def __reset_activated(self, button: Gtk.Button) -> None:
        if not self.__emit:
            return
        self.emit("reset-requested")

    def update_state(self, state: str, paired: bool) -> None:
        self.__emit = False
        if state == "running":
            self.switch.set_active(True)
            self.slider_box.set_sensitive(True)
            self.reset_pairing_button.set_sensitive(paired)
        elif state == "stopped":
            self.switch.set_active(False)
            self.slider_box.set_sensitive(True)
            self.reset_pairing_button.set_sensitive(False)
        elif state == "starting":
            self.switch.set_active(True)
            self.slider_box.set_sensitive(False)
            self.reset_pairing_button.set_sensitive(False)
        elif state == "stopping":
            self.switch.set_active(False)
            self.slider_box.set_sensitive(False)
            self.reset_pairing_button.set_sensitive(False)
        else:
            assert 0, "not reached"
        self.__emit = True


# FIXME add check if it failed to start.


class SettingsApplication(object):
    def __init__(self) -> None:
        self.settings_window = SettingsWindow("stopped", True)
        self.settings_window.connect(
            "start-requested",
            self.start_requested,
        )
        self.settings_window.connect(
            "stop-requested",
            self.stop_requested,
        )
        self.settings_window.connect(
            "reset-requested",
            self.reset_requested,
        )
        self.settings_window.connect("close-request", self.quit)
        self.settings_window.show()
        self.loop = GLib.MainLoop()
        self.bus = SessionMessageBus()
        state, paired = self.retrieve_service_state()
        self.settings_window.update_state(state, paired)
        self.expecting_restart = False

        self.monitor_proxy = self.bus.get_proxy(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            interface_name="org.freedesktop.DBus",
        )
        self.monitor_proxy.NameOwnerChanged.connect(self.__name_owner_changed)

    def __del__(self) -> None:
        dasbus.client.proxy.disconnect_proxy(self.monitor_proxy)

    def __name_owner_changed(
        self,
        bus_name: str,
        unused_old_owner: str,
        new_owner: str,
    ) -> None:
        if bus_name == "com.rudd_o.HASSMPRIS1":
            if new_owner:
                state, paired = self.retrieve_service_state()
                self.settings_window.update_state(state, paired)
            else:
                self.settings_window.update_state("stopped", False)
                if self.expecting_restart:
                    self.settings_window.update_state("starting", False)
                    self.expecting_restart = False

    @contextlib.contextmanager
    def hassmpris_proxy(self) -> Generator[Any, None, None]:
        proxy = self.bus.get_proxy(
            "com.rudd_o.HASSMPRIS1",
            "/",
            interface_name="com.rudd_o.HASSMPRIS1",
        )
        try:
            yield proxy
        finally:
            dasbus.client.proxy.disconnect_proxy(proxy)

    def retrieve_service_state(self) -> Tuple[str, bool]:

        try:
            with self.hassmpris_proxy() as proxy:
                proxy.Ping()
            # FIXME
            # get_native(proxy.IsPaired())
            # For the time, using this (now non-existent) method
            # is pointless, since we don't keep track of pairings
            # of devices.  If in the future we do, and we allow
            # per-client revocation of devices, then it will make
            # sense to do so.
            paired = True
            state = "running"
        except dasbus.error.DBusError as e:
            if service_unknown(e):
                # The service is not running!
                state = "stopped"
                paired = False
            else:
                raise
        return state, paired

    def run(self) -> None:
        self.loop.run()

    def on_service_failure_start(self, exc: Exception) -> None:
        self.settings_window.update_state("stopped", False)
        title = "Could not start the multimedia remote control agent"
        markup = (
            "The agent failed to start for unknown reasons after 5 seconds."
            f"\n\nError: {exc}"
        )
        ErrorDialog(self.settings_window, title, markup).show()

    def start_service(self) -> None:
        run_and_supervise(
            [sys.executable, cfg.program()],
            self.on_service_failure_start,
            [0, None],
        )

    def start_requested(self, _: Any) -> None:
        self.settings_window.update_state("starting", False)
        self.start_service()
        cfg.setup_autostart()

    def stop_requested(self, _: Any) -> None:
        self.settings_window.update_state("stopping", False)
        cfg.disable_autostart()
        with self.hassmpris_proxy() as proxy:
            proxy.Quit()

    def reset_requested(self, _: Any) -> None:
        self.settings_window.update_state("stopping", False)
        self.expecting_restart = True
        with self.hassmpris_proxy() as proxy:
            proxy.ResetPairings()

    def quit(self, _: Any) -> None:
        self.loop.quit()


def main() -> None:
    app = SettingsApplication()
    app.run()


if __name__ == "__main__":
    main()
