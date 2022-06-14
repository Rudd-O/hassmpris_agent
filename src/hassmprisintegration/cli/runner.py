import sys
import threading
import grpc
from ..proto import mpris_pb2_grpc, mpris_pb2


def cli(stub):
    while True:
        s = sys.stdin.readline().strip()
        if not s:
            return
        cmd, player = s.split(" ", 1)
        pbstatus = mpris_pb2.ChangePlayerStatusRequest.PlaybackStatus
        if cmd == "pause":
            stub.ChangePlayerStatus(
                mpris_pb2.ChangePlayerStatusRequest(
                    player_id=player,
                    status=pbstatus.PAUSED,
                )
            )
        elif cmd == "play":
            stub.ChangePlayerStatus(
                mpris_pb2.ChangePlayerStatusRequest(
                    player_id=player,
                    status=pbstatus.PLAYING,
                )
            )


def async_(stub):
    for reply in stub.Updates(mpris_pb2.MPRISUpdateRequest()):
        print(reply)
    print("done")


def main():
    channel = grpc.insecure_channel(target="localhost:50051")
    stub = mpris_pb2_grpc.MPRISServiceStub(channel=channel)

    t = threading.Thread(target=lambda: async_(stub), daemon=True)

    t.start()
    cli(stub)

    channel.close()


if __name__ == "__main__":
    main()
