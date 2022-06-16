import sys

import grpc

from blindecdh import ECDHProtocol, CompletedECDH

from typing import Callable
from concurrent import futures

from hassmpris.security.util import TimedDict
from hassmpris.security.certs import PEM
from hassmpris.security.proto import ecdh_pb2_grpc, ecdh_pb2
from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePublicKey,
)


class ECDHClient(object):
    def __init__(self, channel: grpc.Channel) -> None:
        self.channel = channel

    def ECDH(self) -> CompletedECDH:
        s = ECDHProtocol()
        # send my public key and retrieve the remote end's public key
        stub = ecdh_pb2_grpc.ECDHServiceStub(self.channel)
        stub.ClientPubkey(
            ecdh_pb2.ECDHKey(
                pubkey=PEM.from_ecpubkey(s.public_key).as_bytes(),
            )
        )
        remote_pubkey_pem = PEM(
            stub.ServerPubkey(ecdh_pb2.Ack()).pubkey,
        ).to_ecpubkey()
        return s.run(remote_pubkey_pem)


# FIXME prevent abuse by forcing the client to do
# an expensive computation.


class ECDHServicer(ecdh_pb2_grpc.ECDHServiceServicer):
    def __init__(
        self,
        ecdh_complete_callback: Callable[[str, CompletedECDH], bool],
    ):
        """
        Initializes the ECDH servicer.

        Parameters:
            ecdh_complete_callback: a callback to call with the peer
            identification and the completed ECDH to further process
            down the line.  It must return True to consider the ECDH
            exchange successful.
        """
        self.peers: TimedDict[str, EllipticCurvePublicKey] = TimedDict(60, 16)
        self.callback = ecdh_complete_callback

    def ClientPubkey(
        self, request: ecdh_pb2.ECDHKey, context: grpc.ServicerContext
    ) -> ecdh_pb2.Ack:
        peer: str = context.peer()
        self.peers[peer] = PEM(request.pubkey).to_ecpubkey()
        return ecdh_pb2.Ack()

    def ServerPubkey(
        self, request: ecdh_pb2.Ack, context: grpc.ServicerContext
    ) -> ecdh_pb2.ECDHKey:
        peer: str = context.peer()
        EPERM = grpc.StatusCode.PERMISSION_DENIED
        with self.peers:
            try:
                peer_pubkey = self.peers[peer]
                del self.peers[peer]
            except KeyError:
                context.abort(EPERM, "invalid ECDH")
        s = ECDHProtocol()
        complete = s.run(peer_pubkey)
        result = self.callback(peer, complete)
        if not result:
            context.abort("rejected successful ECDH")
        return ecdh_pb2.ECDHKey(
            pubkey=PEM.from_ecpubkey(s.public_key).as_bytes(),
        )


# from here on now, the client must verify and the server must verify, both interactively, their keys.
# if the client says key matches, then the client connects to a new RPC call to send its certificate,
# encrypted of course using a lockbox.
# if the server says key matches, then the server must store (associated to the ECDHProtocolServer)
# that acceptance.  until such time that the acceptance has been forthcoming, the server should hang in
# the exchange of keys on that RPC call mentioned in the prior paragraph.  if the hang lasts over sixty
# seconds (meaning the client did not verify on time), then it should simply be aborted and the
# data about the exchange should be deleted.
# fixme timeouts
# fixme harden size of payloads here and in auth service
# fixme check that payloads are the right types
# fixme add logging to the networking parts


def server() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    def completed_callback(peer: str, c: CompletedECDH) -> bool:
        print("ECDH complete:", c.derived_key)
        return True

    ecdh_pb2_grpc.add_ECDHServiceServicer_to_server(
        ECDHServicer(completed_callback), server
    )

    server.add_insecure_port("0.0.0.0:50052")
    print("starting server")
    server.start()
    server.wait_for_termination()
    print("server ended")


def client() -> None:
    with grpc.insecure_channel("localhost:50052") as channel:
        client = ECDHClient(channel)
        print("connecting client")
        completed = client.ECDH()
        print("Client ECDH complete:", completed.derived_key)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        server()

    else:
        client()
