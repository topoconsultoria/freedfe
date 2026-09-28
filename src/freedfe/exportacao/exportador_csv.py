"""Exportação em CSV (padrão Excel pt-BR: separador ``;`` e vírgula decimal)."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from decimal import Decimal

from freedfe.documentos.resumo import ResumoDocumento
from freedfe.exportacao.base import COLUNAS, Exportador, linha


class ExportadorCsv(Exportador):
    """CSV com cabeçalho. Ao gravar em arquivo, usa UTF-8 com BOM para o Excel.

    Args:
        separador: separador de campos.
        separador_decimal: ``","`` (pt-BR) ou ``"."``.
    """

    extensao = "csv"
    codificacao_arquivo = "utf-8-sig"

    def __init__(self, separador: str = ";", separador_decimal: str = ","):
        self._separador = separador
        self._separador_decimal = separador_decimal

    def exportar(self, resumos: Sequence[ResumoDocumento]) -> str:
        saida = io.StringIO()
        escritor = csv.DictWriter(saida, fieldnames=COLUNAS, delimiter=self._separador,
                                  lineterminator="\r\n")
        escritor.writeheader()
        for resumo in resumos:
            escritor.writerow({k: self._formatar(v) for k, v in linha(resumo).items()})
        return saida.getvalue()

    def _formatar(self, valor) -> str:
        if valor is None:
            return ""
        if isinstance(valor, bool):
            return "S" if valor else "N"
        if isinstance(valor, Decimal):
            return f"{valor:.2f}".replace(".", self._separador_decimal)
        if hasattr(valor, "isoformat"):
            return valor.isoformat()
        return str(valor)
