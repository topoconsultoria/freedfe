"""Envelope GZip + Base64 dos XMLs trafegados na API da NFS-e Nacional."""

from __future__ import annotations

import base64
import binascii
import gzip

from freedfe.excecoes import RespostaInvalidaErro

_MAGICO_GZIP = b"\x1f\x8b"
_MAXIMO_RODADAS_BASE64 = 3


def comprimir_b64(xml: bytes) -> str:
    """``base64(gzip(xml))``, com ``mtime`` zerado para saída determinística."""
    return base64.b64encode(gzip.compress(xml, mtime=0)).decode("ascii")


def descomprimir_b64(conteudo: str | bytes) -> bytes:
    """Desfaz ``base64(gzip(xml))``, tolerando Base64 duplo e XML sem GZip.

    O campo ``arquivoXml`` da consulta de eventos já foi observado com Base64 duplo
    (divergência entre o OpenAPI oficial e o serviço). A cada rodada decodifica-se o Base64;
    se o resultado for GZip, descomprime-se; se for XML, devolve-se; senão tenta-se de novo.
    """
    dados = conteudo.encode("ascii", "ignore") if isinstance(conteudo, str) else conteudo
    dados = dados.strip()
    if not dados:
        raise RespostaInvalidaErro("Conteúdo GZip/Base64 vazio.")
    for _ in range(_MAXIMO_RODADAS_BASE64):
        try:
            dados = base64.b64decode(dados, validate=False)
        except (binascii.Error, ValueError) as erro:
            raise RespostaInvalidaErro("Base64 inválido na resposta da NFS-e.") from erro
        if dados.startswith(_MAGICO_GZIP):
            try:
                return gzip.decompress(dados)
            except (OSError, EOFError) as erro:
                raise RespostaInvalidaErro("GZip corrompido na resposta da NFS-e.") from erro
        if dados.lstrip().startswith(b"<"):
            return dados
    raise RespostaInvalidaErro("Conteúdo não é GZip nem XML após decodificar o Base64.")
