"""Certificado digital ICP-Brasil do tipo A1 (.pfx/.p12)."""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)

from freedfe.excecoes import CertificadoErro

OID_CNPJ = "2.16.76.1.3.3"  # CNPJ do titular PJ
OID_CPF = "2.16.76.1.3.1"  # PF: nascimento(8) + CPF(11) + ...


@dataclass(frozen=True)
class InfoCertificado:
    """Dados de identificação do certificado."""

    titular: str
    valido_de: dt.datetime
    valido_ate: dt.datetime
    cnpj: str | None
    cpf: str | None

    @property
    def vencido(self) -> bool:
        """True se a validade já expirou."""
        return self.valido_ate < dt.datetime.now(dt.timezone.utc)

    @property
    def dias_para_vencer(self) -> int:
        """Dias restantes até o vencimento (negativo se vencido)."""
        return (self.valido_ate - dt.datetime.now(dt.timezone.utc)).days


class CertificadoA1:
    """Certificado A1 carregado em memória.

    A senha nunca deve ser recebida por argumento de linha de comando:
    prefira variável de ambiente ou cofre de segredos.
    """

    def __init__(self, conteudo_pfx: bytes, senha: str):
        try:
            chave, certificado, cadeia = pkcs12.load_key_and_certificates(
                conteudo_pfx, senha.encode("utf-8"))
        except ValueError as erro:
            raise CertificadoErro("Não foi possível abrir o PFX (senha incorreta?).") from erro
        if chave is None or certificado is None:
            raise CertificadoErro("PFX sem chave privada ou certificado.")
        self._chave = chave
        self._certificado = certificado
        self._cadeia = list(cadeia or [])

    @classmethod
    def de_arquivo(cls, caminho: str | Path, senha: str) -> CertificadoA1:
        """Carrega o certificado de um arquivo .pfx/.p12."""
        try:
            return cls(Path(caminho).read_bytes(), senha)
        except OSError as erro:
            raise CertificadoErro(f"Não foi possível ler o certificado: {caminho}") from erro

    @property
    def chave_privada(self):
        """Chave privada (objeto ``cryptography``), usada na assinatura de eventos."""
        return self._chave

    @property
    def certificado_x509(self) -> x509.Certificate:
        """Certificado do titular (objeto ``cryptography``)."""
        return self._certificado

    def certificado_der(self) -> bytes:
        """Certificado do titular em DER (vai em ``X509Certificate`` da assinatura)."""
        return self._certificado.public_bytes(Encoding.DER)

    def info(self) -> InfoCertificado:
        """Titular, validade e CNPJ/CPF (OIDs ICP-Brasil) do certificado."""
        cnpj, cpf = self._documentos_icp_brasil()
        return InfoCertificado(
            titular=self._certificado.subject.rfc4514_string(),
            valido_de=_data_utc(self._certificado, "not_valid_before"),
            valido_ate=_data_utc(self._certificado, "not_valid_after"),
            cnpj=cnpj,
            cpf=cpf,
        )

    def verificar_validade(self) -> None:
        """Levanta ``CertificadoErro`` se o certificado estiver vencido."""
        info = self.info()
        if info.vencido:
            raise CertificadoErro(f"Certificado vencido em {info.valido_ate:%d/%m/%Y}.")

    @contextlib.contextmanager
    def arquivos_pem(self) -> Iterator[tuple[str, str]]:
        """Grava certificado e chave em PEM temporários (0600) e os remove ao final.

        Yields:
            ``(caminho_cert_pem, caminho_chave_pem)`` para ``ssl.SSLContext.load_cert_chain``.
        """
        pasta = tempfile.mkdtemp(prefix="freedfe_")
        caminho_cert = os.path.join(pasta, "cert.pem")
        caminho_chave = os.path.join(pasta, "key.pem")
        try:
            _gravar_privado(caminho_cert, self._cadeia_pem())
            _gravar_privado(caminho_chave, self._chave.private_bytes(
                Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
            yield caminho_cert, caminho_chave
        finally:
            for caminho in (caminho_cert, caminho_chave):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(caminho)
            with contextlib.suppress(OSError):
                os.rmdir(pasta)

    def _cadeia_pem(self) -> bytes:
        partes = [self._certificado.public_bytes(Encoding.PEM)]
        partes.extend(c.public_bytes(Encoding.PEM) for c in self._cadeia)
        return b"".join(partes)

    def _documentos_icp_brasil(self) -> tuple[str | None, str | None]:
        cnpj = cpf = None
        with contextlib.suppress(x509.ExtensionNotFound):
            san = self._certificado.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value
            for nome in san:
                if not isinstance(nome, x509.OtherName):
                    continue
                conteudo = _der_para_texto(nome.value)
                if nome.type_id.dotted_string == OID_CNPJ:
                    cnpj = conteudo[:14]
                elif nome.type_id.dotted_string == OID_CPF and len(conteudo) >= 19:
                    cpf = conteudo[8:19]
        return cnpj, cpf


def _gravar_privado(caminho: str, conteudo: bytes) -> None:
    """Grava o arquivo com permissão 0600 desde a criação."""
    with open(os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as arquivo:
        arquivo.write(conteudo)


def _data_utc(certificado: x509.Certificate, atributo: str) -> dt.datetime:
    """Lê datas de validade com fuso UTC em qualquer versão do ``cryptography``."""
    valor = getattr(certificado, f"{atributo}_utc", None)
    if valor is None:  # cryptography < 42
        valor = getattr(certificado, atributo).replace(tzinfo=dt.timezone.utc)
    return valor


def _der_para_texto(bruto: bytes) -> str:
    """Remove cabeçalhos DER (tag explícita [0] e tipos string) e devolve o conteúdo ASCII."""
    tags_string = {0x04, 0x0C, 0x13, 0x16, 0xA0}
    while len(bruto) >= 2 and bruto[0] in tags_string:
        tamanho = bruto[1]
        inicio = 2
        if tamanho & 0x80:  # forma longa
            quantidade = tamanho & 0x7F
            tamanho = int.from_bytes(bruto[2:2 + quantidade], "big")
            inicio = 2 + quantidade
        bruto = bruto[inicio:inicio + tamanho]
    return bruto.decode("latin-1", "ignore").strip()
