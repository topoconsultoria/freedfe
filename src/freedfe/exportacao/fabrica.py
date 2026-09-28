"""Seleção do exportador pelo formato de saída."""

from __future__ import annotations

from freedfe.excecoes import ValidacaoErro
from freedfe.exportacao.base import Exportador
from freedfe.exportacao.exportador_csv import ExportadorCsv
from freedfe.exportacao.exportador_json import ExportadorJson
from freedfe.exportacao.exportador_xlsx import ExportadorXlsx
from freedfe.exportacao.exportador_xml import ExportadorXml
from freedfe.exportacao.formato import FormatoSaida


class FabricaExportador:
    """Registro de exportadores por formato. Permite registrar formatos adicionais."""

    def __init__(self):
        self._exportadores: dict[FormatoSaida, type[Exportador]] = {
            FormatoSaida.CSV: ExportadorCsv,
            FormatoSaida.JSON: ExportadorJson,
            FormatoSaida.XML: ExportadorXml,
            FormatoSaida.XLSX: ExportadorXlsx,
        }

    def registrar(self, formato: FormatoSaida, classe: type[Exportador]) -> None:
        """Substitui ou acrescenta o exportador de um formato."""
        self._exportadores[formato] = classe

    def obter(self, formato: FormatoSaida | str) -> Exportador:
        """Instância do exportador do formato informado."""
        formato = FormatoSaida.normalizar(formato)
        if formato not in self._exportadores:
            raise ValidacaoErro(f"Formato sem exportador: {formato.value}")
        return self._exportadores[formato]()
