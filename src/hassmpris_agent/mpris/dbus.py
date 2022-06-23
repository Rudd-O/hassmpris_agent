from typing import Dict, Any

from dasbus.typing import get_native
from dasbus.loop import EventLoop
from dasbus.connection import SessionMessageBus
from dasbus.client.proxy import disconnect_proxy
import threading

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib, GObject  # noqa


def unpack(obj: Any) -> Any:
    if isinstance(obj, GLib.Variant):
        obj = get_native(obj)
    if isinstance(obj, dict):
        obj = dict((unpack(k), unpack(v)) for k, v in obj.items())
    elif isinstance(obj, list):
        obj = [unpack(k) for k in obj]
    return obj


class Player(GObject.GObject):
    __gsignals__ = {
        "playback-status-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (str,),
        ),
        "metadata-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
    }

    def __init__(self, bus: SessionMessageBus, player_id: str) -> None:
        GObject.GObject.__init__(self)
        self.player_id = player_id
        self.proxy = bus.get_proxy(
            player_id,
            "/org/mpris/MediaPlayer2",
            interface_name="org.mpris.MediaPlayer2.Player",
        )
        self.propertiesproxy = bus.get_proxy(
            player_id,
            "/org/mpris/MediaPlayer2",
            interface_name="org.freedesktop.DBus.Properties",
        )
        self.playback_status: str = unpack(self.proxy.PlaybackStatus)
        self.metadata: Dict[str, Any] = unpack(self.proxy.Metadata)
        self.propertiesproxy.PropertiesChanged.connect(
            self._properties_changed,
        )
        GLib.idle_add(
            lambda: self.emit("playback-status-changed", self.playback_status)
        )
        if self.metadata:
            GLib.idle_add(lambda: self.emit("metadata-changed", self.metadata))

    def cleanup(self) -> None:
        if hasattr(self, "proxy"):
            disconnect_proxy(self.proxy)
            delattr(self, "proxy")
        if hasattr(self, "propertiesproxy"):
            disconnect_proxy(self.propertiesproxy)
            delattr(self, "propertiesproxy")

    def __del__(self) -> None:
        self.cleanup()

    def _properties_changed(
        self,
        unused_iface: Any,
        dict_of_properties: Dict[str, Any],
        invalidated_properties: Any,
    ) -> None:
        if "PlaybackStatus" in dict_of_properties:
            self._set_playback_status(dict_of_properties["PlaybackStatus"])
        if "Metadata" in dict_of_properties:
            self._set_metadata(dict_of_properties["Metadata"])

    def _set_playback_status(self, playback_status: GLib.Variant) -> None:
        self.playback_status = unpack(playback_status)
        self.emit("playback-status-changed", self.playback_status)

    def _set_metadata(self, metadata: GLib.Variant) -> None:
        self.metadata = unpack(metadata)
        self.emit("metadata-changed", self.metadata)

    def play(self) -> None:
        if hasattr(self, "proxy"):
            self.proxy.Play()

    def pause(self) -> None:
        if hasattr(self, "proxy"):
            self.proxy.Pause()

    def stop(self) -> None:
        if hasattr(self, "proxy"):
            self.proxy.Stop()

    def next(self) -> None:
        if hasattr(self, "proxy"):
            self.proxy.Next()

    def previous(self) -> None:
        if hasattr(self, "proxy"):
            self.proxy.Previous()


def is_mpris(bus_name: str) -> bool:
    return bus_name.startswith("org.mpris.MediaPlayer2")


class DBusMPRISInterface(threading.Thread, GObject.GObject):
    __gsignals__ = {
        "player-appeared": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (str,),
        ),
        "player-gone": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (str,),
        ),
        "player-playback-status-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (str, str),
        ),
        "player-metadata-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (str, GObject.TYPE_PYOBJECT),
        ),
        "mpris-shutdown": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (),
        ),
    }

    def __init__(self) -> None:
        threading.Thread.__init__(self)
        self.daemon = True
        GObject.GObject.__init__(self)

        self.loop = EventLoop()
        self.bus = SessionMessageBus()
        self.players_lock = threading.RLock()
        self.players: Dict[str, Player] = dict()
        self.proxy = self.bus.get_proxy(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            interface_name="org.freedesktop.DBus",
        )

    def _name_owner_changed(
        self,
        bus_name: str,
        old_owner: str,
        new_owner: str,
    ) -> None:
        if is_mpris(bus_name):
            if not new_owner:
                # is gone
                emit = False
                with self.players_lock:
                    if old_owner in self.players:
                        emit = True
                        try:
                            self.players[old_owner].disconnect_by_func(
                                self._player_playback_status_changed
                            )
                        except ImportError:
                            pass
                        try:
                            self.players[old_owner].disconnect_by_func(
                                self._player_metadata_changed
                            )
                        except ImportError:
                            pass
                        del self.players[old_owner]
                if emit:
                    self.emit("player-gone", old_owner)
            if not old_owner:
                # is new
                emit = False
                with self.players_lock:
                    if new_owner not in self.players:
                        emit = True
                        self.players[new_owner] = Player(self.bus, new_owner)
                        self.players[new_owner].connect(
                            "playback-status-changed",
                            self._player_playback_status_changed,
                        )
                        self.players[new_owner].connect(
                            "metadata-changed",
                            self._player_metadata_changed,
                        )
                if emit:
                    self.emit("player-appeared", new_owner)

    def _player_playback_status_changed(
        self,
        player: Player,
        status: str,
    ) -> None:
        self.emit("player-playback-status-changed", player.player_id, status)

    def _player_metadata_changed(
        self, player: Player, metadata: Dict[str, Any]
    ) -> None:
        self.emit("player-metadata-changed", player.player_id, metadata)

    def _initialize_existing_names(self) -> None:
        names = self.proxy.ListNames()
        for bus_name in names:
            if is_mpris(bus_name):
                owner = self.proxy.GetNameOwner(bus_name)
                if owner:
                    self._name_owner_changed(bus_name, "", owner)

    def run(self) -> None:
        self.proxy.NameOwnerChanged.connect(self._name_owner_changed)
        GLib.idle_add(self._initialize_existing_names)
        self.loop.run()
        print("Loop ended")

    def stop_(self) -> None:
        print("Grabbing lock")
        with self.players_lock:
            print("Grabbed lock")
            for name, player in list(self.players.items()):
                del self.players[name]
                player.cleanup()
        print("Quitting loop")
        self.emit("mpris-shutdown")
        self.loop.quit()
        print("Joining thread")
        self.join()
        print("Quit loop")

    def get_players(self) -> Dict[str, Player]:
        with self.players_lock:
            return dict(self.players.items())

    def play(self, player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players[player_id].play()

    def pause(self, player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players[player_id].pause()

    def stop(self, player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players[player_id].stop()

    def next(self, player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players[player_id].next()

    def previous(self, player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players[player_id].previous()


if __name__ == "__main__":
    o = {
        "Metadata": GLib.Variant(
            "a{sv}", {"abc": GLib.Variant("i", 1), "def": GLib.Variant("i", 2)}
        )
    }
    print(unpack(o))
