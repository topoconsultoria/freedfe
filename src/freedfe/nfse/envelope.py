"""Envelope GZip + Base64 dos XMLs trafegados na API da NFS-e Nacional."""

from __future__ import annotations

import base64
import binascii
import gzip
import re

from freedfe.excecoes import RespostaInvalidaErro, ValidacaoErro

_MAGICO_GZIP = b"\x1f\x8b"
_MAXIMO_RODADAS_BASE64 = 3
_BOM_UTF8 = b"\xef\xbb\xbf"
DECLARACAO_UTF8 = b'<?xml version="1.0" encoding="UTF-8"?>'
_DECLARACAO = re.compile(rb"<\?xml\s[^>]*?\?>")
_ENCODING = re.compile(rb"""encoding\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def com_declaracao_utf8(xml: bytes) -> bytes:
    """Garante a declaração ``<?xml version="1.0" encoding="UTF-8"?>`` no início do XML.

    A Sefin Nacional rejeita com **E1229** ("XML não está utilizando codificação UTF8") o XML
    sem declaração, mesmo quando os bytes são UTF-8 válidos: ela deduz a codificação pela
    declaração (observado na produção restrita em 29/10/2026). A declaração fica fora da
    canonicalização, então acrescentá-la **não** invalida uma assinatura já feita.

    - sem declaração: prefixa a declaração padrão (aspas duplas, sem quebra de linha);
    - declaração UTF-8, em qualquer caixa ou aspas: devolve sem alteração (idempotente);
    - declaração sem ``encoding``: troca pela declaração padrão;
    - BOM UTF-8 inicial é removido.

    Raises:
        ValidacaoErro: declaração com outra codificação (o XML teria de ser reconvertido).
    """
    dados = xml[len(_BOM_UTF8):] if xml.startswith(_BOM_UTF8) else xml
    dados = dados.lstrip()
    declaracao = _DECLARACAO.match(dados)
    if declaracao is None:
        return DECLARACAO_UTF8 + dados
    encoding = _ENCODING.search(declaracao.group(0))
    if encoding is None:
        return DECLARACAO_UTF8 + dados[declaracao.end():]
    if encoding.group(1).decode("ascii", "replace").lower().replace("_", "-") not in (
            "utf-8", "utf8"):
        raise ValidacaoErro(
            f"O XML declara a codificação {encoding.group(1).decode('ascii', 'replace')!r}; a "
            "NFS-e Nacional exige UTF-8 (E1229). Gere o XML em UTF-8 antes de assinar.")
    return dados


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
