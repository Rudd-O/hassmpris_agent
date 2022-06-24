import logging
import os
import shutil
import signal
import sys

# FIXME: something is wrong with this, it hangs after several time
# of being connected, perhaps the executors are getting swamped.

# FIXME: the next line should be fixed when Fedora has
# protoc 3.19.0 or later, and the protobufs need to be recompiled
# when that happens.  Not just the hassmpris protos, also the
# cakes ones.
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import blindecdh  # noqa: E402

from hassmpris_agent.mpris.grpc import MPRISServer  # noqa: E402
from hassmpris_agent.auth import CAKESServer  # noqa: E402
from hassmpris_agent.control import (  # noqa: E402
    HASSMPRISControl,
    CMD_RESTART,
    CMD_RESET_PAIRINGS,
)
from hassmpris import config  # noqa: E402
from hassmpris import certs  # noqa: E402
from hassmpris_agent import verify  # noqa: E402


from queue import Queue  # noqa: E402
from typing import Any  # noqa: E402

from cryptography.x509 import Certificate  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.rsa import (  # noqa: E402,E501
    RSAPrivateKey,
)


_LOGGER = logging.getLogger(__name__)


class Server(object):
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
        self.verification_ui = verify.PeerVerificationUI()
        self.cakes_server = CAKESServer(
            ca_certificate,
            ca_key,
            cakes_listen_address,
            self.verify_peer,
        )
        self.dbus_control_iface = HASSMPRISControl()

    def verify_peer(
        self,
        peer: str,
        completed: blindecdh.CompletedECDH,
    ) -> bool:
        return self.verification_ui.verify(peer, completed.derived_key)

    def start(self) -> None:
        self.dbus_control_iface.start()
        self.mpris_server.start()
        self.verification_ui.start()
        self.cakes_server.start()

    def stop(self) -> None:
        self.cakes_server.stop()
        self.verification_ui.stop()
        self.mpris_server.stop()
        self.dbus_control_iface.stop()


def setup_signal_queue() -> Queue[int]:
    q: Queue[int] = Queue()

    def handler(signum: int, unused_frame: Any) -> None:
        q.put(signum)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    signal.signal(CMD_RESET_PAIRINGS, handler)
    signal.signal(CMD_RESTART, handler)

    return q


def main() -> None:
    logging.basicConfig(level=logging.DEBUG)
    fld = config.folder()
    _LOGGER.info("Loading / creating CA certificates.")
    ca_certificate, ca_key = certs.load_or_create_ca_certs(fld)
    _LOGGER.info("Loading / creating server certificates.")
    server_certificate, server_key = certs.load_or_create_server_certs(fld)
    _LOGGER.info("Creating server.")
    agent = Server(
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
    if signum == CMD_RESTART:
        _LOGGER.info("Restarting server.")
        os.execv(sys.executable, [sys.executable, sys.argv[0]])
    elif signum == CMD_RESET_PAIRINGS:
        _LOGGER.info("Resetting all pairings and restarting server.")
        shutil.rmtree(config.folder())
        os.execv(sys.executable, [sys.executable, sys.argv[0]])
    else:
        _LOGGER.info("Server shut down, exiting.")


if __name__ == "__main__":
    main()
