import datetime
import os
from socket import gethostname
from typing import Optional, Tuple, cast

import xdg.BaseDirectory

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from cryptography.x509 import (
    load_pem_x509_certificate,
    Certificate,
    CertificateSigningRequest,
    CertificateBuilder,
    random_serial_number,
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

NON_CA_USAGES = x509.KeyUsage(
    digital_signature=True,
    content_commitment=True,
    data_encipherment=True,
    key_encipherment=True,
    key_cert_sign=False,
    key_agreement=False,
    crl_sign=False,
    encipher_only=False,
    decipher_only=False,
)
CA_USAGES = x509.KeyUsage(
    digital_signature=True,
    content_commitment=True,
    data_encipherment=True,
    key_encipherment=True,
    key_cert_sign=True,
    key_agreement=True,
    crl_sign=True,
    encipher_only=False,
    decipher_only=False,
)
NON_CA_EXTENDED_KEY_USAGES = x509.ExtendedKeyUsage(
    [
        x509.OID_SERVER_AUTH,
        x509.OID_CLIENT_AUTH,
    ]
)
CA_EXTENDED_KEY_USAGES = x509.ExtendedKeyUsage([x509.OID_EXTENDED_KEY_USAGE])
CA_CONSTRAINTS = x509.BasicConstraints(ca=True, path_length=None)
NON_CA_CONSTRAINTS = x509.BasicConstraints(ca=False, path_length=None)


def _folder(folder: Optional[str] = None) -> str:
    return (
        folder
        if folder is not None
        else os.path.join(
            xdg.BaseDirectory.xdg_config_home,
            "hassmpris",
        )
    )


def keypair(
    type_: str,
    folder: Optional[str] = None,
    create: bool = True,
    ca: bool = True,
) -> Tuple[Certificate, RSAPrivateKey]:
    """
    Retrieve a preexisting self-signed keypair, or generate the pair
    if it does not exist yet (if the create parameter is True, else
    a FileNotFoundError exception is raised).

    By default the created certificate is a certificate authority
    that may sign other certificates.

    Returns a tuple of (RSA cert, RSA privkey).
    """
    ffolder = _folder(folder)

    pubkey = os.path.join(ffolder, "%s.crt" % type_)
    privkey = os.path.join(ffolder, "%s.key" % type_)

    try:
        with open(privkey, "rb") as f:
            privkey_data = f.read()
        with open(pubkey, "rb") as f:
            pubkey_data = f.read()
        return (
            PEM(pubkey_data).to_rsa_certificate(),
            PEM(privkey_data).to_rsa_privkey(),
        )
    except FileNotFoundError:
        if create:
            pass
        else:
            raise

    # Create a key pair.
    k = rsa.generate_private_key(65537, 4096)
    p = k.public_key()
    servername = gethostname()

    name = x509.Name(  # type: ignore
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "XX"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "No city"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HASS MPRIS bridge"),
            x509.NameAttribute(NameOID.COMMON_NAME, servername),
        ]
    )
    now = datetime.datetime.utcnow()
    expiry = datetime.datetime.utcnow() + datetime.timedelta(days=3650)
    cert = (
        CertificateBuilder()  # type: ignore
        .subject_name(name)
        .issuer_name(name)
        .serial_number(random_serial_number())
        .public_key(p)
        .not_valid_before(now)
        .not_valid_after(expiry)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(servername)]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(p),
            critical=False,
        )
    )

    if ca:
        cert = cert.add_extension(CA_CONSTRAINTS, critical=True)
        cert = cert.add_extension(CA_USAGES, critical=False)
    else:
        cert = cert.add_extension(NON_CA_CONSTRAINTS, critical=False)
        cert = cert.add_extension(NON_CA_USAGES, critical=False)

    cert = cert.sign(k, hashes.SHA256())

    privkey_data = PEM.from_rsa_privkey(k)
    pubkey_data = PEM.from_rsa_certificate(cert)

    old_umask = os.umask(0o077)
    os.makedirs(ffolder, exist_ok=True)
    os.umask(old_umask)

    with open(privkey, "wb") as g:
        g.write(privkey_data)
    with open(pubkey, "wb") as g:
        g.write(pubkey_data)

    return (
        PEM(pubkey_data).to_rsa_certificate(),
        PEM(privkey_data).to_rsa_privkey(),
    )


def certificate(type_: str, folder: Optional[str] = None) -> Certificate:
    """
    Retrieve a preexisting certificate.

    Returns a tuple of (RSA cert, RSA privkey).
    """
    ffolder = _folder(folder)

    pubkey = os.path.join(ffolder, "%s.crt" % type_)

    with open(pubkey, "rb") as f:
        pubkey_data = f.read()
    return PEM(pubkey_data).to_rsa_certificate()


def csrkey(
    type_: str, folder: Optional[str] = None
) -> Tuple[CertificateSigningRequest, RSAPrivateKey]:
    """
    Retrieve a preexisting private key and CSR, or generate a new private
    key and a CSR if it does not exist yet.

    Returns a tuple of (RSA CSR, RSA privkey).
    """
    ffolder = _folder(folder)

    csr = os.path.join(ffolder, "%s.csr" % type_)
    privkey = os.path.join(ffolder, "%s.key" % type_)

    try:
        with open(privkey, "rb") as f:
            privkey_data = f.read()
        with open(csr, "rb") as f:
            csr_data = f.read()
        return (
            PEM(csr_data).to_rsa_csr(),
            PEM(privkey_data).to_rsa_privkey(),
        )
    except FileNotFoundError:
        pass

    # Create a key pair.
    k = rsa.generate_private_key(65537, 4096)
    servername = gethostname()
    # create a CSR (unsigned)
    c = (
        x509.CertificateSigningRequestBuilder()  # type: ignore
        .subject_name(
            x509.Name(  # type: ignore
                [
                    # Provide various details about who we are.
                    x509.NameAttribute(NameOID.COMMON_NAME, servername),
                ]
            )
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    # Describe what sites we want this certificate for.
                    x509.DNSName(servername),
                ]
            ),
            critical=False,
            # Sign the CSR with our private key.
        )
        .sign(k, hashes.SHA256())
    )

    privkey_data = PEM.from_rsa_privkey(k)
    csr_data = PEM.from_rsa_csr(c)

    old_umask = os.umask(0o077)
    os.makedirs(ffolder, exist_ok=True)
    os.umask(old_umask)

    with open(privkey, "wb") as g:
        g.write(privkey_data)
    with open(csr, "wb") as g:
        g.write(csr_data)

    return (
        PEM(csr_data).to_rsa_csr(),
        PEM(privkey_data).to_rsa_privkey(),
    )


def issue_certificate_from_csr(
    csr: CertificateSigningRequest,
    root_cert: Certificate,
    root_privkey: RSAPrivateKey,
) -> Certificate:
    now = datetime.datetime.utcnow()
    expiry = datetime.datetime.utcnow() + datetime.timedelta(days=3650)
    SAN = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    sanvalue = SAN.value

    cert = (
        CertificateBuilder()  # type: ignore
        .subject_name(csr.subject)
        .add_extension(
            x509.SubjectAlternativeName(sanvalue),
            critical=False,
        )
        .issuer_name(root_cert.subject)
        .public_key(csr.public_key())
        .serial_number(random_serial_number())
        .not_valid_before(now)
        .not_valid_after(expiry)
    )
    cert = cert.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(
            root_cert.public_key(),
        ),
        critical=False,
    )
    cert = cert.add_extension(NON_CA_CONSTRAINTS, critical=False)
    cert = cert.add_extension(NON_CA_USAGES, critical=False)

    cert = cert.sign(root_privkey, hashes.SHA256())

    return cast(Certificate, cert)


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


def save_cert(
    type_: str,
    cert: Certificate,
    folder: Optional[str] = None,
) -> None:
    """
    Save a certificate under a named type.
    """
    ffolder = _folder(folder)

    cert_path = os.path.join(ffolder, "%s.crt" % type_)

    cert_data = PEM.from_rsa_certificate(cert)

    old_umask = os.umask(0o077)
    os.makedirs(ffolder, exist_ok=True)
    os.umask(old_umask)

    with open(cert_path, "wb") as g:
        g.write(cert_data)


def certificate_hostname(cert: Certificate) -> str:
    try:
        hostnamemaybe = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        hostnamev = cast(x509.SubjectAlternativeName, hostnamemaybe)
        hostnames = hostnamev.get_values_for_type(x509.DNSName)  # type: ignore
        if not isinstance(hostnames[0], str):
            raise ValueError("the provided certificate's SAN is not a string")
        return hostnames[0]
    except (x509.ExtensionNotFound, IndexError):
        try:
            cnmaybe = cert.subject
            cns = cnmaybe.get_attributes_for_oid(x509.OID_COMMON_NAME)
            firstcn = cns[0]
            if not isinstance(firstcn.value, str):
                raise ValueError(
                    "the provided certificate's common name is not a string"
                )
            return firstcn.value
        except IndexError:
            pass
    raise LookupError("the provided certificate has no SAN or common name")
