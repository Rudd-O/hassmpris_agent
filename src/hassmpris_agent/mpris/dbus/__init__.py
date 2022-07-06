import logging
import time


from typing import Dict, Any, Optional, List, cast

from dasbus.error import DBusError
from dasbus.typing import get_native
from dasbus.loop import EventLoop
from dasbus.connection import SessionMessageBus
from dasbus.client.proxy import disconnect_proxy, InterfaceProxy
import threading
from hassmpris_agent.mpris.dbus.chromium import ChromiumObjectHandler

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib, GObject  # noqa


_LOGGER = logging.getLogger(__name__)


def unpack(obj: Any) -> Any:
    if isinstance(obj, GLib.Variant):
        obj = get_native(obj)
    if isinstance(obj, dict):
        obj = dict((unpack(k), unpack(v)) for k, v in obj.items())
    elif isinstance(obj, list):
        obj = [unpack(k) for k in obj]
    return obj


class BadPlayer(DBusError):
    pass


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
        "property-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (
                str,
                GObject.TYPE_PYOBJECT,
            ),
        ),
    }

    def __str__(self) -> str:
        return "<Player %s at %s>" % (self.identity, self.player_id)

    def __init__(self, bus: SessionMessageBus, player_id: str) -> None:
        GObject.GObject.__init__(self)
        self.player_id = player_id
        self.properties_proxy = cast(
            InterfaceProxy,
            bus.get_proxy(
                player_id,
                "/org/mpris/MediaPlayer2",
                interface_name="org.freedesktop.DBus.Properties",
            ),
        )
        time.sleep(0.1)

        _LOGGER.debug("entering potential hang by getting properties")
        try:
            x = self.properties_proxy.GetAll("org.mpris.MediaPlayer2")
        except DBusError as e:
            raise BadPlayer from e
        _LOGGER.debug("potential hang by getting properties avoided")

        allprops = unpack(x)
        self.identity: str = allprops.get(
            "Identity",
            allprops.get(
                "DesktopEntry",
                self.player_id,
            ),
        )

        allplayerprops = unpack(
            self.properties_proxy.GetAll("org.mpris.MediaPlayer2.Player"),
        )
        self.playback_status: str = allplayerprops["PlaybackStatus"]
        self.metadata: str = allplayerprops["Metadata"]
        self.CanControl: bool = allplayerprops.get("CanControl", False)
        self.CanPause: bool = allplayerprops.get("CanPause", False)
        self.CanPlay: bool = allplayerprops.get("CanPlay", False)
        self.CanSeek: bool = allplayerprops.get("CanSeek", False)

        kw = (
            {"handler_factory": ChromiumObjectHandler}
            if self.identity.lower().startswith("chrom")
            else {}
        )
        self.player_proxy = bus.get_proxy(
            player_id,
            "/org/mpris/MediaPlayer2",
            interface_name="org.mpris.MediaPlayer2.Player",
            **kw,
        )

        self.properties_proxy.PropertiesChanged.connect(
            self._properties_changed,
        )

    def cleanup(self) -> None:
        if hasattr(self, "player_proxy"):
            try:
                disconnect_proxy(self.player_proxy)
            except ImportError:
                # Python is shutting down.
                pass
            delattr(self, "player_proxy")
        if hasattr(self, "properties_proxy"):
            try:
                self.properties_proxy.PropertiesChanged.disconnect(
                    self._properties_changed,
                )
                disconnect_proxy(self.properties_proxy)
            except ImportError:
                # Python is shutting down.
                pass
            delattr(self, "properties_proxy")

    def __del__(self) -> None:
        self.cleanup()

    def _properties_changed(
        self,
        unused_iface: Any,
        dict_of_properties: Dict[str, Any],
        invalidated_properties: Any,
    ) -> None:
        # _LOGGER.debug(
        #     "%s: properties changed: %s %s",
        #     self.identity,
        #     dict_of_properties,
        #     invalidated_properties,
        # )
        if "PlaybackStatus" in dict_of_properties:
            self._set_playback_status(dict_of_properties["PlaybackStatus"])
        elif "PlaybackStatus" in invalidated_properties:
            self._set_playback_status("stopped")

        for prop in ["CanControl", "CanPause", "CanPlay", "CanSeek"]:
            if prop in dict_of_properties:
                self._set_property(prop, dict_of_properties[prop])
            elif prop in invalidated_properties:
                self._set_property(prop, False)

        if "Metadata" in dict_of_properties:
            self._set_metadata(dict_of_properties["Metadata"])
            try:
                # Opportunistically update playback status since some players
                # neglect to do so.
                pbstatus = self.player_proxy.PlaybackStatus
            except DBusError:
                # Ah.  The player exited at the time of doing this query.
                pbstatus = GLib.Variant("s", "stopped")
            self._set_playback_status(pbstatus)
        elif "Metadata" in invalidated_properties:
            self._set_metadata({})

    def _set_playback_status(self, playback_status: GLib.Variant) -> None:
        pbstatus = unpack(playback_status)
        if pbstatus != self.playback_status:
            self.playback_status = pbstatus
            self.emit("playback-status-changed", self.playback_status)

    def _set_property(self, name: str, value: GLib.Variant) -> None:
        realvalue = unpack(value)
        if realvalue != getattr(self, name):
            setattr(self, name, realvalue)
            self.emit("property-changed", name, realvalue)

    def _set_metadata(self, metadata: GLib.Variant) -> None:
        self.metadata = unpack(metadata)
        self.emit("metadata-changed", self.metadata)

    def play(self) -> None:
        if hasattr(self, "player_proxy"):
            self.player_proxy.Play()

    def pause(self) -> None:
        if hasattr(self, "player_proxy"):
            self.player_proxy.Pause()

    def stop(self) -> None:
        if hasattr(self, "player_proxy"):
            self.player_proxy.Stop()

    def next(self) -> None:
        if hasattr(self, "player_proxy"):
            self.player_proxy.Next()

    def previous(self) -> None:
        if hasattr(self, "player_proxy"):
            self.player_proxy.Previous()


def is_mpris(bus_name: str) -> bool:
    return bus_name.startswith("org.mpris.MediaPlayer2")


class PlayerCollection(Dict[str, Player]):
    def lookup_by_identity(self, i: str) -> Player:
        for p in self.values():
            if p.identity == i:
                return p
        raise KeyError(i)

    def lookup(self, i: str) -> Player:
        try:
            p = self[i]
            return p
        except KeyError:
            return self.lookup_by_identity(i)

    def add(self, bus: SessionMessageBus, player_id: str) -> Player:
        p = Player(bus, player_id)

        def already(s: str) -> bool:
            try:
                self.lookup_by_identity(s)
                return True
            except KeyError:
                return False

        pattern = p.identity.replace("%", "%%") + " (%d)"
        if already(p.identity):
            count = 1
            while True:
                count = count + 1
                newidentity = pattern % count
                if already(newidentity):
                    continue
                p.identity = newidentity
                break

        self[player_id] = p
        return p

    def remove(self, player: Player) -> None:
        del self[player.player_id]


class DBusMPRISInterface(threading.Thread, GObject.GObject):
    __gsignals__ = {
        "player-appeared": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "player-gone": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (GObject.TYPE_PYOBJECT,),
        ),
        "player-playback-status-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (GObject.TYPE_PYOBJECT, str),
        ),
        "player-property-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (GObject.TYPE_PYOBJECT, str, GObject.TYPE_PYOBJECT),
        ),
        "player-metadata-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (GObject.TYPE_PYOBJECT, GObject.TYPE_PYOBJECT),
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
        self.players = PlayerCollection()
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
                m: Optional[Player] = None
                with self.players_lock:
                    if old_owner in self.players:
                        m = self.players[old_owner]
                        try:
                            m.disconnect_by_func(
                                self._player_playback_status_changed,
                            )
                        except ImportError:
                            pass
                        try:
                            m.disconnect_by_func(
                                self._player_metadata_changed,
                            )
                        except ImportError:
                            pass
                        m.cleanup()
                        self.players.remove(m)
                if m:
                    self.emit(
                        "player-gone",
                        m,
                    )
            if not old_owner:
                # is new
                m = None
                with self.players_lock:
                    if new_owner not in self.players:
                        try:
                            m = self.players.add(self.bus, new_owner)
                            m.connect(
                                "playback-status-changed",
                                self._player_playback_status_changed,
                            )
                            m.connect(
                                "property-changed",
                                self._player_property_changed,
                            )
                            m.connect(
                                "metadata-changed",
                                self._player_metadata_changed,
                            )
                        except BadPlayer:
                            s = "Ignoring %s — badly implemented D-Bus spec"
                            _LOGGER.exception(s, new_owner)
                if m:
                    self.emit(
                        "player-appeared",
                        m,
                    )

    def _player_playback_status_changed(
        self,
        player: Player,
        status: str,
    ) -> None:
        self.emit(
            "player-playback-status-changed",
            player,
            status,
        )

    def _player_metadata_changed(
        self, player: Player, metadata: Dict[str, Any]
    ) -> None:
        self.emit(
            "player-metadata-changed",
            player,
            metadata,
        )

    def _player_property_changed(
        self,
        player: Player,
        name: str,
        value: Any,
    ) -> None:
        self.emit(
            "player-property-changed",
            player,
            name,
            value,
        )

    def _initialize_existing_names(self) -> None:
        names = self.proxy.ListNames()
        for bus_name in names:
            if is_mpris(bus_name):
                owner = self.proxy.GetNameOwner(bus_name)
                if owner:
                    self._name_owner_changed(bus_name, "", owner)

    def run(self) -> None:
        self.proxy.NameOwnerChanged.connect(self._name_owner_changed)
        GLib.idle_add(lambda: self._initialize_existing_names())
        self.loop.run()

    def stop_(self) -> None:
        with self.players_lock:
            for player in list(self.players.values()):
                self.players.remove(player)
                player.cleanup()
        _LOGGER.debug("Quitting loop")
        self.emit("mpris-shutdown")
        self.loop.quit()
        _LOGGER.debug("Joining thread")
        self.join()
        _LOGGER.debug("Quit loop")

    def get_players(self) -> List[Player]:
        with self.players_lock:
            return list(self.players.values())

    def play(self, identity_or_player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).play()

    def pause(self, identity_or_player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).pause()

    def stop(self, identity_or_player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).stop()

    def next(self, identity_or_player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).next()

    def previous(self, identity_or_player_id: str) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).previous()


if __name__ == "__main__":
    o = {
        "Metadata": GLib.Variant(
            "a{sv}", {"abc": GLib.Variant("i", 1), "def": GLib.Variant("i", 2)}
        )
    }
    print(unpack(o))
