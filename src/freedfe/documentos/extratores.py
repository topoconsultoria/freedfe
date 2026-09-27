"""Extração dos dados principais de cada tipo de XML fiscal."""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation

from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento
from freedfe.tipos import ModeloDocumento, SituacaoDocumento
from freedfe.xml_utils import achar, nome_local, texto, todos

CSTAT_DENEGADO = frozenset({"110", "205", "301", "302", "303"})
SITUACAO_RESUMO_NFE = {"1": SituacaoDocumento.AUTORIZADO, "2": SituacaoDocumento.DENEGADO,
                       "3": SituacaoDocumento.CANCELADO}


# ---------------------------------------------------------------------------
# Conversões
# ---------------------------------------------------------------------------

def converter_data(valor: str | None) -> dt.datetime | None:
    """Converte ``dhEmi``/``dEmi``/``dhEvento`` (ISO 8601) em datetime."""
    if not valor:
        return None
    try:
        return dt.datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def converter_decimal(valor: str | None) -> Decimal | None:
    """Converte valores monetários do XML (ponto decimal) em Decimal."""
    if not valor:
        return None
    try:
        return Decimal(valor)
    except InvalidOperation:
        return None


def documento_de(no: ET.Element | None) -> str | None:
    """CNPJ ou CPF contido no elemento (``emit``, ``dest``, ``rem``, ``autXML``...)."""
    return texto(no, "CNPJ") or texto(no, "CPF")


def chave_do_id(no: ET.Element | None) -> str | None:
    """Chave de acesso a partir do atributo ``Id`` (``NFe``/``CTe``/``MDFe`` + 44 posições)."""
    if no is None:
        return None
    identificador = no.attrib.get("Id", "")
    return identificador[-44:] if len(identificador) >= 44 else None


def situacao_protocolo(cstat: str | None) -> SituacaoDocumento:
    """Situação a partir do cStat do protocolo de autorização."""
    return SituacaoDocumento.DENEGADO if cstat in CSTAT_DENEGADO else SituacaoDocumento.AUTORIZADO


# ---------------------------------------------------------------------------
# Extratores
# ---------------------------------------------------------------------------

class ExtratorDados(ABC):
    """Contrato: recebe a raiz do XML e devolve o resumo ou o evento."""

    @abstractmethod
    def extrair(self, raiz: ET.Element,
                interessado: str | None = None) -> ResumoDocumento | EventoDocumento:
        """Extrai os dados. ``interessado`` completa informações ausentes no XML."""


class ExtratorDocumentoCompleto(ExtratorDados):
    """Base para NF-e, CT-e e MDF-e completos (com ou sem protocolo).

    As subclasses informam os nomes de tags específicos do modelo.
    """

    modelo: ModeloDocumento
    tag_inf: str
    tag_numero: str
    tag_prot: str
    tag_chave: str
    caminho_valor: tuple[str, ...]
    tags_participantes: tuple[str, ...]

    def extrair(self, raiz: ET.Element, interessado: str | None = None) -> ResumoDocumento:
        inf = achar(raiz, self.tag_inf)
        protocolo = achar(raiz, self.tag_prot)
        destinatario = achar(inf, "dest")
        return ResumoDocumento(
            chave=chave_do_id(inf) or texto(protocolo, self.tag_chave) or "",
            modelo=self.modelo,
            tipo_xml=nome_local(raiz.tag),
            xml_completo=True,
            numero=texto(inf, "ide", self.tag_numero),
            serie=texto(inf, "ide", "serie"),
            data_emissao=converter_data(texto(inf, "ide", "dhEmi") or texto(inf, "ide", "dEmi")),
            emitente_documento=documento_de(achar(inf, "emit")),
            emitente_nome=texto(inf, "emit", "xNome"),
            destinatario_documento=documento_de(destinatario),
            destinatario_nome=texto(destinatario, "xNome"),
            valor_total=converter_decimal(texto(inf, *self.caminho_valor)),
            protocolo=texto(protocolo, "nProt"),
            situacao=situacao_protocolo(texto(protocolo, "cStat")),
            demais_participantes=self._participantes(inf),
        )

    def _participantes(self, inf: ET.Element | None) -> list[str]:
        encontrados = []
        for tag in self.tags_participantes:
            for elemento in todos(inf, tag):
                documento = documento_de(elemento)
                if documento and documento not in encontrados:
                    encontrados.append(documento)
        return encontrados


class ExtratorNFe(ExtratorDocumentoCompleto):
    """NF-e modelo 55 (``nfeProc`` ou ``NFe``)."""

    modelo = ModeloDocumento.NFE
    tag_inf = "infNFe"
    tag_numero = "nNF"
    tag_prot = "protNFe"
    tag_chave = "chNFe"
    caminho_valor = ("total", "ICMSTot", "vNF")
    tags_participantes = ("transporta", "autXML")


class ExtratorCTe(ExtratorDocumentoCompleto):
    """CT-e modelo 57 (``cteProc`` ou ``CTe``)."""

    modelo = ModeloDocumento.CTE
    tag_inf = "infCte"
    tag_numero = "nCT"
    tag_prot = "protCTe"
    tag_chave = "chCTe"
    caminho_valor = ("vPrest", "vTPrest")
    tags_participantes = ("rem", "exped", "receb", "toma4", "autXML")


class ExtratorMDFe(ExtratorDocumentoCompleto):
    """MDF-e modelo 58 (``mdfeProc`` ou ``MDFe``). Não possui destinatário."""

    modelo = ModeloDocumento.MDFE
    tag_inf = "infMDFe"
    tag_numero = "nMDF"
    tag_prot = "protMDFe"
    tag_chave = "chMDFe"
    caminho_valor = ("tot", "vCarga")
    tags_participantes = ("infContratante", "prop", "autXML")


class ExtratorResumoNFe(ExtratorDados):
    """Resumo de NF-e (``resNFe``), entregue ao destinatário antes da manifestação."""

    def extrair(self, raiz: ET.Element, interessado: str | None = None) -> ResumoDocumento:
        chave = texto(raiz, "chNFe") or ""
        return ResumoDocumento(
            chave=chave,
            modelo=ModeloDocumento.NFE,
            tipo_xml="resNFe",
            xml_completo=False,
            numero=chave[25:34].lstrip("0") or None,
            serie=chave[22:25].lstrip("0") or "0",
            data_emissao=converter_data(texto(raiz, "dhEmi")),
            emitente_documento=texto(raiz, "CNPJ") or texto(raiz, "CPF"),
            emitente_nome=texto(raiz, "xNome"),
            destinatario_documento=interessado,
            valor_total=converter_decimal(texto(raiz, "vNF")),
            protocolo=texto(raiz, "nProt"),
            situacao=SITUACAO_RESUMO_NFE.get(texto(raiz, "cSitNFe"), SituacaoDocumento.AUTORIZADO),
        )


class ExtratorEvento(ExtratorDados):
    """Eventos (``procEventoNFe``, ``procEventoCTe``, ``procEventoMDFe``, ``resEvento``)."""

    def extrair(self, raiz: ET.Element, interessado: str | None = None) -> EventoDocumento:
        chave = texto(raiz, "chNFe") or texto(raiz, "chCTe") or texto(raiz, "chMDFe") or ""
        return EventoDocumento(
            chave=chave,
            modelo=ModeloDocumento.de_chave(chave),
            tipo_evento=texto(raiz, "tpEvento") or "",
            descricao=texto(raiz, "descEvento") or texto(raiz, "xEvento"),
            data_evento=converter_data(texto(raiz, "dhEvento")),
            protocolo=texto(raiz, "nProt"),
        )
