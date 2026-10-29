"""Interpretação das respostas JSON da API da NFS-e Nacional.

O contrato oficial (OpenAPI) diverge do serviço real: há variação de caixa nos nomes
(``Codigo``/``codigo``, ``erro``/``erros``) e já foram observadas 8 diferenças. Por isso todo
acesso a campo é tolerante (:func:`campo`) e a resposta bruta é preservada para auditoria.

HIPÓTESE: nomes ``chaveAcesso``, ``nfseXmlGZipB64``, ``alertas``, ``tipoAmbiente``,
``versaoAplicativo`` e ``dataHoraProcessamento`` (relatório técnico, sem chancela oficial).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from freedfe.excecoes import (
    ComunicacaoErro,
    EmissaoIncertaErro,
    ErroNfse,
    RejeicaoNfseErro,
    RespostaInvalidaErro,
)
from freedfe.nfse.envelope import descomprimir_b64
from freedfe.nfse.identificadores import chave_de_id_nfse
from freedfe.transporte import RespostaHttp

__all__ = [
    "ErroNfse",
    "RetornoEmissao",
    "RetornoEventoNfse",
    "campo",
    "extrair_erros",
    "interpretar_consulta_nfse",
    "interpretar_emissao",
    "interpretar_evento",
    "interpretar_lista_eventos",
    "ler_chave_do_xml",
    "ler_json",
    "levantar_rejeicao",
]

# Status em que a Sefin pode ou não ter processado a requisição: nunca reenviar sem consultar.
STATUS_INCERTOS = frozenset({408, 425, 429, 500, 502, 503, 504})


def campo(dados: Any, *nomes: str, padrao: Any = None) -> Any:
    """Valor do primeiro campo presente, sem diferenciar maiúsculas de minúsculas."""
    if not isinstance(dados, dict):
        return padrao
    minusculas = {str(chave).lower(): valor for chave, valor in dados.items()}
    for nome in nomes:
        if nome.lower() in minusculas:
            return minusculas[nome.lower()]
    return padrao


@dataclass(frozen=True)
class RetornoEmissao:
    """NFS-e autorizada (ou consultada) devolvida pela Sefin."""

    chave: str
    xml_nfse: bytes
    status_http: int = 201
    id_dps: str | None = None
    alertas: list[ErroNfse] = field(default_factory=list)
    tipo_ambiente: int | None = None
    versao_aplicativo: str | None = None
    data_processamento: str | None = None


@dataclass(frozen=True)
class RetornoEventoNfse:
    """Resultado do registro de um evento (ex.: cancelamento) na Sefin."""

    chave_nfse: str
    tipo_evento: str
    xml_evento: bytes | None
    status_http: int = 201
    alertas: list[ErroNfse] = field(default_factory=list)
    tipo_ambiente: int | None = None
    versao_aplicativo: str | None = None
    data_processamento: str | None = None


def ler_json(resposta: RespostaHttp) -> Any:
    """Corpo da resposta como JSON; ``None`` se vazio. Levanta ``RespostaInvalidaErro``."""
    if not resposta.corpo.strip():
        return None
    try:
        return json.loads(resposta.corpo.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise RespostaInvalidaErro(
            f"Resposta da Sefin não é JSON (HTTP {resposta.status}): "
            f"{resposta.corpo[:500]!r}") from erro


def extrair_erros(dados: Any, *chaves: str) -> list[ErroNfse]:
    """Lista de erros/alertas, aceitando ``erros``/``erro`` (lista ou objeto) e PascalCase."""
    nomes = chaves or ("erros", "erro")
    bruto = campo(dados, *nomes)
    if bruto is None:
        return []
    itens = bruto if isinstance(bruto, list) else [bruto]
    erros = []
    for item in itens:
        if isinstance(item, dict):
            erros.append(ErroNfse(
                codigo=str(campo(item, "codigo", "cod", padrao="") or ""),
                descricao=str(campo(item, "descricao", "mensagem", "message", padrao="") or ""),
                complemento=str(campo(item, "complemento", "caminho", "path", padrao="") or "")))
        elif item:
            erros.append(ErroNfse(codigo="", descricao=str(item)))
    return erros


def ler_chave_do_xml(xml_nfse: bytes) -> str | None:
    """Chave de acesso (50 posições) a partir de ``infNFSe/@Id`` do XML da NFS-e."""
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        raiz = etree.fromstring(xml_nfse, parser)
    except etree.XMLSyntaxError:
        return None
    for elemento in raiz.iter():
        if isinstance(elemento.tag, str) and etree.QName(elemento).localname == "infNFSe":
            identificador = elemento.get("Id")
            return chave_de_id_nfse(identificador) if identificador else None
    return None


def interpretar_emissao(resposta: RespostaHttp, id_dps: str | None = None) -> RetornoEmissao:
    """Resultado do ``POST /nfse``.

    Returns:
        ``RetornoEmissao`` quando a NFS-e foi gerada (HTTP 2xx).

    Raises:
        RejeicaoNfseErro: rejeição de negócio ou de validação (4xx). **Não repita** sem corrigir.
        EmissaoIncertaErro: 5xx, 408/425/429 ou resposta incompreensível; a Sefin pode ter
            processado a DPS. Chame ``reconciliar`` antes de qualquer novo envio.
        RespostaInvalidaErro: 2xx sem o XML da NFS-e.
    """
    if resposta.status in STATUS_INCERTOS:
        raise EmissaoIncertaErro(f"A Sefin respondeu HTTP {resposta.status}.", id_dps or "?")
    if 200 <= resposta.status < 300:
        return _retorno_nfse(resposta, id_dps)
    levantar_rejeicao(resposta, id_dps)
    raise AssertionError("inalcançável")  # pragma: no cover


def interpretar_consulta_nfse(resposta: RespostaHttp) -> RetornoEmissao:
    """Resultado do ``GET /nfse/{chave}`` (200 com ``nfseXmlGZipB64``)."""
    if 200 <= resposta.status < 300:
        return _retorno_nfse(resposta, None)
    levantar_rejeicao(resposta, None)
    raise AssertionError("inalcançável")  # pragma: no cover


def _retorno_nfse(resposta: RespostaHttp, id_dps: str | None) -> RetornoEmissao:
    dados = ler_json(resposta)
    texto_xml = campo(dados, "nfseXmlGZipB64", "nfseXml", "xmlNfse")
    if not texto_xml:
        raise RespostaInvalidaErro(
            f"Resposta HTTP {resposta.status} sem o XML da NFS-e: {resposta.corpo[:500]!r}")
    xml = descomprimir_b64(texto_xml)
    chave = campo(dados, "chaveAcesso", "chave") or ler_chave_do_xml(xml)
    if not chave:
        raise RespostaInvalidaErro("Resposta da Sefin sem a chave de acesso da NFS-e.")
    ambiente = campo(dados, "tipoAmbiente", "tpAmb")
    return RetornoEmissao(
        chave=str(chave), xml_nfse=xml, status_http=resposta.status, id_dps=id_dps,
        alertas=extrair_erros(dados, "alertas", "alerta"),
        tipo_ambiente=int(ambiente) if str(ambiente).isdigit() else None,
        versao_aplicativo=campo(dados, "versaoAplicativo"),
        data_processamento=campo(dados, "dataHoraProcessamento", "dataProcessamento"))


def levantar_rejeicao(resposta: RespostaHttp, id_dps: str | None) -> None:
    try:
        dados = ler_json(resposta)
    except RespostaInvalidaErro:
        dados = None
    erros = extrair_erros(dados)
    if not erros:
        erros = [ErroNfse(codigo=f"HTTP{resposta.status}",
                          descricao=resposta.corpo[:300].decode("utf-8", "replace").strip())]
    raise RejeicaoNfseErro(erros, status_http=resposta.status, id_dps=id_dps)


def interpretar_evento(resposta: RespostaHttp, chave_nfse: str,
                       tipo_evento: str) -> RetornoEventoNfse:
    """Resultado do ``POST /nfse/{chave}/eventos``.

    HIPÓTESE: o XML do evento registrado vem em ``eventoXmlGZipB64`` (ou campo equivalente);
    se a Sefin não o devolver, ``xml_evento`` fica ``None`` e o chamador consulta a lista.

    Raises:
        RejeicaoNfseErro: rejeição de negócio (ex.: E0824, E0840).
        ComunicacaoErro: 5xx/408/429; o evento pode ou não ter sido registrado.
    """
    if resposta.status in STATUS_INCERTOS:
        raise ComunicacaoErro(f"A Sefin respondeu HTTP {resposta.status} ao registrar o evento.")
    if not 200 <= resposta.status < 300:
        levantar_rejeicao(resposta, None)
    dados = ler_json(resposta)
    texto_xml = campo(dados, "eventoXmlGZipB64", "eventoXml", "xmlGZipB64", "arquivoXml")
    ambiente = campo(dados, "tipoAmbiente", "tpAmb")
    return RetornoEventoNfse(
        chave_nfse=chave_nfse, tipo_evento=tipo_evento,
        xml_evento=descomprimir_b64(texto_xml) if texto_xml else None,
        status_http=resposta.status, alertas=extrair_erros(dados, "alertas", "alerta"),
        tipo_ambiente=int(ambiente) if str(ambiente).isdigit() else None,
        versao_aplicativo=campo(dados, "versaoAplicativo"),
        data_processamento=campo(dados, "dataHoraProcessamento", "dataProcessamento"))


def interpretar_lista_eventos(resposta: RespostaHttp) -> list[bytes]:
    """XML dos eventos devolvidos por ``GET /nfse/{chave}/eventos[/...]``.

    O campo ``arquivoXml`` já foi observado com Base64 duplo; a decodificação é tolerante.
    404 significa "sem eventos". Aceita a lista em ``eventos`` ou um único evento no topo.
    """
    if resposta.status == 404:
        return []
    if not 200 <= resposta.status < 300:
        levantar_rejeicao(resposta, None)
    dados = ler_json(resposta)
    itens = campo(dados, "eventos", "evento")
    itens = itens if isinstance(itens, list) else ([itens] if itens else [dados])
    xmls = []
    for item in itens:
        texto_xml = campo(item, "arquivoXml", "eventoXmlGZipB64", "eventoXml", "xml")
        if texto_xml:
            xmls.append(descomprimir_b64(texto_xml))
    return xmls
