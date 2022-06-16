import grpc
from hassmpris.agent.servicer import MPRISServiceServicer
from hassmpris.agent.mpris import MPRIS
from hassmpris.security.certs import keypair, PEM

import signal  # noqa

from cryptography.x509 import Certificate
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

import threading
from concurrent import futures
from hassmpris.proto import mpris_pb2_grpc


class ControlServer(threading.Thread):
    def __init__(
        self,
        listen_address: str,
        busthread: MPRIS,
        privkey: RSAPrivateKey,
        pubkey: Certificate,
        root_cert: Certificate,
    ) -> None:
        threading.Thread.__init__(self)
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        servicer = MPRISServiceServicer(busthread)
        mpris_pb2_grpc.add_MPRISServiceServicer_to_server(servicer, server)
        credentials = grpc.ssl_server_credentials(
            (
                (
                    PEM.from_rsa_privkey(privkey).as_bytes(),
                    PEM.from_rsa_certificate(pubkey).as_bytes(),
                ),
            ),
            PEM.from_rsa_certificate(root_cert).as_bytes(),
            require_client_auth=True,
        )
        server.add_secure_port(listen_address, credentials)
        self.server = server
        self.servicer = servicer

    def run(self) -> None:
        self.server.start()
        self.server.wait_for_termination()
        self.servicer.end()

    def end(self) -> None:
        self.server.stop(grace=10)
        self.join()


def agent() -> None:
    pubkey, privkey = keypair("agent")
    root_cert = pubkey
    control_listen_address = "0.0.0.0:50051"

    busthread = MPRIS()

    control_server = ControlServer(
        control_listen_address, busthread, privkey, pubkey, root_cert
    )

    busthread.start()
    print("Started D-Bus thread")
    control_server.start()
    print("Started gRPC server")

    try:
        control_server.join()
        busthread.join()
    except KeyboardInterrupt:
        print("KeyboardInterrupt")

    print("Ending servicer")
    control_server.end()
    print("Ending bus thread")
    busthread.end()
    print("Ended")


def main() -> None:
    agent()


if __name__ == "__main__":
    main()
