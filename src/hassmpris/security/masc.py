"""
Mutual authentication with simple authenticated strings after ECDH.

The purpose of this protocol is to establish trust between server and client.
After successful untampered ECDH, the server and the client have a shared
secret they can use to encrypt and decrypt traffic.  This is useful, but the
key is not enough — the goal is to arrive at mutually authenticated TLS
between the peers, so that gRPC can then continue in a fully symmetrically
authenticated manner.

To that effect, after successful ECDH:

1. Both peers must verify that the shared key is the same (using a SAS derived
   from the key).  This step requires user intervention.
2. The client must receive the server's public key, and remember it for
   subsequent use with the authenticated service.  This public key must be
   transmitted to the client encrypted and signed, so the client knows the
   server issued it.
3. The client must send a certificate signing request for the server to issue
   a certificate to the client and then sign it with its own public key.
   The CSR will be encrypted and signed, so the server knows it's authentic
   and no MITM is possible.  Then the server must return the issued
   certificate, signed, to the client.  This certificate must also be
   encrypted and signed - the client knows it's authentic and no MITM happened.

Once this process is complete, the client has a client certificate properly-
signed by the server, so the client may connect to the server and the server
can identify it.  The client also has the server's public certificate, so the
client can identify the server.  This makes mutually-authenticated TLS work.

Detailed steps of the process:  (FIXME annotate the different function calls)

1. After successful ECDH, the program passes the ECDH state to the UI for
   authorization, both on the client side and on the server side.
   This ECDH carries along the peer name.
   In parallel:
   * Server upcalls to mutual authentication UI, which displays the SAS and
     asks for confirmation.
   * Client upcalls to mutual authentication UI, which displays the SAS and
     asks for confirmation.
3. User accepts the SAS on the server UI.
   UI of the server now moves the associated ECDH and peer name into the
   MASC RPC server's "pending" keyring, ready for the following RPC call.
5. User accepts the SAS on the client.
   UI of the client now moves the associated ECDH and peer name into the
   MASC RPC client's control.  With this the client can perform the
   following RPC call.
6. Client tries, for T seconds, to call server's IssueCertificate
   method, with a symmetrically encrypted form of the client's CSR, and a
   known nonce.
   * If the server says that the mutual verification is pending, it retries
     until T seconds have elapsed.
   * If the server replies with permission denied, then the client discards
     its ECDH and all related material, aborting the operation, and upcalling
     to the UI to note that the authentication failed.
7. Server receives call for IssueCertificate and decrypts it using the
   ECDH shared key associated to the client in the "accepted" keyring.
   a) If decryption is successful, it creates a certificate for the client,
      encrypts it with the ECDH shared key, and sends it back.  Along with
      the client certificate, the server's own public certificate is sent
      in encrypted form as well.
   b) If decryption is unsuccessful, it discards the ECDH shared key and
      replies to the client with a permission denied error.
   c) If the ECDH is in the "pending" keyring, it replies to the client that
      the mutual verification is pending and to please retry later.
8. Client receives reply, and successfully decrypts the certificate.  Client
   then stores the public certificate associated to the server (which also
   happens to be the signing authority for its own certificate), and can now
   use it for mutually-authenticated communication.
"""

import logging
import secrets
import sys
from typing import Callable, Tuple, Optional, cast
from concurrent import futures

import grpc
from cryptography.exceptions import InvalidKey, InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509 import Certificate, CertificateSigningRequest

from hassmpris.security.certs import (
    csrkey,
    keypair,
    issue_certificate_from_csr,
    save_cert,
    PEM,
)
from hassmpris.security.proto import masc_pb2_grpc, masc_pb2
from blindecdh import CompletedECDH
from hassmpris.security.util import TimedDict


_LOGGER = logging.getLogger(__name__)
EPERM = grpc.StatusCode.PERMISSION_DENIED
EWAIT = grpc.StatusCode.UNAUTHENTICATED
EINVAL = grpc.StatusCode.INVALID_ARGUMENT


temp_nonce = secrets.token_bytes(12)
temp_csr = PEM(
    b"""-----BEGIN CERTIFICATE-----
MIIEnTCCAoUCAgPoMA0GCSqGSIb3DQEBCwUAMBMxETAPBgNVBAMMCHByb2plY3Rz
MCAXDTIyMDYxNDE4MDcxN1oYDzIxMjIwNTIxMTgwNzE3WjATMREwDwYDVQQDDAhw
cm9qZWN0czCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAN76byhR+Ali
3aCVYFOxiCT+AdkL+lwM8d9aWk8JvTLq9XwAAqkCNMo1j+xpCwgRJc7jaBW1e4K6
GBw3QI3cq63Ao5aiVssNuEHVEjwDFFHXIu7B186pX0NVIawxdqd+86TplF/xIsHI
jSGm1Z2JvPKdoxLAVhO1rwgV/5XnzjjKwQSmZSBO6OLAXhLNzMCJ89ijM31Lvyqo
vEtQPYhH4b2x2QJm61vhl2RfmaZMoTQJO7kRfYdw8WzUicMGDTfyjpjpS8vD7Gba
12BglhgQcAMcHnLL3NbRD+mNh6U3GdgiNrVJ3UNsETuapj59VCKC+bKZ2li/YcDG
aYS12HqOukiRrGHFseDJYQEBoLVjwL3mNsCkTVfa1EkMuWa/nT4uB/4zB+Q7nN5E
6MKX3mtavydUBU+WIH3/zC+mEe0H3e3pL1rPxRjWfm4PXP+/BL/Ce3I218nkkOXW
UqhIZ5wYtTml59BXAHzS8FuxRRcnyuti45hBE6Uup7+UFtA10L2rv1cumfZg9yLD
0SOwjHI21ViZmHsoYsBfukQodHuENpM/vu75qtGwlkxP7YkJXdD6bm3WO6XbE7on
9Xbe+Dms6EtPMDZXHyKUvpcv374TdxR0A86vb+q6kZZBHE9uht+DhNgRyvvSQIu0
QAHsOKD5yEnISOOg45LTfIvZyHc8PDTXAgMBAAEwDQYJKoZIhvcNAQELBQADggIB
AH6hwN4WGhcapJsw/emrhnLO6k5gcFn5Uevwq4BK1ZQLtuCORDBCqMvX/74Pw4Dc
czT2BQD9ux/d7GvX4NXI8OF4w5Eo2SWb7WnxLOYNjIEDqo7B2G7yZOhjwEqgR8xU
o27UburHSewBtjiQ6sL1O71hJFI/phZpKdrKKeE1hXWZWi+ZqZCbc5jJIv8DkSsz
YmcB5YekPFy1kzBApjIL2TQ+DsQBMFn/H2QdzV/XIjC2k1uCaFept47ovC76jV3S
bH/FRZuXjZTv2TPSLDngh3AFVs56+k2CnZ/rsRlPCseoPELfZy8gWdO1R8WIBKuq
0RIfuY7QPOGfqFUu43vel73E70NDq9DjHZUulYwEIiCZGYzhNM+dunpUSqKIZ3SG
pNK3aSftAV6HiwG0fSjt6SXS0HitDVinf/BTe9aNLIFeEVz3Ic5K9rx14MDH726V
xYZZxL5jwTPbPAqhdZoyMLZahjF+AOEo9NZtiqr0xL1MtZHOvAYS//gMXQ+SknoD
6EH6l/BhFDn4zHWxTDrpXaSNZVoFkGy0BLLaoN9BhI7MTiHQ9cx88hOKSpTfw14X
5mfrLlgmPG4L1+kPmbLI/qm2Pkggr4lWY0CmkKJfa2LUsEznVftRAktnJireNx9k
dbGQqvwr6pPX3P1Uup5j4FRIEsQ4807Z7iJaoeEOLSMy
-----END CERTIFICATE-----
"""
)


class MASCServicer(masc_pb2_grpc.MASCServiceServicer):
    def __init__(
        self,
        server_certificate: Certificate,
        server_certificate_key: RSAPrivateKey,
        certificate_issued_callback: Callable[[str, Certificate], bool],
    ):
        """
        Initializes the MASC servicer.

        Parameters:
            server_certificate: a Certificate object that will be sent to
            successfully-authenticated clients as the certificate root of
            trust they must use to connect to authenticated services.
            server_certificate_key: an RSAPrivateKey object that will be
            used to sign certificates that authenticated clients present
            for signature.
            certificate_issued_callback: a callback function that will be
            called with the signed certificate after successful signing,
            so that users of this class may later use the certificate for
            their own purposes.  If the callback returns False, then the
            client is denied its signed certificate.
        """
        self.ecdhs: TimedDict[str, Optional[CompletedECDH]] = TimedDict(60, 4)
        self.certificate_issued_callback = certificate_issued_callback
        self.server_certificate = server_certificate
        self.server_certificate_key = server_certificate_key

    def add_ecdh(self, peer: str, ecdh: Optional[CompletedECDH]) -> None:
        """
        Instructs the servicer to accept the following ECDH
        for certificate issuance.  If the ECDH is None, then that means
        the server-side authentication is pending and the client must be
        told to back off until the authentication is complete.

        ECDHs added with this method are only valid for the next 60 seconds.

        Parameters:
            peer: the name of the TCP peer as context.peer() would give it
            ecdh: the successfully-completed ECDH exchange
        """
        self.ecdhs[peer] = ecdh

    def IssueCertificate(
        self,
        request: masc_pb2.IssueCertificateRequest,
        context: grpc.ServicerContext,
    ) -> masc_pb2.IssueCertificateReply:
        """
        Performs the server side of the MASC process, post-ECDH.  The user
        of this servicer must have passed the approved ECDH (along with the
        peer identity) to the add_ecdh() method of this class, prior to the
        call to this RPC service endpoint.

        If the authentication of the ECDH is still pending (because the user
        of this servicer called add_ecdh(client, None) and has not yet called
        add_ecdh(client, definitive_ecdh)) this condition is signaled by
        returning to the client an error grpc.StatusCode.UNAUTHENTICATED .

        If successful, it returns to the caller a newly-issued certificate,
        signed by the server, which the caller may use to do mutual TLS
        via gRPC to any services protected by the server_certificate and
        server_certificate_key passed to the constructor of this object.
        """
        peer: str = context.peer()

        with self.ecdhs:
            try:
                ecdh = self.ecdhs[peer]
                del self.ecdhs[peer]
            except KeyError:
                try:
                    # Useful for testing.  Cannot be abused via
                    # production gRPC calls, as no peer ever is
                    # "*" and this value is not under the control
                    # of the peer.
                    ecdh = self.ecdhs["*"]
                    del self.ecdhs["*"]
                except KeyError:
                    context.abort(EPERM, "no corresponding ECDH")

        if ecdh is None:
            context.abort(
                EWAIT,
                "the user has not yet approved the ECDH exchange",
            )
            return

        chacha = ChaCha20Poly1305(ecdh.derived_key)
        encrypted_csr = request.EncryptedCSR
        plaintext_nonce = request.EncryptedCSRNonce
        try:
            decrypted = chacha.decrypt(
                plaintext_nonce,
                encrypted_csr,
                plaintext_nonce,
            )
        except (InvalidKey, InvalidSignature, InvalidTag):
            context.abort(
                EPERM,
                "cannot decrypt certificate signing request",
            )

        # Now validate.
        try:
            csr = PEM(decrypted).to_rsa_csr()
        except Exception:
            context.abort(
                EINVAL,
                "invalid certificate signing request to be signed",
            )

        cert = issue_certificate_from_csr(
            csr,
            self.server_certificate,
            self.server_certificate_key,
        )
        certpem = PEM.from_rsa_certificate(cert)

        enc_cert_nonce = secrets.token_bytes(12)
        enc_cert = chacha.encrypt(
            enc_cert_nonce,
            certpem.as_bytes(),
            enc_cert_nonce,
        )

        enc_srv_nonce = secrets.token_bytes(12)
        enc_srv = chacha.encrypt(
            enc_srv_nonce,
            PEM.from_rsa_certificate(self.server_certificate),
            enc_srv_nonce,
        )

        response = masc_pb2.IssueCertificateReply(
            EncryptedClientCertificate=enc_cert,
            EncryptedClientCertificateNonce=enc_cert_nonce,
            EncryptedServerCertificate=enc_srv,
            EncryptedServerCertificateNonce=enc_srv_nonce,
        )

        result = self.certificate_issued_callback(peer, cert)
        if not result:
            context.abort(
                EPERM,
                "callback refused to sign the certificate",
            )
        return response


class TryAgain(Exception):
    pass


class Unauthorized(Exception):
    pass


class MASCClient(object):
    def __init__(
        self,
        channel: grpc.Channel,
        ecdh: CompletedECDH,
        csr: CertificateSigningRequest,
    ) -> None:
        """
        Initializes the MASC client.

        Parameters:
            channel: a grpc.Channel, that does not need to be encrypted, to
            the authentication endpoint.
            ecdh: an ECDH exchange successfully completed with the endpoint.
            csr: a certificate signing request in PEM format which will be
            used by the endpoint to create a certificate signed by it.
        """
        self.channel = channel
        self.ecdh = ecdh
        self.csr = csr

    def MASC(self) -> Tuple[Certificate, Certificate]:
        """
        Performs the client-side part of the MASC exchange.

        If successful, the caller will receive a tuple of (newly-issued
        certificate signed by the server, server certificate).

        If the remote side still has not approved the use of the ECDH
        exchange, this will raise an exception TryAgain.  The client
        using this code should retry in 3-5 seconds again.

        If the remote side determines that the exchange is not authorized
        and will not be authorized by the user, or for some reason cannot
        decrypt the information sent to the server by this call, then
        the exception Unauthorized is raised.  Clients should report this
        error as a permanent failure to the user.
        """
        stub = masc_pb2_grpc.MASCServiceStub(self.channel)
        chacha = ChaCha20Poly1305(self.ecdh.derived_key)
        plaintext_nonce = secrets.token_bytes(12)
        encrypted_csr = chacha.encrypt(
            plaintext_nonce,
            PEM.from_rsa_csr(self.csr).as_bytes(),
            plaintext_nonce,
        )
        proto = masc_pb2.IssueCertificateRequest(
            EncryptedCSR=encrypted_csr,
            EncryptedCSRNonce=plaintext_nonce,
        )
        try:
            response = cast(
                masc_pb2.IssueCertificateReply,
                stub.IssueCertificate(proto),
            )
        except grpc.RpcError as e:
            if e.code() == EWAIT:
                raise TryAgain(e.details())
            if e.code() == EPERM:
                raise Unauthorized(e.details())
            else:
                raise

        decrypted_cert_data = chacha.decrypt(
            response.EncryptedClientCertificateNonce,
            response.EncryptedClientCertificate,
            response.EncryptedClientCertificateNonce,
        )

        decrypted_server_cert_data = chacha.decrypt(
            response.EncryptedServerCertificateNonce,
            response.EncryptedServerCertificate,
            response.EncryptedServerCertificateNonce,
        )

        # Now validate.
        cert = PEM(decrypted_cert_data).to_rsa_certificate()
        server_cert = PEM(decrypted_server_cert_data).to_rsa_certificate()

        # Process complete.
        return cert, server_cert


temp_ecdh = CompletedECDH(bytes(), bytes(), secrets.token_bytes(32))
temp_ecdh.derived_key = b"\x88\xe0\xd3q~\xfe\x1f\x1a\xf1\xcc\xeb\xa8\x01\xca\xa3\xe42\xd8m\xcf\tgM\xbc\xe8\xac5\xac\x9e,Ea"  # noqa


def server() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    cert, privkey = keypair("agent")
    mascservicer = MASCServicer(cert, privkey, lambda _, __: True)
    mascservicer.add_ecdh("*", temp_ecdh)
    masc_pb2_grpc.add_MASCServiceServicer_to_server(mascservicer, server)
    server.add_insecure_port("0.0.0.0:50052")
    print("starting server")
    server.start()
    server.wait_for_termination()
    print("server ended")


def client() -> None:
    csr, _ = csrkey("client")
    with grpc.insecure_channel("localhost:50052") as channel:
        client = MASCClient(channel, temp_ecdh, csr)
        print("connecting client")
        mycert, servercert = client.MASC()
    save_cert("client", mycert)
    save_cert("server", servercert)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        server()

    else:
        client()
