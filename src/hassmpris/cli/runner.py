import sys
import socket
import threading
import grpc
from hassmpris.proto import mpris_pb2_grpc, mpris_pb2
from hassmpris.security.certs import (
    keypair,
    PEM,
    certificate,
    certificate_hostname,
)


def repl(stub: mpris_pb2_grpc.MPRISServiceStub) -> None:
    while True:
        s = sys.stdin.readline().strip()
        if not s:
            return
        try:
            cmd, player = s.split(" ", 1)
        except IndexError:
            return

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


def async_(stub: mpris_pb2_grpc.MPRISServiceStub) -> None:
    for reply in stub.Updates(mpris_pb2.MPRISUpdateRequest()):
        print(reply)
    print("done")


def client() -> None:
    pubkey, privkey = keypair("client")
    server_cert = certificate("server")
    server_cert_hostname = certificate_hostname(server_cert)

    credentials = grpc.ssl_channel_credentials(
        PEM.from_rsa_certificate(server_cert).as_bytes(),
        PEM.from_rsa_privkey(privkey).as_bytes(),
        PEM.from_rsa_certificate(pubkey).as_bytes(),
    )
    # We must override the target name so the server chooses
    # the right (issuing) certificate to present to us, irrespective
    # of the actual DNS or IP address used.
    channel = grpc.secure_channel(
        "%s:50051" % socket.gethostname(),
        credentials,
        options=[("grpc.ssl_target_name_override", server_cert_hostname)],
    )
    stub = mpris_pb2_grpc.MPRISServiceStub(channel=channel)

    t = threading.Thread(target=lambda: async_(stub), daemon=True)

    t.start()
    repl(stub)

    channel.close()  # raises exception in async_ FIXME

    t.join()


def main() -> None:
    client()


if __name__ == "__main__":
    main()
