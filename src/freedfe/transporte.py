"""Transporte SOAP 1.2 sobre TLS 1.2+ com autenticação mútua (certificado cliente)."""

from __future__ import annotations

import http.client
import ssl
import urllib.parse
from abc import ABC, abstractmethod

from freedfe.certificado import CertificadoA1
from freedfe.excecoes import ComunicacaoErro

SOAP12_NS = "http://www.w3.org/2003/05/soap-envelope"


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
            self._certificado.verificar_validade()
            contexto = ssl.create_default_context(cafile=self._ca_bundle)
            contexto.minimum_version = ssl.TLSVersion.TLSv1_2
            with self._certificado.arquivos_pem() as (cert_pem, chave_pem):
                contexto.load_cert_chain(cert_pem, chave_pem)
            self._contexto = contexto
        return self._contexto
