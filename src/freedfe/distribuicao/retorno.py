"""Interpretação do retorno ``retDistDFeInt`` e descompactação dos ``docZip``."""

from __future__ import annotations

import base64
import binascii
import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from freedfe.excecoes import RespostaInvalidaErro
from freedfe.xml_utils import achar, texto, todos

CSTAT_DOCUMENTOS_LOCALIZADOS = "138"
CSTAT_NENHUM_DOCUMENTO = "137"


@dataclass(frozen=True)
class DocumentoRecebido:
    """XML bruto de um ``docZip`` (ou de um arquivo importado), ainda não interpretado."""

    xml: bytes
    nsu: str | None = None
    schema: str = ""


@dataclass
class RetornoDistribuicao:
    """Resultado normalizado de ``retDistDFeInt``."""

    cstat: str
    xmotivo: str
    dh_resp: str | None
    ult_nsu: str | None
    max_nsu: str | None
    documentos: list[DocumentoRecebido] = field(default_factory=list)

    @property
    def documentos_localizados(self) -> bool:
        """True quando o cStat é 138 (há documentos no lote)."""
        return self.cstat == CSTAT_DOCUMENTOS_LOCALIZADOS

    @property
    def sem_novos(self) -> bool:
        """True quando a NT manda aguardar 1 h (137 ou ``ultNSU == maxNSU``)."""
        return self.cstat == CSTAT_NENHUM_DOCUMENTO or (
            self.ult_nsu is not None and self.ult_nsu == self.max_nsu)


class InterpretadorRetorno:
    """Extrai ``retDistDFeInt`` de qualquer envelope (NF-e, CT-e, MDF-e)."""

    def interpretar(self, resposta: bytes) -> RetornoDistribuicao:
        """Converte a resposta SOAP em ``RetornoDistribuicao``."""
        try:
            raiz = ET.fromstring(resposta)
        except ET.ParseError as erro:
            raise RespostaInvalidaErro(f"Resposta não é XML válido: {resposta[:300]!r}") from erro
        ret = achar(raiz, "retDistDFeInt")
        if ret is None:
            raise RespostaInvalidaErro(f"Resposta sem retDistDFeInt: {self._descrever_falha(raiz)}")
        return RetornoDistribuicao(
            cstat=texto(ret, "cStat") or "",
            xmotivo=texto(ret, "xMotivo") or "",
            dh_resp=texto(ret, "dhResp"),
            ult_nsu=texto(ret, "ultNSU"),
            max_nsu=texto(ret, "maxNSU"),
            documentos=[self._documento(el) for el in todos(ret, "docZip")],
        )

    @staticmethod
    def descompactar(conteudo_b64: str) -> bytes:
        """``docZip`` = base64(gzip(xml))."""
        try:
            return gzip.decompress(base64.b64decode(conteudo_b64))
        except (binascii.Error, OSError) as erro:
            raise RespostaInvalidaErro("docZip corrompido.") from erro

    def _documento(self, elemento: ET.Element) -> DocumentoRecebido:
        return DocumentoRecebido(
            xml=self.descompactar((elemento.text or "").strip()),
            nsu=elemento.attrib.get("NSU"),
            schema=elemento.attrib.get("schema", ""),
        )

    @staticmethod
    def _descrever_falha(raiz: ET.Element) -> str:
        """Texto do SOAP Fault (SOAP 1.2 ``Text`` ou 1.1 ``faultstring``)."""
        return texto(raiz, "Text") or texto(raiz, "faultstring") or "sem detalhe"
