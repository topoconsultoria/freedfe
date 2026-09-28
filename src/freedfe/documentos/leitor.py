"""Identifica o tipo de XML fiscal e delega ao extrator correspondente."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from freedfe.documentos.extratores import (
    ExtratorCTe,
    ExtratorDados,
    ExtratorEvento,
    ExtratorMDFe,
    ExtratorNFe,
    ExtratorResumoNFe,
)
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento
from freedfe.excecoes import ValidacaoErro
from freedfe.xml_utils import nome_local

logger = logging.getLogger(__name__)


class LeitorDocumentoFiscal:
    """Lê um XML fiscal e devolve ``ResumoDocumento``, ``EventoDocumento`` ou ``None``.

    ``None`` indica XML não reconhecido (ex.: schema novo); o chamador deve
    preservar o arquivo bruto mesmo assim.
    """

    def __init__(self, extratores: dict[str, ExtratorDados] | None = None):
        self._extratores = extratores or self.extratores_padrao()

    @staticmethod
    def extratores_padrao() -> dict[str, ExtratorDados]:
        """Mapeamento elemento raiz -> extrator."""
        nfe, cte, mdfe, evento = ExtratorNFe(), ExtratorCTe(), ExtratorMDFe(), ExtratorEvento()
        return {
            "nfeProc": nfe, "NFe": nfe,
            "resNFe": ExtratorResumoNFe(),
            "cteProc": cte, "CTe": cte,
            "mdfeProc": mdfe, "MDFe": mdfe,
            "procEventoNFe": evento, "procEventoCTe": evento, "procEventoMDFe": evento,
            "resEvento": evento,
        }

    def ler(self, xml: bytes,
            interessado: str | None = None) -> ResumoDocumento | EventoDocumento | None:
        """Interpreta o XML. Devolve ``None`` se o tipo for desconhecido ou estiver malformado."""
        try:
            raiz = ET.fromstring(xml)
        except ET.ParseError:
            logger.warning("XML malformado ignorado na leitura.")
            return None
        extrator = self._extratores.get(nome_local(raiz.tag))
        if extrator is None:
            return None
        try:
            item = extrator.extrair(raiz, interessado)
        except ValidacaoErro as erro:
            logger.warning("XML %s não interpretado: %s", nome_local(raiz.tag), erro)
            return None
        return item if item.chave else None
