import logging

import grpc

from typing import List

from cryptography.x509 import Certificate
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

import pskca
import cakes
from concurrent import futures
from cakes.proto import cakes_pb2_grpc


_LOGGER = logging.getLogger(__name__)


class CAKESServer(object):
    def __init__(
        self,
        ca_certificate: Certificate,
        ca_key: RSAPrivateKey,
        cakes_listen_address: str,
        verification_callback: cakes.ECDHVerificationCallback,
    ) -> None:
        cakes_server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        ca = pskca.CA(
            ca_certificate,
            ca_key,
            certificate_chain=[ca_certificate],
        )
        cakes_servicer = cakes.CAKESServicer(
            ca,
            verification_callback,
            self.certificate_issued_callback,
        )
        cakes_pb2_grpc.add_CAKESServicer_to_server(  # type: ignore
            cakes_servicer,
            cakes_server,
        )
        cakes_server.add_insecure_port(cakes_listen_address)
        self.cakes_server = cakes_server

    def start(self) -> None:
        self.cakes_server.start()

    def stop(self) -> None:
        self.cakes_server.stop(0)
        self.cakes_server.wait_for_termination(15)

    def certificate_issued_callback(
        self,
        peer: str,
        cert: Certificate,
        unused_chain: List[Certificate],
    ) -> bool:
        _LOGGER.debug("Certificate issued: %s" % cert)
        return True
