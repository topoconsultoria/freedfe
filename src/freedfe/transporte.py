"""Transportes sobre TLS 1.2+ com autenticação mútua (certificado cliente).

``ClienteSoap`` atende a distribuição DF-e e a manifestação (SOAP 1.2); ``ClienteRest``
atende a NFS-e Nacional (REST/JSON). Ambos compartilham o contexto TLS de ``criar_contexto_tls``.
"""

from __future__ import annotations

import http.client
import ssl
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from freedfe._versao import __version__
from freedfe.certificado import CertificadoA1
from freedfe.excecoes import ComunicacaoErro, ValidacaoErro

SOAP12_NS = "http://www.w3.org/2003/05/soap-envelope"


def criar_contexto_tls(certificado: CertificadoA1, ca_bundle: str | None = None) -> ssl.SSLContext:
    """Contexto TLS 1.2+ com o certificado cliente carregado (mTLS).

    Os PEM temporários são apagados logo após o carregamento. A verificação do servidor
    nunca é desabilitada: use ``ca_bundle`` se a cadeia ICP-Brasil não estiver no sistema.
    """
    certificado.verificar_validade()
    contexto = ssl.create_default_context(cafile=ca_bundle)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    with certificado.arquivos_pem() as (cert_pem, chave_pem):
        contexto.load_cert_chain(cert_pem, chave_pem)
    return contexto


class Transporte(ABC):
    """Contrato de envio de mensagens SOAP. Permite substituir o transporte em testes."""

    @abstractmethod
    def enviar(self, url: str, corpo: bytes, acao_soap: str) -> bytes:
        """Envia o envelope e devolve o corpo da resposta."""


class ClienteSoap(Transporte):
    """POST SOAP 1.2 com certificado A1, usando apenas a biblioteca padrão.

    Args:
        certificado: certificado A1 do interessado.
        ca_bundle: bundle PEM com a cadeia ICP-Brasil, se o TLS do servidor não validar
            com as ACs do sistema. Nunca desabilite a verificação.
        timeout: tempo máximo, em segundos, de cada requisição.
    """

    def __init__(self, certificado: CertificadoA1, ca_bundle: str | None = None,
                 timeout: int = 60):
        self._certificado = certificado
        self._ca_bundle = ca_bundle
        self._timeout = timeout
        self._contexto: ssl.SSLContext | None = None

    def enviar(self, url: str, corpo: bytes, acao_soap: str) -> bytes:
        """Envia o envelope. Levanta ``ComunicacaoErro`` em falha de rede ou HTTP sem SOAP."""
        alvo = urllib.parse.urlsplit(url)
        conexao = http.client.HTTPSConnection(
            alvo.hostname, alvo.port or 443, context=self._obter_contexto(), timeout=self._timeout)
        try:
            conexao.request("POST", alvo.path, body=corpo,
                            headers=self._cabecalhos(corpo, acao_soap))
            resposta = conexao.getresponse()
            conteudo = resposta.read()
        except (OSError, http.client.HTTPException) as erro:
            raise ComunicacaoErro(f"Falha ao comunicar com {alvo.hostname}: {erro}") from erro
        finally:
            conexao.close()
        if resposta.status != 200 and b"Envelope" not in conteudo:
            raise ComunicacaoErro(f"HTTP {resposta.status} {resposta.reason}: {conteudo[:500]!r}")
        return conteudo

    @staticmethod
    def _cabecalhos(corpo: bytes, acao_soap: str) -> dict[str, str]:
        return {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{acao_soap}"',
            "Content-Length": str(len(corpo)),
        }

    def _obter_contexto(self) -> ssl.SSLContext:
        """Cria (uma vez) o contexto TLS. Os PEM temporários são apagados após o carregamento."""
        if self._contexto is None:
            self._contexto = criar_contexto_tls(self._certificado, self._ca_bundle)
        return self._contexto


# ---------------------------------------------------------------------------
# REST/JSON (NFS-e Nacional)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RespostaHttp:
    """Resposta HTTP já lida. Status 4xx/5xx não geram exceção: quem chama interpreta."""

    status: int
    corpo: bytes = b""
    cabecalhos: dict[str, str] = field(default_factory=dict)

    def cabecalho(self, nome: str, padrao: str | None = None) -> str | None:
        """Valor de um cabeçalho (sem diferenciar maiúsculas de minúsculas)."""
        alvo = nome.lower()
        for chave, valor in self.cabecalhos.items():
            if chave.lower() == alvo:
                return valor
        return padrao


class TransporteRest(ABC):
    """Contrato de requisições HTTP/REST. Permite substituir o transporte em testes."""

    @abstractmethod
    def requisitar(self, metodo: str, url: str, corpo: bytes | None = None,
                   cabecalhos: dict[str, str] | None = None,
                   timeout: int | None = None) -> RespostaHttp:
        """Executa a requisição e devolve a resposta, qualquer que seja o status HTTP.

        Levanta ``ComunicacaoErro`` apenas em falha de rede (TLS, DNS, timeout).
        """


class ClienteRest(TransporteRest):
    """Requisições HTTPS com certificado A1 (mTLS), usando apenas a biblioteca padrão.

    Args:
        certificado: certificado A1 do interessado.
        ca_bundle: bundle PEM com a cadeia do servidor, se não validar com as ACs do sistema.
            Nunca desabilite a verificação.
        timeout: tempo máximo padrão, em segundos, de cada requisição.
    """

    def __init__(self, certificado: CertificadoA1, ca_bundle: str | None = None,
                 timeout: int = 60):
        self._certificado = certificado
        self._ca_bundle = ca_bundle
        self._timeout = timeout
        self._contexto: ssl.SSLContext | None = None

    def requisitar(self, metodo: str, url: str, corpo: bytes | None = None,
                   cabecalhos: dict[str, str] | None = None,
                   timeout: int | None = None) -> RespostaHttp:
        """Executa a requisição. Levanta ``ComunicacaoErro`` em falha de rede ou timeout."""
        alvo = urllib.parse.urlsplit(url)
        if alvo.scheme != "https" or not alvo.hostname:
            raise ValidacaoErro(f"A API exige HTTPS: {url}")
        caminho = alvo.path or "/"
        if alvo.query:
            caminho = f"{caminho}?{alvo.query}"
        metodo = metodo.upper()
        conexao = http.client.HTTPSConnection(
            alvo.hostname, alvo.port or 443, context=self._obter_contexto(),
            timeout=timeout or self._timeout)
        try:
            conexao.request(metodo, caminho, body=corpo,
                            headers=self._cabecalhos(metodo, corpo, cabecalhos))
            resposta = conexao.getresponse()
            conteudo = resposta.read()  # HEAD devolve b"": lê e descarta
            recebidos = {nome.lower(): valor for nome, valor in resposta.getheaders()}
        except (OSError, http.client.HTTPException) as erro:
            raise ComunicacaoErro(
                f"Falha ao comunicar com {alvo.hostname} ({metodo}): {erro}") from erro
        finally:
            conexao.close()
        return RespostaHttp(status=resposta.status, corpo=conteudo, cabecalhos=recebidos)

    @staticmethod
    def _cabecalhos(metodo: str, corpo: bytes | None,
                    extras: dict[str, str] | None) -> dict[str, str]:
        cabecalhos = {"Accept": "application/json", "User-Agent": f"freedfe/{__version__}"}
        if corpo is not None:
            cabecalhos["Content-Type"] = "application/json"
            cabecalhos["Content-Length"] = str(len(corpo))
        cabecalhos.update(extras or {})
        return cabecalhos

    def _obter_contexto(self) -> ssl.SSLContext:
        if self._contexto is None:
            self._contexto = criar_contexto_tls(self._certificado, self._ca_bundle)
        return self._contexto
