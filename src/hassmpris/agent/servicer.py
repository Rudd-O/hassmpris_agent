from typing import Deque, Any
import collections
import threading
from queue import Queue

import json
import grpc

from gi.repository import GObject  # noqa

from ..proto import mpris_pb2_grpc, mpris_pb2
from .mpris import MPRIS


def playback_status_to_PlayerStatus(playback_status: str) -> int:
    map = {
        "playing": mpris_pb2.PlayerStatus.PLAYING,
        "paused": mpris_pb2.PlayerStatus.PAUSED,
        "stopped": mpris_pb2.PlayerStatus.STOPPED,
    }
    s = map[playback_status.lower()]
    return s


def metadata_to_json_metadata(metadata: Any) -> str:
    return json.dumps(metadata)


def with_mpris(f):
    def inner(self, request, context):
        if not hasattr(self, "mpris"):
            context.abort(
                grpc.StatusCode.UNAVAILABLE, "The MPRIS interface is now gone."
            )
            return
        return f(self, request, context)

    return inner


def player_id_validated(f):
    def inner(self, request, context):
        try:
            return f(self, request, context)
        except KeyError:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "No such player %s" % request.player_id,
            )

    return inner


class MPRISServiceServicer(mpris_pb2_grpc.MPRISServiceServicer):
    def __init__(self, mpris: MPRIS):
        mpris_pb2_grpc.MPRISServiceServicer.__init__(self)
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
        self.queues: Deque[Queue] = collections.deque()
        self.queues_lock = threading.RLock()

    def __del__(self):
        if hasattr(self, "mpris"):
            for conn in self.conns:
                try:
                    self.mpris.disconnect(conn)
                except ImportError:
                    pass
        self._push_to_queues(None)
        if hasattr(self, "mpris"):
            delattr(self, "mpris")

    def _handle_mpris_shutdown(self, unused_mpris):
        self.__del__()

    def _handle_player_appeared(self, unused_mpris, player_id: str):
        print("player %s appeared" % player_id)
        m = mpris_pb2.MPRISUpdateReply(
            player_id=player_id,
            status=mpris_pb2.PlayerStatus.APPEARED,
        )
        self._push_to_queues(m)

    def _handle_player_gone(self, unused_mpris, player_id: str):
        print("player %s gone" % player_id)
        m = mpris_pb2.MPRISUpdateReply(
            player_id=player_id,
            status=mpris_pb2.PlayerStatus.GONE,
        )
        self._push_to_queues(m)

    def _handle_player_playback_status_changed(
        self, unused_mpris, player_id: str, playback_status: str
    ):
        s = playback_status_to_PlayerStatus(playback_status)
        print("player %s status changed: %s" % (player_id, s))
        m = mpris_pb2.MPRISUpdateReply(
            player_id=player_id,
            status=s,
        )
        self._push_to_queues(m)

    def _handle_player_metadata_changed(
        self, unused_mpris, player_id: str, metadata: Any
    ):
        s = metadata_to_json_metadata(metadata)
        print("player %s metadata changed: %s" % (player_id, s))
        m = mpris_pb2.MPRISUpdateReply(
            player_id=player_id,
            json_metadata=s,
        )
        self._push_to_queues(m)

    def _push_to_queues(self, m):
        with self.queues_lock:
            queues = list(self.queues)
        for q in queues:
            q.put(m)

    @with_mpris
    def Updates(self, request, context):
        q = Queue()
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

    def end(self):
        self.__del__()

    @player_id_validated
    @with_mpris
    def ChangePlayerStatus(
        self,
        request: mpris_pb2.ChangePlayerStatusRequest,
        context,
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
        self, request: mpris_pb2.PlayerNextRequest, context
    ) -> mpris_pb2.PlayerNextReply:
        self.mpris.next(request.player_id)
        return mpris_pb2.PlayerNextReply()

    @player_id_validated
    @with_mpris
    def PlayerPrevious(
        self, request: mpris_pb2.PlayerPreviousRequest, context
    ) -> mpris_pb2.PlayerPreviousReply:
        self.mpris.next(request.player_id)
        return mpris_pb2.PlayerPreviousReply()
