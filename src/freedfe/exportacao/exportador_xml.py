"""Exportação em XML (``<documentos><documento>...</documento></documentos>``)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from decimal import Decimal

from freedfe.documentos.resumo import ResumoDocumento
from freedfe.exportacao.base import COLUNAS, Exportador, linha


class ExportadorXml(Exportador):
    """XML simples, um elemento por coluna. Campos vazios são omitidos."""

    extensao = "xml"

    def exportar(self, resumos: Sequence[ResumoDocumento]) -> str:
        raiz = ET.Element("documentos", quantidade=str(len(resumos)))
        for resumo in resumos:
            self._documento(raiz, linha(resumo))
        ET.indent(raiz)
        return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(raiz, encoding="unicode")

    @staticmethod
    def _documento(raiz: ET.Element, valores: dict) -> None:
        documento = ET.SubElement(raiz, "documento")
        for coluna in COLUNAS:
            valor = valores[coluna]
            if valor is None:
                continue
            ET.SubElement(documento, coluna).text = _texto(valor)


def _texto(valor) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return str(valor)
