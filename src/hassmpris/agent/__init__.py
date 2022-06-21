from hassmpris.agent.mpris.grpc import MPRISServer
from hassmpris.agent.auth import CAKESServer
from hassmpris import certs

import logging
import signal

from queue import Queue
from typing import List, Any

from cryptography.x509 import Certificate
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

import blindecdh


_LOGGER = logging.getLogger(__name__)


class Agent(object):
    def __init__(
        self,
        ca_certificate: Certificate,
        ca_key: RSAPrivateKey,
        server_certificate: Certificate,
        server_key: RSAPrivateKey,
        mpris_listen_address: str,
        cakes_listen_address: str,
    ) -> None:
        self.mpris_server = MPRISServer(
            server_certificate,
            server_key,
            [ca_certificate],
            mpris_listen_address,
        )
        self.cakes_server = CAKESServer(
            ca_certificate,
            ca_key,
            cakes_listen_address,
        )

    def start(self) -> None:
        self.mpris_server.start()
        self.cakes_server.start()

    def stop(self) -> None:
        self.cakes_server.stop()
        self.mpris_server.stop()

    def verification_callback(
        self,
        peer: str,
        ecdh: blindecdh.CompletedECDH,
    ) -> bool:
        raise NotImplementedError

    def certificate_issued_callback(
        self,
        peer: str,
        cert: Certificate,
        unused_chain: List[Certificate],
    ) -> bool:
        _LOGGER.debug("Certificate issued: %s" % cert)
        return True


def setup_signal_queue() -> Queue[int]:
    q: Queue[int] = Queue()

    def handler(signum: int, unused_frame: Any) -> None:
        q.put(signum)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    return q


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    _LOGGER.info("Loading / creating CA certificates.")
    ca_certificate, ca_key = certs.load_or_create_ca_certs()
    _LOGGER.info("Loading / creating server certificates.")
    server_certificate, server_key = certs.load_or_create_server_certs()
    _LOGGER.info("Creating agent.")
    agent = Agent(
        ca_certificate,
        ca_key,
        server_certificate,
        server_key,
        "0.0.0.0:40051",
        "0.0.0.0:40052",
    )
    _LOGGER.info("Starting server.")
    q = setup_signal_queue()
    agent.start()
    signum = q.get()
    _LOGGER.info(
        "Shutting down server after signal %s.",
        signal.Signals(signum).name,
    )
    agent.stop()
    _LOGGER.info("Server shut down, exiting.")


if __name__ == "__main__":
    main()
