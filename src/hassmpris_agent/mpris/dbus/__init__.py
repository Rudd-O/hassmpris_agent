import json
import logging
import time


from typing import Dict, Any, Optional, List, cast, Tuple, Callable

from dasbus.error import DBusError
from dasbus.typing import get_native
from dasbus.loop import EventLoop
from dasbus.connection import SessionMessageBus
from dasbus.client.proxy import (
    disconnect_proxy,
    InterfaceProxy,
    get_object_handler as goh,
)
import threading
from hassmpris_agent.mpris.dbus.chromium import ChromiumObjectHandler
from hassmpris_agent.mpris.dbus.vlc import VLCObjectHandler

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib, GObject  # noqa


_LOGGER = logging.getLogger(__name__)

STATUS_PLAYING = "Playing"
STATUS_PAUSED = "Paused"
STATUS_STOPPED = "Stopped"

PROP_PLAYBACKSTATUS = "PlaybackStatus"
PROP_MINIMUM_RATE = "MinimumRate"
PROP_MAXIMUM_RATE = "MaximumRate"
PROP_RATE = "Rate"
PROP_POSITION = "Position"
PROP_METADATA = "Metadata"

ALL_CAN_PROPS = {
    "CanControl": False,
    "CanPause": False,
    "CanPlay": False,
    "CanSeek": False,
    "CanGoNext": False,
    "CanGoPrevious": False,
}
ALL_NUMERIC_PROPS = {
    PROP_MINIMUM_RATE: 1.0,
    PROP_MAXIMUM_RATE: 1.0,
    PROP_RATE: 1.0,
}
ALL_OTHER_PROPS = {
    PROP_PLAYBACKSTATUS: STATUS_STOPPED,
    PROP_METADATA: lambda: dict(),
}
ALL_PROPS = ALL_OTHER_PROPS | ALL_CAN_PROPS | ALL_NUMERIC_PROPS


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


def test_properties_proxy_for_timeout(proxy: InterfaceProxy) -> None:
    """Check for timeouts."""
    handler = goh(proxy)
    try:
        _LOGGER.debug("Entering potential hang as properties are retrieved")
        time.sleep(0.05)
        handler._call_method(
            "org.freedesktop.DBus.Properties",
            "Get",
            "(ss)",
            "(v)",
            "org.mpris.MediaPlayer2",
            "Rate",
            timeout=3000,
        )
    except GLib.GError as e:
        if e.code == 24 and e.domain == "g-io-error-quark":
            raise TimeoutError("Timed out retrieving MPRIS property") from e
        raise


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

    def __init__(self, control_proxy, properties_proxy):
        # type: (Player, InterfaceProxy, InterfaceProxy) -> None
        GObject.GObject.__init__(self)
        self.control_proxy = control_proxy
        self.properties_proxy = properties_proxy

    def __del__(self) -> None:
        _LOGGER.debug("Cleaning up %s controller", self)
        if hasattr(self, "control_proxy"):
            delattr(self, "control_proxy")
        if hasattr(self, "properties_proxy"):
            delattr(self, "properties_proxy")


class SignalSeekController(BaseSeekController):
    def __init__(self, control_proxy, properties_proxy):
        # type: (InterfaceProxy, InterfaceProxy) -> None
        BaseSeekController.__init__(
            self,
            control_proxy,
            properties_proxy,
        )
        _LOGGER.debug("Signal seek controller chosen")
        self.started = False

    def start(self) -> None:
        if not self.started:
            self.control_proxy.Seeked.connect(
                self._seeked,
            )
            self.started = True

    def stop(self) -> None:
        if self.started:
            try:
                self.control_proxy.Seeked.disconnect(self._seeked)
            except (ImportError, DBusError):
                # Python or the MPRIS bus owner is shutting down.
                pass
            self.started = False

    def _seeked(
        self,
        pos_usec: int,
    ) -> None:
        pos = float(pos_usec) / 1000 / 1000
        self.emit("seeked", pos)

    def __del__(self) -> None:
        self.stop()
        BaseSeekController.__del__(self)


class PollSeekController(BaseSeekController):
    TICK = 1
    _prop_proxy_connected = False

    def __init__(self, control_proxy, properties_proxy):
        # type: (InterfaceProxy, InterfaceProxy) -> None
        BaseSeekController.__init__(
            self,
            control_proxy,
            properties_proxy,
        )
        (
            self._last_checked,
            self._status,
            self._position,
            self._rate,
        ) = self._get_pbstatus_pos_rate()
        _LOGGER.debug("Poll seek controller chosen")
        self._source: Any = None

    def start(self) -> None:
        if self._source is None:
            time.sleep(0.05)
            self.properties_proxy.PropertiesChanged.connect(
                self._check_playback_change,
            )
            self._source = GLib.timeout_add(self.TICK * 1000, self._check_seeked)

    def stop(self) -> None:
        if self._source is not None:
            try:
                self.properties_proxy.PropertiesChanged.disconnect(
                    self._check_playback_change,
                )
            except (ImportError, DBusError):
                # Python or the MPRIS bus owner is shutting down.
                pass
            GLib.source_remove(self._source)
            self._source = None

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
            raise BadPlayer("Cannot get all properties for PSK") from e

        if PROP_PLAYBACKSTATUS not in props:
            raise BadPlayer("Player properties do not contain PlaybackStatus")
        if PROP_RATE not in props:
            props[PROP_RATE] = 1.0
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
            self.stop()
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
            (
                self._last_checked,
                self._status,
                self._position,
                self._rate,
            ) = (
                checked,
                status,
                pos,
                rate,
            )
        return True

    def __del__(self) -> None:
        self.stop()
        BaseSeekController.__del__(self)


class BasePropertiesController(GObject.GObject):
    __gsignals__ = {
        # Emitted when the player being monitored has had its
        # properties changed.
        "properties-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (object,),
        ),
    }

    def __init__(self, properties_proxy: InterfaceProxy) -> None:
        GObject.GObject.__init__(self)
        _LOGGER.debug("Using %s", self.__class__.__name__)
        self.properties_proxy = properties_proxy

    def __del__(self) -> None:
        _LOGGER.debug("Cleaning up %s controller", self)
        if hasattr(self, "properties_proxy"):
            delattr(self, "properties_proxy")

    def get_properties(self) -> GLib.Variant:
        if not hasattr(self, "properties_proxy"):
            return
        try:
            return self.properties_proxy.GetAll(
                "org.mpris.MediaPlayer2.Player",
            )
        except DBusError as e:
            raise BadPlayer(
                "Cannot get MediaPlayer2.Player properties",
            ) from e

    def get_position(self) -> float | None:
        try:
            prop = self.properties_proxy.Get(
                "org.mpris.MediaPlayer2.Player",
                PROP_POSITION,
            )
            return float(unpack(prop)) / 1000 / 1000
        except DBusError:
            return None

    def get_entity_properties(self) -> GLib.Variant:
        if not hasattr(self, "properties_proxy"):
            return
        try:
            return self.properties_proxy.GetAll(
                "org.mpris.MediaPlayer2",
            )
        except DBusError as e:
            raise BadPlayer(
                "Cannot get MediaPlayer2 properties",
            ) from e


class SignalPropertiesController(BasePropertiesController):
    def __init__(self, properties_proxy: InterfaceProxy) -> None:
        """
        Creates a property retrieval controller, taking ownership of the
        properties proxy.
        """
        self._sources: list[int] = []
        BasePropertiesController.__init__(self, properties_proxy)
        self.started = False

    def start(self) -> None:
        self.properties_proxy.PropertiesChanged.connect(
            self._properties_changed,
        )
        self.started = True

    def _properties_changed(
        self,
        unused_iface: Any,
        unused_propdict: Dict[str, Any],
        unused_invalidated_properties: Any,
    ) -> None:
        # Queue update CanPlay and other properties since some players
        # like VLC sometimes neglect to do so.  We basically wait 50 ms
        # and then query the properties again.
        self._delayed_property_update()

    def _delayed_property_update(self) -> None:
        def inner() -> None:
            try:
                props = self.get_properties()
                self.emit("properties-changed", props)
            except DBusError:
                # Player is gone:
                pass
            for source in self._sources:
                GLib.source_remove(source)
                if source in self._sources:
                    self._sources.remove(source)

        source = GLib.timeout_add(50, inner)
        self._sources.append(source)

    def stop(self) -> None:
        for source in self._sources:
            GLib.source_remove(source)
        self._sources = []
        if self.started:
            self.properties_proxy.PropertiesChanged.disconnect(self._properties_changed)
        self.started = False

    def __del__(self) -> None:
        self.stop()
        BasePropertiesController.__del__(self)


class PollPropertiesController(BasePropertiesController):
    def __init__(self, properties_proxy: InterfaceProxy):
        """
        Creates a property retrieval controller, taking ownership of the
        properties proxy.
        """
        self._sources: list[int] = []
        BasePropertiesController.__init__(self, properties_proxy)
        self.started = False

    def start(self) -> None:
        self._sources.append(GLib.timeout_add(2000, self._periodic_property_update))
        self.started = True

    def _periodic_property_update(self) -> bool:
        try:
            props = self.get_properties()
        except (ImportError, DBusError):
            self.stop()
            # Python or the MPRIS bus owner is shutting down.
            return False
        self.emit("properties-changed", props)
        return True

    def stop(self) -> None:
        for source in self._sources:
            GLib.source_remove(source)
        self._sources = []
        self.started = False

    def __del__(self) -> None:
        self.stop()
        BasePropertiesController.__del__(self)


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
        _LOGGER.debug("Discovering player %s", player_id)
        GObject.GObject.__init__(self)
        self.player_id = player_id
        self._cleanuppers: list[tuple[str, Callable[[], Any]]] = []

        def to_cleanup(name: str, func: Callable[[], Any]) -> None:
            self._cleanuppers.append((name, func))

        try:
            prop_proxy = cast(
                InterfaceProxy,
                bus.get_proxy(
                    player_id,
                    "/org/mpris/MediaPlayer2",
                    interface_name="org.freedesktop.DBus.Properties",
                ),
            )
            to_cleanup(
                "disconnect properties proxy",
                lambda: disconnect_proxy(prop_proxy),
            )

            # Test the properties proxy.
            try:
                test_properties_proxy_for_timeout(prop_proxy)
            except TimeoutError as e:
                raise BadPlayer("Timeout error retrieving property") from e
            except Exception:
                _LOGGER.debug(
                    "Ignoring non-timeout property get error -- "
                    "any fatal errors will be handled downstream"
                )

            try:
                prop_proxy_controller = SignalPropertiesController(prop_proxy)
            except DBusError as e:
                raise BadPlayer("Cannot set up properties changed mechanism") from e

            try:
                _LOGGER.debug("Getting all player properties")
                entity_props = unpack(prop_proxy_controller.get_entity_properties())
                player_props = prop_proxy_controller.get_properties()
                _LOGGER.debug("Got player properties")
            except DBusError as e:
                raise BadPlayer("Cannot get player properties") from e

            self.identity: str = entity_props.get(
                "Identity",
                entity_props.get(
                    "DesktopEntry",
                    self.player_id,
                ),
            )
            _LOGGER.info(
                "Player with bus ID %s has identity %s",
                player_id,
                self.identity,
            )

            if entity_props.get("DesktopEntry") == "org.gnome.Totem":
                # This player does not correctly emit the properties-changed
                # signal, so we must default to using the other.
                prop_proxy_controller = PollPropertiesController(prop_proxy)

            prop_proxy_controller.connect(
                "properties-changed",
                self._handle_properties_changed,
            )
            to_cleanup(
                "disconnect handle properties changed",
                lambda: prop_proxy_controller.disconnect_by_func(
                    self._handle_properties_changed
                ),
            )

            prop_proxy_controller.start()
            to_cleanup(
                "stop properties proxy controller",
                lambda: prop_proxy_controller.stop(),
            )

            kw = {}
            if self.identity.lower().startswith("chrom"):
                kw["handler_factory"] = ChromiumObjectHandler
            if "vlc" in self.identity.lower():
                kw["handler_factory"] = VLCObjectHandler

            control_proxy = cast(
                InterfaceProxy,
                bus.get_proxy(
                    player_id,
                    "/org/mpris/MediaPlayer2",
                    interface_name="org.mpris.MediaPlayer2.Player",
                    **kw,
                ),
            )
            to_cleanup(
                "disconnect control proxy",
                lambda: disconnect_proxy(control_proxy),
            )

            try:
                if entity_props.get("DesktopEntry") == "org.gnome.Totem":
                    raise Exception("Totem cannot use signal seek controller")
                seek_controller = SignalSeekController(control_proxy, prop_proxy)
            except Exception:
                _LOGGER.exception(
                    "Signal seek controller did not work, trying simulated one"
                )
                seek_controller = PollSeekController(control_proxy, prop_proxy)

            seek_controller.connect("seeked", self._handle_seek)
            to_cleanup(
                "disconnect seeked from seek controller",
                lambda: seek_controller.disconnect_by_func(self._handle_seek),
            )

            seek_controller.start()
            to_cleanup("stop seek controller", lambda: seek_controller.stop())

            self._update_player_properties(player_props, init=True)

            self.control_proxy = control_proxy
            to_cleanup("deref control proxy", lambda: delattr(self, "control_proxy"))

            self.properties_proxy_controller = prop_proxy_controller
            to_cleanup(
                "deref properties proxy controller",
                lambda: delattr(self, "properties_proxy_controller"),
            )

        except Exception:
            self.cleanup()

    def cleanup(self) -> None:
        while self._cleanuppers:
            name, cleanupper = self._cleanuppers.pop()
            _LOGGER.debug("Cleanup: %s", name)
            try:
                cleanupper()
            except (ImportError, DBusError) as exc:
                _LOGGER.debug("%s running %s", exc, name)
            except Exception:
                _LOGGER.exception("Cleanup error running %s", name)

    def __del__(self) -> None:
        self.cleanup()

    def _handle_seek(self, unused_controller: Any, pos: float) -> None:
        self.emit("seeked", pos)

    def _handle_properties_changed(
        self,
        unused_controller: Any,
        props: GLib.Variant,
    ) -> None:
        self._update_player_properties(props)

    def _update_player_properties(
        self,
        allplayerprops_variant: GLib.Variant,
        init: bool = False,
    ) -> None:
        allplayerprops = unpack(allplayerprops_variant)
        for prop, defval in ALL_PROPS.items():
            if prop in allplayerprops:
                # We have this property.  We update the value we have locally,
                # taking care not to emit anything during initialization.
                self._set_property(prop, allplayerprops[prop], init)
            elif init:
                # We are initializing.
                # Accordingly, since we assume we are getting all the player
                # properties known through D-Bus, then we take the liberty
                # of updating all even with default values.
                if callable(defval):
                    defval = defval()
                self._set_property(prop, defval, init)

    def _set_property(
        self,
        prop: str,
        value: Any,
        init: bool = False,
    ) -> None:
        # Checks for validity.
        if prop == PROP_RATE and value == 0:
            _LOGGER.warning(
                "%s: %s cannot be %s, ignoring and pretending it is 1.0",
                self.identity,
                prop,
                value,
            )
            value = 1.0
        if init:
            # We are not emitting anything during initialization.
            setattr(self, prop, value)
            return
        if not deepequals(value, getattr(self, prop)):
            setattr(self, prop, value)
            if prop == PROP_PLAYBACKSTATUS:
                self.emit("playback-status-changed", value)
            elif prop == PROP_METADATA:
                self.emit("metadata-changed", value)
            else:
                self.emit("property-changed", prop, value)

    def get_position(self) -> float | None:
        return self.properties_proxy_controller.get_position()

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
        """Causes the player to seek forward or backward <offset> seconds."""
        if hasattr(self, "control_proxy"):
            o = round(offset * 1000 * 1000)
            self.control_proxy.Seek(o)

    def seek_absolute(self, position: float) -> None:
        """Causes the player to seek to <position> seconds in current track."""
        if hasattr(self, "control_proxy"):
            curr = self.get_position()
            if curr is None:
                raise ValueError("no current position")
            offset = position - curr
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

    def seek_absolute(
        self,
        identity_or_player_id: str,
        position: float,
    ) -> None:
        # May raise KeyError.
        with self.players_lock:
            self.players.lookup(identity_or_player_id).seek_absolute(position)


if __name__ == "__main__":
    o = {
        PROP_METADATA: GLib.Variant(
            "a{sv}", {"abc": GLib.Variant("i", 1), "def": GLib.Variant("i", 2)}
        )
    }
    print(unpack(o))
