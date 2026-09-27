"""Fixtures compartilhadas: certificado de teste, transporte falso e relógio controlado."""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID

from freedfe.certificado import OID_CNPJ, CertificadoA1
from freedfe.transporte import Transporte

from fabrica_xml import CNPJ_INTERESSADO

SENHA_TESTE = "senha-teste"


def gerar_pfx(cnpj: str = CNPJ_INTERESSADO, dias_validade: int = 365) -> bytes:
    """PFX autoassinado com o OID ICP-Brasil de CNPJ no SubjectAlternativeName."""
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"EMPRESA TESTE:{cnpj}")])
    agora = dt.datetime.now(dt.timezone.utc)
    valor_der = bytes([0x13, len(cnpj)]) + cnpj.encode()  # PrintableString
    san = x509.SubjectAlternativeName([x509.OtherName(x509.ObjectIdentifier(OID_CNPJ), valor_der)])
    certificado = (x509.CertificateBuilder()
                   .subject_name(nome).issuer_name(nome).public_key(chave.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(agora - dt.timedelta(days=1))
                   .not_valid_after(agora + dt.timedelta(days=dias_validade))
                   .add_extension(san, critical=False)
                   .sign(chave, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(
        b"teste", chave, certificado, None, BestAvailableEncryption(SENHA_TESTE.encode()))


@pytest.fixture(scope="session")
def pfx_teste() -> bytes:
    return gerar_pfx()


@pytest.fixture
def certificado(pfx_teste) -> CertificadoA1:
    return CertificadoA1(pfx_teste, SENHA_TESTE)


class TransporteFalso(Transporte):
    """Devolve respostas pré-programadas e registra as requisições enviadas."""

    def __init__(self, respostas: list[bytes] | None = None):
        self.respostas = list(respostas or [])
        self.enviados: list[tuple[str, bytes, str]] = []

    def enviar(self, url: str, corpo: bytes, acao_soap: str) -> bytes:
        self.enviados.append((url, corpo, acao_soap))
        if not self.respostas:
            raise AssertionError("Chamada inesperada ao Web Service.")
        return self.respostas.pop(0)


class RelogioFalso:
    """Relógio controlável para testar bloqueios de 1 hora."""

    def __init__(self, inicio: dt.datetime | None = None):
        self.agora = inicio or dt.datetime(2026, 9, 27, 13, 0, tzinfo=dt.timezone.utc)

    def __call__(self) -> dt.datetime:
        return self.agora

    def avancar(self, **delta) -> None:
        self.agora += dt.timedelta(**delta)


@pytest.fixture
def relogio() -> RelogioFalso:
    return RelogioFalso()
