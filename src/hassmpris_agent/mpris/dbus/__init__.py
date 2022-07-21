import json
import logging
import time


from typing import Dict, Any, Optional, List, cast, Tuple

from dasbus.error import DBusError
from dasbus.typing import get_native
from dasbus.loop import EventLoop
from dasbus.connection import SessionMessageBus
from dasbus.client.proxy import disconnect_proxy, InterfaceProxy
import threading
from hassmpris_agent.mpris.dbus.chromium import ChromiumObjectHandler
from hassmpris_agent.mpris.dbus.vlc import VLCObjectHandler

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
ALL_NUMERIC_PROPS = {
    "Rate": 1.0,
    "MinimumRate": 1.0,
    "MaximumRate": 1.0,
}
PROP_PLAYBACKSTATUS = "PlaybackStatus"
PROP_RATE = "Rate"
PROP_POSITION = "Position"
PROP_METADATA = "Metadata"

STATUS_PLAYING = "Playing"
STATUS_PAUSED = "Paused"
STATUS_STOPPED = "Stopped"


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


class BaseSeekController(GObject.GObject):

    __gsignals__ = {
        # Emitted when the player being monitored has seeked in a way that is
        # inconsistent with the playback state.  Unlike the Seeked D-Bus MPRIS
        # signal, the position is a float that represents a count of seconds
        # from the start of playback.
        "seeked": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (float,),
        ),
    }

    def __init__(self, player, control_proxy, properties_proxy):
        # type: (Player, InterfaceProxy, InterfaceProxy) -> None
        GObject.GObject.__init__(self)
        self.player = player
        self.control_proxy = control_proxy
        self.properties_proxy = properties_proxy

    def __del__(self) -> None:
        if hasattr(self, "control_proxy"):
            delattr(self, "control_proxy")
        if hasattr(self, "properties_proxy"):
            delattr(self, "properties_proxy")
        if hasattr(self, "player"):
            delattr(self, "player")


class SignalSeekController(BaseSeekController):
    def __init__(self, player, control_proxy, properties_proxy):
        # type: (Player, InterfaceProxy, InterfaceProxy) -> None
        control_proxy.Seeked.connect(
            self._seeked,
        )
        BaseSeekController.__init__(
            self,
            player,
            control_proxy,
            properties_proxy,
        )
        _LOGGER.debug("Signal seek controller chosen for %s", player)

    def _seeked(
        self,
        pos_usec: int,
    ) -> None:
        pos = float(pos_usec) / 1000 / 1000
        self.emit("seeked", pos)

    def __del__(self) -> None:
        if hasattr(self, "control_proxy"):
            try:
                self.control_proxy.Seeked.disconnect(self._seeked)
            except (ImportError, DBusError):
                # Python or the MPRIS bus owner is shutting down.
                pass
        BaseSeekController.__del__(self)


class PollSeekController(BaseSeekController):

    TICK = 1
    _prop_proxy_connected = False

    def __init__(self, player, control_proxy, properties_proxy):
        # type: (Player, InterfaceProxy, InterfaceProxy) -> None
        BaseSeekController.__init__(
            self,
            player,
            control_proxy,
            properties_proxy,
        )
        (
            self._last_checked,
            self._status,
            self._position,
            self._rate,
        ) = self._get_pbstatus_pos_rate()
        # First, connect the signal to the properties proxy.
        try:
            time.sleep(0.05)
            self.properties_proxy.PropertiesChanged.connect(
                self._check_playback_change,
            )
            self._prop_proxy_connected = True
        except DBusError as e:
            raise BadPlayer from e
        self._source = GLib.timeout_add(self.TICK * 1000, self._check_seeked)
        _LOGGER.debug("Poll seek controller chosen for %s", player)

    def _check_playback_change(
        self,
        unused_iface: Any,
        propdict: Dict[str, Any],
        invalidated_properties: Any,
    ) -> None:
        if PROP_PLAYBACKSTATUS in propdict:
            self._check_seeked()

    def _get_pbstatus_pos_rate(self) -> Tuple[float, str, int, float]:
        try:
            props = unpack(
                self.properties_proxy.GetAll(
                    "org.mpris.MediaPlayer2.Player",
                )
            )

        except DBusError as e:
            raise BadPlayer from e

        if PROP_PLAYBACKSTATUS not in props:
            raise BadPlayer("Player properties do not contain PlaybackStatus")
        if PROP_RATE not in props:
            props[PROP_RATE] = 0.0
        if PROP_POSITION not in props:
            props[PROP_POSITION] = 0
        return (
            time.time(),
            props[PROP_PLAYBACKSTATUS],
            props[PROP_POSITION],
            props[PROP_RATE],
        )

    def _check_seeked(self) -> bool:
        if self._source is None:
            return False

        try:
            checked, status, pos, rate = self._get_pbstatus_pos_rate()
        except DBusError:
            # Player is probably gone now.
            GLib.source_remove(self._source)
            self._source = None
            return False

        tick = self.TICK
        slack = self.TICK / 2
        last_pos_s = self._position / 1000 / 1000
        cur_pos_s = float(pos) / 1000 / 1000
        min_rate = min([self._rate, rate])
        max_rate = max([self._rate, rate])
        time_elapsed = checked - self._last_checked

        from_status = self._status
        to_status = status
        from_stopped = from_status == STATUS_STOPPED
        from_paused = from_status == STATUS_PAUSED
        from_playing = from_status == STATUS_PLAYING
        to_stopped = to_status == STATUS_STOPPED
        to_paused = to_status == STATUS_PAUSED
        to_playing = to_status == STATUS_PLAYING

        if from_stopped:
            if to_stopped:
                # checked
                predicted_min = 0.0
                predicted_max = 0.0
            elif to_paused:
                # checked
                predicted_min = 0.0
                predicted_max = 0.0
            elif to_playing:
                # checked
                predicted_min = 0.0
                predicted_max = tick * max_rate
        elif from_paused:
            if to_stopped:
                # checked
                predicted_min = 0.0
                predicted_max = last_pos_s + (time_elapsed * max_rate) + slack
            elif to_paused:
                # checked
                predicted_min = last_pos_s
                predicted_max = last_pos_s
            elif to_playing:
                # checked
                predicted_min = last_pos_s
                predicted_max = last_pos_s + time_elapsed * max_rate + slack
        elif from_playing:
            if to_stopped:
                # checked
                predicted_min = 0.0
                predicted_max = tick * max_rate
            elif to_paused:
                # checked
                predicted_min = last_pos_s
                predicted_max = last_pos_s + (time_elapsed * max_rate) + slack
            elif to_playing:
                # checked
                predicted_min = last_pos_s + (time_elapsed * min_rate) - slack
                predicted_max = last_pos_s + (time_elapsed * max_rate) + slack

        seeked = status in [STATUS_PLAYING, STATUS_PAUSED] and not (
            cur_pos_s >= predicted_min and cur_pos_s <= predicted_max
        )

        if seeked:
            _LOGGER.debug(
                "%s: %s->%s -- seeked, at %.2f, pred [%.2f, %.2f] at rate %s",
                self.player,
                self._status,
                status,
                cur_pos_s,
                predicted_min,
                predicted_max,
                rate,
            )
            self.emit("seeked", cur_pos_s)

        if self._rate != rate or self._status != status or seeked:
            (self._last_checked, self._status, self._position, self._rate,) = (
                checked,
                status,
                pos,
                rate,
            )
        return True

    def __del__(self) -> None:
        if hasattr(self, "_source") and self._source is not None:
            GLib.source_remove(self._source)
            self._source = None
        if self._prop_proxy_connected:
            try:
                self.properties_proxy.PropertiesChanged.disconnect(
                    self._check_playback_change,
                )
            except (ImportError, DBusError):
                # Python or the MPRIS bus owner is shutting down.
                pass
            self._prop_proxy_connected = False
        BaseSeekController.__del__(self)


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
        "seeked": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (float,),
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
        try:
            _LOGGER.debug("entering potential hang by getting properties")
            time.sleep(0.05)
            self.properties_proxy.PropertiesChanged.connect(
                self._properties_changed,
            )
            _LOGGER.debug("potential hang by getting properties avoided")
        except DBusError as e:
            self.cleanup()
            raise BadPlayer from e

        try:
            allprops_variant = self.properties_proxy.GetAll(
                "org.mpris.MediaPlayer2",
            )
        except DBusError as e:
            self.cleanup()
            raise BadPlayer from e

        try:
            allprops = unpack(allprops_variant)
            self.identity: str = allprops.get(
                "Identity",
                allprops.get(
                    "DesktopEntry",
                    self.player_id,
                ),
            )

            kw = {}
            if self.identity.lower().startswith("chrom"):
                kw["handler_factory"] = ChromiumObjectHandler
            if "vlc" in self.identity.lower():
                kw["handler_factory"] = VLCObjectHandler

            self.control_proxy = cast(
                InterfaceProxy,
                bus.get_proxy(
                    player_id,
                    "/org/mpris/MediaPlayer2",
                    interface_name="org.mpris.MediaPlayer2.Player",
                    **kw,
                ),
            )
        except Exception:
            self.cleanup()
            raise

        # FIXME
        try:
            self.seek_controller = SignalSeekController(
                self, self.control_proxy, self.properties_proxy
            )
        except Exception:
            _LOGGER.exception(
                "Signal seek controller did not work, trying simulated one"
            )
            self.seek_controller = PollSeekController(
                self, self.control_proxy, self.properties_proxy
            )
        self.seek_controller.connect("seeked", self._handle_seek)

        self._set_player_properties(
            emit=False,
        )

    def cleanup(self) -> None:
        if hasattr(self, "seek_controller"):
            try:
                self.seek_controller.disconnect_by_func(self._handle_seek)
            except ImportError:
                # Python is shutting down.
                pass
            delattr(self, "seek_controller")

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

    def _handle_seek(self, unused_controller: Any, pos: float) -> None:
        self.emit("seeked", pos)

    def _properties_changed(
        self,
        unused_iface: Any,
        propdict: Dict[str, Any],
        invalidated_properties: Any,
    ) -> None:
        handled: dict[str, Any] = {}

        propdict = dict(propdict.items())

        if PROP_PLAYBACKSTATUS in propdict:
            handled[PROP_PLAYBACKSTATUS] = propdict[PROP_PLAYBACKSTATUS]
            del propdict[PROP_PLAYBACKSTATUS]
        elif PROP_PLAYBACKSTATUS in invalidated_properties:
            handled[PROP_PLAYBACKSTATUS] = GLib.Variant("s", STATUS_STOPPED)
            invalidated_properties.remove(PROP_PLAYBACKSTATUS)

        if PROP_METADATA in propdict:
            handled[PROP_METADATA] = propdict[PROP_METADATA]
            del propdict[PROP_METADATA]
        elif PROP_METADATA in invalidated_properties:
            handled[PROP_METADATA] = {}
            invalidated_properties.remove(PROP_METADATA)

        for prop in ALL_CAN_PROPS + list(ALL_NUMERIC_PROPS):
            if prop in propdict:
                handled[prop] = propdict[prop]
                del propdict[prop]
            elif prop in invalidated_properties:
                handled[prop] = False
                invalidated_properties.remove(prop)

        if propdict:
            dump = json.dumps(
                unpack(propdict),
                sort_keys=True,
                indent=4,
            ).splitlines()
            for line in dump:
                _LOGGER.debug(
                    "%s: unhandled properties changed: %s",
                    self.identity,
                    line,
                )
        if invalidated_properties:
            _LOGGER.debug(
                "%s: unhandled properties invalidated: %s",
                self.identity,
                invalidated_properties,
            )

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
            for prop in ALL_CAN_PROPS + list(ALL_NUMERIC_PROPS):
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
                STATUS_STOPPED,
            )
            self.metadata: str = allplayerprops.get(PROP_METADATA, {})
            for prop in ALL_CAN_PROPS:
                setattr(self, prop, allplayerprops.get(prop, False))
            for prop, defval in ALL_NUMERIC_PROPS.items():
                setattr(self, prop, allplayerprops.get(prop, defval))

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

    def get_position(self) -> float | None:
        try:
            prop = self.properties_proxy.Get(
                "org.mpris.MediaPlayer2.Player", PROP_POSITION,
            )
            return float(unpack(prop)) / 1000 / 1000
        except DBusError:
            return None

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

    def seek(self, offset: float) -> None:
        """Causes the player to seek forward or backward <position> seconds."""
        if hasattr(self, "control_proxy"):
            o = round(offset * 1000 * 1000)
            self.control_proxy.Seek(o)

    def set_position(self, track_id: str, position: float) -> None:
        """Causes the player to seek forward or backward <position> seconds."""
        if hasattr(self, "control_proxy"):
            p = round(position * 1000 * 1000)
            self.control_proxy.SetPosition(track_id, p)


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
        # The following cleanup event is important because
        # otherwise the Player is never cleaned up (its own
        # seek controller retains a reference to it).
        # FIXME: figure out how to solve that problem without
        # explicit cleanups like these.
        player.cleanup()
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
        "player-seeked": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (GObject.TYPE_PYOBJECT, float),
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
                        for ff in [
                            self._player_playback_status_changed,
                            self._player_metadata_changed,
                            self._player_property_changed,
                            self._player_seeked,
                        ]:
                            try:
                                m.disconnect_by_func(ff)
                            except ImportError:
                                pass
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
                            for s, ff in [
                                (
                                    "playback-status-changed",
                                    self._player_playback_status_changed,
                                ),
                                (
                                    "property-changed",
                                    self._player_property_changed,
                                ),
                                (
                                    "metadata-changed",
                                    self._player_metadata_changed,
                                ),
                                (
                                    "seeked",
                                    self._player_seeked,
                                ),
                            ]:
                                m.connect(s, ff)
                        except BadPlayer:
                            msg = (
                                f"Ignoring player {new_owner} — probably badly"
                                " implemented D-Bus spec; please report this"
                                " traceback as a bug (see README.md)."
                            )
                            _LOGGER.exception(msg)
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

    def _player_seeked(self, player: Player, position: float) -> None:
        self.emit(
            "player-seeked",
            player,
            position,
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

    def seek(self, identity_or_player_id: str, offset: float) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).seek(offset)

    def set_position(
        self,
        identity_or_player_id: str,
        track_id: str,
        position: float,
    ) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).set_position(
                track_id,
                position,
            )


if __name__ == "__main__":
    o = {
        PROP_METADATA: GLib.Variant(
            "a{sv}", {"abc": GLib.Variant("i", 1), "def": GLib.Variant("i", 2)}
        )
    }
    print(unpack(o))
