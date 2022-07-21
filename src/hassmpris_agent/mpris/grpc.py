import logging
from typing import (
    Deque,
    Any,
    List,
    Optional,
    TypeVar,
    Callable,
    cast,
    Generator,
)
from google.protobuf.empty_pb2 import Empty
from functools import wraps
import collections
import threading
from queue import Queue, Empty as QueueEmpty

from concurrent import futures

from hassmpris.certs import PEM

import json
import grpc

from gi.repository import GObject  # noqa
from cryptography.x509 import Certificate
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from hassmpris.proto import mpris_pb2_grpc, mpris_pb2
from hassmpris_agent.mpris.dbus import (
    DBusMPRISInterface,
    Player,
    ALL_CAN_PROPS,
    ALL_NUMERIC_PROPS,
    STATUS_STOPPED,
    STATUS_PAUSED,
    STATUS_PLAYING,
)


_LOGGER = logging.getLogger(__name__)


def playback_status_to_PlayerStatus(playback_status: str) -> int:
    map = {
        STATUS_PLAYING: mpris_pb2.PlayerStatus.PLAYING,
        STATUS_PAUSED: mpris_pb2.PlayerStatus.PAUSED,
        STATUS_STOPPED: mpris_pb2.PlayerStatus.STOPPED,
    }
    s: int = map[playback_status]
    return s


def metadata_to_json_metadata(metadata: Any) -> str:
    return json.dumps(metadata)


def playerappearedmessage(player: Player) -> mpris_pb2.MPRISUpdateReply:
    s = playback_status_to_PlayerStatus(player.playback_status)
    m = metadata_to_json_metadata(player.metadata)
    props = {}
    kwargs = {}
    for prop in ALL_CAN_PROPS + list(ALL_NUMERIC_PROPS):
        props[prop] = getattr(player, prop)
    position = player.get_position()
    if position is not None and position != 0.0:
        kwargs["seeked"] = mpris_pb2.MPRISPlayerSeeked(position=position)
    return mpris_pb2.MPRISUpdateReply(
        player=mpris_pb2.MPRISPlayerUpdate(
            player_id=player.identity,
            status=s,
            json_metadata=m,
            properties=mpris_pb2.MPRISPlayerProperties(**props),
            **kwargs,
        )
    )


def playergonemessage(player: Player) -> mpris_pb2.MPRISUpdateReply:
    return mpris_pb2.MPRISUpdateReply(
        player=mpris_pb2.MPRISPlayerUpdate(
            player_id=player.identity,
            status=mpris_pb2.PlayerStatus.GONE,
        )
    )


ServicerContextFunc = TypeVar("ServicerContextFunc", bound=Callable[..., Any])


def with_mpris(f: ServicerContextFunc) -> ServicerContextFunc:
    @wraps(f)
    def inner(self: Any, request: Any, context: Any) -> Any:
        if not hasattr(self, "mpris"):
            context.abort(
                grpc.StatusCode.UNAVAILABLE, "The MPRIS interface is now gone."
            )
            return
        return f(self, request, context)

    return cast(ServicerContextFunc, inner)


def player_id_validated(f: ServicerContextFunc) -> ServicerContextFunc:
    @wraps(f)
    def inner(self: Any, request: Any, context: Any) -> Any:
        try:
            return f(self, request, context)
        except KeyError:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "No such player %s" % request.player_id,
            )

    return cast(ServicerContextFunc, inner)


class MPRISServicer(mpris_pb2_grpc.MPRISServicer):
    def __init__(self, mpris: DBusMPRISInterface):
        mpris_pb2_grpc.MPRISServicer.__init__(self)
        self.mpris = mpris
        self.conns = []
        self.conns.append(
            mpris.connect("player-appeared", self._handle_player_appeared),
        )
        self.conns.append(
            mpris.connect("player-gone", self._handle_player_gone),
        )
        self.conns.append(
            mpris.connect(
                "player-playback-status-changed",
                self._handle_player_playback_status_changed,
            ),
        )
        self.conns.append(
            mpris.connect(
                "player-metadata-changed",
                self._handle_player_metadata_changed,
            ),
        )
        self.conns.append(
            mpris.connect(
                "player-property-changed",
                self._handle_player_property_changed,
            ),
        )
        self.conns.append(
            mpris.connect(
                "player-seeked",
                self._handle_player_seeked,
            ),
        )
        self.conns.append(
            mpris.connect(
                "mpris-shutdown",
                self._handle_mpris_shutdown,
            ),
        )
        self.queues: Deque[
            Queue[Optional[mpris_pb2.MPRISUpdateReply]]
        ] = collections.deque()
        self.queues_lock = threading.RLock()

    def __del__(self) -> None:
        if hasattr(self, "mpris"):
            for conn in self.conns:
                try:
                    self.mpris.disconnect(conn)
                except ImportError:
                    pass
        self._push_to_queues(None)
        if hasattr(self, "mpris"):
            delattr(self, "mpris")

    def _handle_mpris_shutdown(self, unused_mpris: Any) -> None:
        self.__del__()

    def _handle_player_appeared(
        self,
        unused_mpris: Any,
        player: Player,
    ) -> None:
        _LOGGER.debug("%s appeared", player)
        m = playerappearedmessage(player)
        self._push_to_queues(m)

    def _handle_player_gone(
        self,
        unused_mpris: Any,
        player: Player,
    ) -> None:
        _LOGGER.debug("%s gone", player)
        m = playergonemessage(player)
        self._push_to_queues(m)

    def _handle_player_playback_status_changed(
        self,
        unused_mpris: Any,
        player: Player,
        playback_status: str,
    ) -> None:
        s = playback_status_to_PlayerStatus(playback_status)
        _LOGGER.debug("%s status changed: %s (%s)", player, s, playback_status)
        m = mpris_pb2.MPRISUpdateReply(
            player=mpris_pb2.MPRISPlayerUpdate(
                player_id=player.identity,
                status=s,
            )
        )
        self._push_to_queues(m)

    def _handle_player_metadata_changed(
        self,
        unused_mpris: Any,
        player: Player,
        metadata: Any,
    ) -> None:
        s = metadata_to_json_metadata(metadata)
        _LOGGER.debug("%s metadata changed: %s", player.identity, s)
        m = mpris_pb2.MPRISUpdateReply(
            player=mpris_pb2.MPRISPlayerUpdate(
                player_id=player.identity,
                json_metadata=s,
            )
        )
        self._push_to_queues(m)

    def _handle_player_property_changed(
        self,
        unused_mpris: Any,
        player: Player,
        property_name: str,
        property_value: Any,
    ) -> None:
        _LOGGER.debug(
            "%s property changed: %s -> %s",
            player.identity,
            property_name,
            property_value,
        )
        kws = {
            property_name: property_value,
        }
        m = mpris_pb2.MPRISUpdateReply(
            player=mpris_pb2.MPRISPlayerUpdate(
                player_id=player.identity,
                properties=mpris_pb2.MPRISPlayerProperties(**kws),
            )
        )
        self._push_to_queues(m)

    def _handle_player_seeked(
        self,
        unused_mpris: Any,
        player: Player,
        position: float,
    ) -> None:
        _LOGGER.debug(
            "%s seeked to %.2f",
            player.identity,
            position,
        )
        m = mpris_pb2.MPRISUpdateReply(
            player=mpris_pb2.MPRISPlayerUpdate(
                player_id=player.identity,
                seeked=mpris_pb2.MPRISPlayerSeeked(position=position),
            )
        )
        self._push_to_queues(m)

    def _push_to_queues(self, m: Optional[mpris_pb2.MPRISUpdateReply]) -> None:
        with self.queues_lock:
            queues = list(self.queues)
        for q in queues:
            q.put(m)

    @with_mpris
    def Updates(
        self,
        request: mpris_pb2.MPRISUpdateRequest,
        context: grpc.ServicerContext,
    ) -> Generator[mpris_pb2.MPRISUpdateReply, None, None]:
        q: Queue[Optional[mpris_pb2.MPRISUpdateReply]] = Queue()
        for player in self.mpris.get_players():
            m = playerappearedmessage(player)
            q.put(m)
        q.put(mpris_pb2.MPRISUpdateReply())
        with self.queues_lock:
            self.queues.append(q)
            _LOGGER.info("Clients connected now: %d", len(self.queues))
        try:
            while True:
                try:
                    m = q.get(timeout=10)
                except QueueEmpty:
                    m = mpris_pb2.MPRISUpdateReply(
                        heartbeat=mpris_pb2.MPRISUpdateHeartbeat(),
                    )
                if m is None:
                    break
                else:
                    yield m
        except Exception:
            _LOGGER.exception("Problem relaying status update to client")
        finally:
            with self.queues_lock:
                self.queues.remove(q)
                _LOGGER.info("Clients connected now: %d", len(self.queues))

    def stop(self) -> None:
        self.__del__()

    @player_id_validated
    @with_mpris
    def ChangePlayerStatus(
        self,
        request: mpris_pb2.ChangePlayerStatusRequest,
        context: grpc.ServicerContext,
    ) -> mpris_pb2.ChangePlayerStatusReply:
        a = mpris_pb2.ChangePlayerStatusRequest.PlaybackStatus
        actions = {
            a.PLAYING: self.mpris.play,
            a.PAUSED: self.mpris.pause,
            a.STOPPED: self.mpris.stop,
        }
        f = actions[request.status]
        f(request.player_id)
        return mpris_pb2.ChangePlayerStatusReply()

    @player_id_validated
    @with_mpris
    def Next(
        self,
        request: mpris_pb2.NextRequest,
        context: grpc.ServicerContext,
    ) -> mpris_pb2.NextReply:
        self.mpris.next(request.player_id)
        return mpris_pb2.NextReply()

    @player_id_validated
    @with_mpris
    def Previous(
        self,
        request: mpris_pb2.PreviousRequest,
        context: grpc.ServicerContext,
    ) -> mpris_pb2.PreviousReply:
        _LOGGER.debug(
            "Requested %s previous",
            request.player_id,
        )
        self.mpris.previous(request.player_id)
        return mpris_pb2.PreviousReply()

    @player_id_validated
    @with_mpris
    def Seek(
        self,
        request: mpris_pb2.SeekRequest,
        context: grpc.ServicerContext,
    ) -> mpris_pb2.SeekReply:
        _LOGGER.debug(
            "Requested %s seek %s seconds",
            request.player_id,
            request.offset,
        )
        try:
            self.mpris.seek(request.player_id, request.offset)
        except OverflowError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

        return mpris_pb2.SeekReply()

    @player_id_validated
    @with_mpris
    def SetPosition(
        self,
        request: mpris_pb2.SetPositionRequest,
        context: grpc.ServicerContext,
    ) -> mpris_pb2.SetPositionReply:
        _LOGGER.debug(
            "Requested %s set position %s seconds",
            request.player_id,
            request.position,
        )
        try:
            self.mpris.set_position(
                request.player_id,
                request.track_id,
                request.position,
            )
        except OverflowError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

        return mpris_pb2.SetPositionReply()

    def Ping(
        self, unused_request: Empty, unused_context: grpc.ServicerContext
    ) -> Empty:
        return Empty()


class MPRISServer(object):
    def __init__(
        self,
        server_certificate: Certificate,
        server_key: RSAPrivateKey,
        cert_chain: List[Certificate],
        listen_address: str,
    ):
        mpris_iface = DBusMPRISInterface()
        mpris_server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        mpris_servicer = MPRISServicer(mpris_iface)
        self.mpris_servicer = mpris_servicer
        mpris_pb2_grpc.add_MPRISServicer_to_server(  # type: ignore
            mpris_servicer,
            mpris_server,
        )
        trust_chain = b"\n".join(
            PEM.from_rsa_certificate(s).as_bytes() for s in cert_chain
        )
        mpris_server_credentials = grpc.ssl_server_credentials(
            (
                (
                    PEM.from_rsa_privkey(server_key).as_bytes(),
                    PEM.from_rsa_certificate(server_certificate).as_bytes(),
                ),
            ),
            trust_chain,
            require_client_auth=True,
        )
        mpris_server.add_secure_port(
            listen_address,
            mpris_server_credentials,
        )
        self.mpris_server = mpris_server

        self.mpris_iface = mpris_iface

    def start(self) -> None:
        self.mpris_iface.start()
        self.mpris_server.start()

    def stop(self) -> None:
        self.mpris_server.stop(0)
        self.mpris_server.wait_for_termination(15)
        self.mpris_servicer.stop()
        self.mpris_iface.stop_()
