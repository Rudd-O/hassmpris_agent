import json
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

ALL_CAN_PROPS = [
    "CanControl",
    "CanPause",
    "CanPlay",
    "CanSeek",
    "CanGoNext",
    "CanGoPrevious",
]
PROP_PLAYBACKSTATUS = "PlaybackStatus"
PROP_METADATA = "Metadata"


def deepequals(one: Any, two: Any) -> bool:
    one_j = json.dumps(one, sort_keys=True)
    two_j = json.dumps(two, sort_keys=True)
    return one_j == two_j


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
        self._sources: list[int] = []

        self.player_id = player_id
        self.properties_proxy = cast(
            InterfaceProxy,
            bus.get_proxy(
                player_id,
                "/org/mpris/MediaPlayer2",
                interface_name="org.freedesktop.DBus.Properties",
            ),
        )

        # First, connect the signal to the properties proxy.
        _LOGGER.debug("entering potential hang by getting properties")
        try:
            time.sleep(0.05)
            self.properties_proxy.PropertiesChanged.connect(
                self._properties_changed,
            )
        except DBusError as e:
            raise BadPlayer from e
        _LOGGER.debug("potential hang by getting properties avoided")

        try:
            try:
                allprops_variant = self.properties_proxy.GetAll(
                    "org.mpris.MediaPlayer2"
                )
            except DBusError as e:
                raise BadPlayer from e

            allprops = unpack(allprops_variant)
            self.identity: str = allprops.get(
                "Identity",
                allprops.get(
                    "DesktopEntry",
                    self.player_id,
                ),
            )

            kw = (
                {"handler_factory": ChromiumObjectHandler}
                if self.identity.lower().startswith("chrom")
                else {}
            )
            self.control_proxy = bus.get_proxy(
                player_id,
                "/org/mpris/MediaPlayer2",
                interface_name="org.mpris.MediaPlayer2.Player",
                **kw,
            )
        except Exception:
            self.properties_proxy.PropertiesChanged.disconnect(
                self._properties_changed,
            )
            disconnect_proxy(self.properties_proxy)
            if hasattr(self, "control_proxy"):
                disconnect_proxy(self.control_proxy)
            raise

        self._set_player_properties(
            emit=False,
        )

    def cleanup(self) -> None:
        if hasattr(self, "control_proxy"):
            try:
                disconnect_proxy(self.control_proxy)
            except (ImportError, DBusError):
                # Python or the MPRIS bus owner is shutting down.
                pass
            delattr(self, "control_proxy")
        if hasattr(self, "properties_proxy"):
            try:
                self.properties_proxy.PropertiesChanged.disconnect(
                    self._properties_changed,
                )
                disconnect_proxy(self.properties_proxy)
            except (ImportError, DBusError):
                # Python or the MPRIS bus owner is shutting down.
                pass
            delattr(self, "properties_proxy")

    def __del__(self) -> None:
        self.cleanup()

    def _properties_changed(
        self,
        unused_iface: Any,
        propdict: Dict[str, Any],
        invalidated_properties: Any,
    ) -> None:
        handled: dict[str, Any] = {}

        if PROP_PLAYBACKSTATUS in propdict:
            handled[PROP_PLAYBACKSTATUS] = propdict[PROP_PLAYBACKSTATUS]
            del propdict[PROP_PLAYBACKSTATUS]
        elif PROP_PLAYBACKSTATUS in invalidated_properties:
            handled[PROP_PLAYBACKSTATUS] = GLib.Variant("s", "Stopped")
            invalidated_properties.remove(PROP_PLAYBACKSTATUS)

        if PROP_METADATA in propdict:
            handled[PROP_METADATA] = propdict[PROP_METADATA]
            del propdict[PROP_METADATA]
        elif PROP_METADATA in invalidated_properties:
            handled[PROP_METADATA] = {}
            invalidated_properties.remove(PROP_METADATA)

        for prop in ALL_CAN_PROPS:
            if prop in propdict:
                handled[prop] = propdict[prop]
                del propdict[prop]
            elif prop in invalidated_properties:
                handled[prop] = False
                invalidated_properties.remove(prop)

        # _LOGGER.debug(
        #     "%s: properties changed: %s",
        #     self.identity,
        # )
        # _LOGGER.debug(
        #     "%s: unhandled properties changed: %s %s",
        #     self.identity,
        #     propdict,
        #     invalidated_properties,
        # )

        # Call the property updates now.
        self._set_player_properties(
            emit=True,
            allplayerprops_variant=handled,
        )

        # Now queue update CanPlay and other properties since some players
        # like VLC sometimes neglect to do so.  We basically wait 50 ms
        # and then query the properties again.
        self._delayed_property_update()

    def _delayed_property_update(self) -> None:
        mysource: list[int] = []

        def inner() -> None:
            try:
                self._set_player_properties(emit=True)
            except DBusError:
                # Player is gone:
                pass
            for source in mysource:
                GLib.source_remove(source)
                if source in self._sources:
                    self._sources.remove(source)

        for source in self._sources:
            GLib.source_remove(source)
        self._sources = []
        source = GLib.timeout_add(50, inner)
        self._sources.append(source)
        mysource.append(source)

    def _set_player_properties(
        self,
        emit: bool = True,
        allplayerprops_variant: Any | None = None,
    ) -> None:
        if not allplayerprops_variant:
            if not hasattr(self, "properties_proxy"):
                return
            try:
                allplayerprops_variant = self.properties_proxy.GetAll(
                    "org.mpris.MediaPlayer2.Player",
                )
            except DBusError as e:
                raise BadPlayer from e
        if emit:
            if PROP_METADATA in allplayerprops_variant:
                self._set_metadata(
                    allplayerprops_variant[PROP_METADATA],
                )
            if PROP_PLAYBACKSTATUS in allplayerprops_variant:
                self._set_playback_status(
                    allplayerprops_variant[PROP_PLAYBACKSTATUS],
                )
            for prop in ALL_CAN_PROPS:
                if prop in allplayerprops_variant:
                    self._set_property(prop, allplayerprops_variant[prop])
        else:
            # This branch is only called upon object initialization.
            # Accordingly, since we assume we are getting all the player
            # properties known through D-Bus, then we take the liberty
            # of updating all even with default values.
            allplayerprops = unpack(allplayerprops_variant)
            self.playback_status: str = allplayerprops.get(
                PROP_PLAYBACKSTATUS,
                "Stopped",
            )
            self.metadata: str = allplayerprops.get(PROP_METADATA, {})
            for prop in ALL_CAN_PROPS:
                setattr(self, prop, allplayerprops.get(prop, False))

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
        m = unpack(metadata)
        if not deepequals(m, self.metadata):
            self.metadata = m
            self.emit("metadata-changed", self.metadata)

    def play(self) -> None:
        if hasattr(self, "control_proxy"):
            self.control_proxy.Play()

    def pause(self) -> None:
        if hasattr(self, "control_proxy"):
            self.control_proxy.Pause()

    def stop(self) -> None:
        if hasattr(self, "control_proxy"):
            self.control_proxy.Stop()

    def next(self) -> None:
        if hasattr(self, "control_proxy"):
            self.control_proxy.Next()

    def previous(self) -> None:
        if hasattr(self, "control_proxy"):
            self.control_proxy.Previous()

    def seek(self, position: float) -> None:
        if hasattr(self, "control_proxy"):
            p = round(position * 1000 * 1000)
            self.control_proxy.Seek(p)


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

    def seek(self, identity_or_player_id: str, position: float) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).seek(position)


if __name__ == "__main__":
    o = {
        PROP_METADATA: GLib.Variant(
            "a{sv}", {"abc": GLib.Variant("i", 1), "def": GLib.Variant("i", 2)}
        )
    }
    print(unpack(o))
