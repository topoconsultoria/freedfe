"""Exportação em JSON (lista de objetos, datas ISO 8601)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal

from freedfe.documentos.resumo import ResumoDocumento
from freedfe.exportacao.base import Exportador, linha


class ExportadorJson(Exportador):
    """JSON com uma lista de documentos. Valores monetários como número."""

    extensao = "json"

    def __init__(self, indentacao: int | None = 2):
        self._indentacao = indentacao

    def exportar(self, resumos: Sequence[ResumoDocumento]) -> str:
        return json.dumps([linha(r) for r in resumos], ensure_ascii=False,
                          indent=self._indentacao, default=_serializar)


def _serializar(valor):
    if isinstance(valor, Decimal):
        return float(valor)
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    raise TypeError(f"Tipo não serializável: {type(valor).__name__}")
