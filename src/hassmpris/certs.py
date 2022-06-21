import os
from typing import Optional, Tuple, List

import xdg.BaseDirectory

import pskca

from cryptography.hazmat.primitives import serialization

from cryptography.x509 import (
    load_pem_x509_certificate,
    Certificate,
    CertificateSigningRequest,
    load_pem_x509_csr,
)
from cryptography.hazmat.primitives.serialization import (
    load_pem_public_key,
    load_pem_private_key,
)
from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurvePublicKey,
)
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


def _folder(folder: Optional[str] = None) -> str:
    return (
        folder
        if folder is not None
        else os.path.join(
            xdg.BaseDirectory.xdg_config_home,
            "hassmpris",
        )
    )


def _pem(class_: str, type_: str, folder: Optional[str] = None) -> str:
    folder = _folder(folder)
    return os.path.join(folder, "%s.%s" % (class_, type_))


def cert_path(class_: str, folder: Optional[str] = None) -> str:
    return _pem(class_, "crt", folder)


def chain_path(class_: str, folder: Optional[str] = None) -> str:
    return _pem(class_, "trust.pem", folder)


def key_path(class_: str, folder: Optional[str] = None) -> str:
    return _pem(class_, "key", folder)


def load_certificate_from_file(path: str) -> Certificate:
    with open(path, "rb") as f:
        pubkey_data = f.read()
    return PEM(pubkey_data).to_rsa_certificate()


def load_key_from_file(path: str) -> RSAPrivateKey:
    with open(path, "rb") as f:
        pubkey_data = f.read()
    return PEM(pubkey_data).to_rsa_privkey()


def load_trust_chain_from_file(path: str) -> List[Certificate]:
    with open(path, "rb") as f:
        chain_data = f.read()

    start_line = b"-----BEGIN CERTIFICATE-----"
    cert_slots = chain_data.split(start_line)
    certificates: List[Certificate] = []
    for single_pem_cert in cert_slots[1:]:
        loaded = load_pem_x509_certificate(start_line + single_pem_cert)
        certificates.append(loaded)
    return certificates


def load_client_certs_and_trust_chain() -> Tuple[
    Certificate,
    RSAPrivateKey,
    List[Certificate],
]:
    client_certificate_path = cert_path("client")
    client_key_path = key_path("client")
    client_trust_chain_path = chain_path("client")

    return (
        load_certificate_from_file(client_certificate_path),
        load_key_from_file(client_key_path),
        load_trust_chain_from_file(client_trust_chain_path),
    )


def save_client_certs_and_trust_chain(
    cert: Certificate,
    key: RSAPrivateKey,
    trust_chain: List[Certificate],
) -> None:
    client_certificate_path = cert_path("client")
    client_key_path = key_path("client")
    client_trust_chain_path = chain_path("client")

    with open(client_certificate_path, "wb") as f:
        f.write(PEM.from_rsa_certificate(cert).as_bytes())
    with open(client_key_path, "wb") as f:
        f.write(PEM.from_rsa_privkey(key).as_bytes())
    with open(client_trust_chain_path, "wb") as f:
        for c in trust_chain:
            f.write(PEM.from_rsa_certificate(c).as_bytes())


def create_and_load_client_key_and_csr() -> Tuple[
    CertificateSigningRequest,
    RSAPrivateKey,
]:
    client_key_path = key_path("client")
    csr, key = pskca.create_certificate_signing_request()
    with open(client_key_path, "wb") as f:
        f.write(PEM.from_rsa_privkey(key).as_bytes())
    return csr, key


def create_ca_certs(certpath: str, keypath: str) -> None:
    cert, key = pskca.create_certificate_and_key(
        cn="HASS MPRIS",
        ca=True,
    )
    os.makedirs(os.path.dirname(certpath), exist_ok=True)
    os.makedirs(os.path.dirname(keypath), exist_ok=True)
    with open(certpath, "wb") as f:
        f.write(PEM.from_rsa_certificate(cert).as_bytes())
    with open(keypath, "wb") as f:
        f.write(PEM.from_rsa_privkey(key).as_bytes())


def load_or_create_ca_certs() -> Tuple[Certificate, RSAPrivateKey]:
    ca_certificate_path = cert_path("ca")
    ca_key_path = key_path("ca")

    try:
        return (
            load_certificate_from_file(ca_certificate_path),
            load_key_from_file(ca_key_path),
        )
    except FileNotFoundError:
        create_ca_certs(ca_certificate_path, ca_key_path)
        return (
            load_certificate_from_file(ca_certificate_path),
            load_key_from_file(ca_key_path),
        )


def create_server_certs(
    certpath: str,
    keypath: str,
    ca_cert: Certificate,
    ca_key: RSAPrivateKey,
) -> None:
    # By convention, the server is always named hassmpris.
    # The client will force this hostname irrespective of
    # what IP address or hostname it connects to.
    csr, key = pskca.create_certificate_signing_request(
        cn="hassmpris",
    )
    cert = pskca.issue_certificate(
        csr,
        ca_cert,
        ca_key,
        ca=False,
    )
    os.makedirs(os.path.dirname(certpath), exist_ok=True)
    os.makedirs(os.path.dirname(keypath), exist_ok=True)
    with open(certpath, "wb") as f:
        f.write(PEM.from_rsa_certificate(cert).as_bytes())
    with open(keypath, "wb") as f:
        f.write(PEM.from_rsa_privkey(key).as_bytes())


def load_or_create_server_certs() -> Tuple[Certificate, RSAPrivateKey]:
    server_certificate_path = cert_path("server")
    server_key_path = key_path("server")

    try:
        return (
            load_certificate_from_file(server_certificate_path),
            load_key_from_file(server_key_path),
        )
    except FileNotFoundError:
        ca_certificate, ca_key = load_or_create_ca_certs()
        create_server_certs(
            server_certificate_path,
            server_key_path,
            ca_certificate,
            ca_key,
        )
        return (
            load_certificate_from_file(server_certificate_path),
            load_key_from_file(server_key_path),
        )


class PEM(bytes):
    @classmethod
    def from_ecpubkey(klass, ecpubkey: EllipticCurvePublicKey) -> "PEM":
        if not isinstance(ecpubkey, EllipticCurvePublicKey):
            raise TypeError("ecpubkey must be an EllipticCurvePublicKey")
        return klass(
            ecpubkey.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )

    @classmethod
    def from_rsa_certificate(klass, cert: Certificate) -> "PEM":
        if not isinstance(cert, Certificate):
            ok = cert.__class__
            raise TypeError("cert must be a Certificate, was a %s" % ok)
        return klass(cert.public_bytes(serialization.Encoding.PEM))

    @classmethod
    def from_rsa_privkey(klass, key: RSAPrivateKey) -> "PEM":
        if not isinstance(key, RSAPrivateKey):
            ok = key.__class__
            raise TypeError("key must be an RSAPrivateKey, was a %s" % ok)
        return klass(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    @classmethod
    def from_rsa_csr(klass, csr: CertificateSigningRequest) -> "PEM":
        if not isinstance(csr, CertificateSigningRequest):
            ok = csr.__class__
            raise TypeError(
                "csr must be a CertificateSigningRequest, was a %s" % ok,
            )
        return klass(csr.public_bytes(serialization.Encoding.PEM))

    def as_bytes(self) -> bytes:
        return bytes(self)

    def to_ecpubkey(self) -> EllipticCurvePublicKey:
        k = load_pem_public_key(self.as_bytes())
        if not isinstance(k, EllipticCurvePublicKey):
            ok = k.__class__
            raise TypeError(
                "this PEM is not an elliptic curve public key, was a %s" % ok
            )
        return k

    def to_rsa_certificate(self) -> Certificate:
        k = load_pem_x509_certificate(self)
        return k

    def to_rsa_privkey(self) -> RSAPrivateKey:
        k = load_pem_private_key(self, None)
        if not isinstance(k, RSAPrivateKey):
            raise TypeError(
                "the PEM data does not contain an RSA private key, was a %s"
                % k.__class__
            )
        return k

    def to_rsa_csr(self) -> CertificateSigningRequest:
        k = load_pem_x509_csr(self)
        return k
