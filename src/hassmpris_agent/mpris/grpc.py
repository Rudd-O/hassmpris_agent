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
from functools import wraps
import collections
import threading
from queue import Queue

from concurrent import futures

from hassmpris_agent.certs import PEM

import json
import grpc

from gi.repository import GObject  # noqa
from cryptography.x509 import Certificate
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from hassmpris.proto import mpris_pb2_grpc, mpris_pb2
from hassmpris_agent.mpris.dbus import DBusMPRISInterface


def playback_status_to_PlayerStatus(playback_status: str) -> int:
    map = {
        "playing": mpris_pb2.PlayerStatus.PLAYING,
        "paused": mpris_pb2.PlayerStatus.PAUSED,
        "stopped": mpris_pb2.PlayerStatus.STOPPED,
    }
    s: int = map[playback_status.lower()]
    return s


def metadata_to_json_metadata(metadata: Any) -> str:
    return json.dumps(metadata)


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
            mpris.connect("mpris-shutdown", self._handle_mpris_shutdown),
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
        player_id: str,
    ) -> None:
        print("player %s appeared" % player_id)
        m = mpris_pb2.MPRISUpdateReply(
            player_id=player_id,
            status=mpris_pb2.PlayerStatus.APPEARED,
        )
        self._push_to_queues(m)

    def _handle_player_gone(
        self,
        unused_mpris: Any,
        player_id: str,
    ) -> None:
        print("player %s gone" % player_id)
        m = mpris_pb2.MPRISUpdateReply(
            player_id=player_id,
            status=mpris_pb2.PlayerStatus.GONE,
        )
        self._push_to_queues(m)

    def _handle_player_playback_status_changed(
        self,
        unused_mpris: Any,
        player_id: str,
        playback_status: str,
    ) -> None:
        s = playback_status_to_PlayerStatus(playback_status)
        print("player %s status changed: %s" % (player_id, s))
        m = mpris_pb2.MPRISUpdateReply(
            player_id=player_id,
            status=s,
        )
        self._push_to_queues(m)

    def _handle_player_metadata_changed(
        self,
        unused_mpris: Any,
        player_id: str,
        metadata: Any,
    ) -> None:
        s = metadata_to_json_metadata(metadata)
        print("player %s metadata changed: %s" % (player_id, s))
        m = mpris_pb2.MPRISUpdateReply(
            player_id=player_id,
            json_metadata=s,
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
        for player in self.mpris.get_players().values():
            m = mpris_pb2.MPRISUpdateReply(
                player_id=player.player_id,
                status=mpris_pb2.PlayerStatus.APPEARED,
            )
            m = mpris_pb2.MPRISUpdateReply(
                player_id=player.player_id,
                status=playback_status_to_PlayerStatus(player.playback_status),
            )
            if player.metadata:
                m = mpris_pb2.MPRISUpdateReply(
                    player_id=player.player_id,
                    json_metadata=metadata_to_json_metadata(player.metadata),
                )
            q.put(m)
        with self.queues_lock:
            self.queues.append(q)
        while True:
            m = q.get()
            if m is None:
                break
            else:
                yield m
        with self.queues_lock:
            self.queues.remove(q)

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
    def PlayerNext(
        self,
        request: mpris_pb2.PlayerNextRequest,
        context: grpc.ServicerContext,
    ) -> mpris_pb2.PlayerNextReply:
        self.mpris.next(request.player_id)
        return mpris_pb2.PlayerNextReply()

    @player_id_validated
    @with_mpris
    def PlayerPrevious(
        self,
        request: mpris_pb2.PlayerPreviousRequest,
        context: grpc.ServicerContext,
    ) -> mpris_pb2.PlayerPreviousReply:
        self.mpris.next(request.player_id)
        return mpris_pb2.PlayerPreviousReply()


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
        mpris_pb2_grpc.add_MPRISServicer_to_server(
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