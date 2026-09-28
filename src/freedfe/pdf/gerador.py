"""Geração de PDF (DANFE, DACTE, DAMDFE) a partir do XML completo."""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from pathlib import Path

from freedfe.excecoes import DependenciaAusenteErro
from freedfe.tipos import ModeloDocumento


class GeradorPdf(ABC):
    """Contrato de geração do documento auxiliar em PDF."""

    @abstractmethod
    def gerar(self, xml: bytes, modelo: ModeloDocumento, destino: str | Path) -> Path:
        """Gera o PDF do XML completo e devolve o caminho gravado."""


class GeradorPdfBrazilFiscalReport(GeradorPdf):
    """Implementação com a biblioteca BrazilFiscalReport (LGPL-3.0).

    Requer o extra ``pdf``: ``pip install 'freedfe[pdf]'``.
    """

    # modelo -> (módulo, classe) da BrazilFiscalReport
    CLASSES = {
        ModeloDocumento.NFE: ("brazilfiscalreport.danfe", "Danfe"),
        ModeloDocumento.CTE: ("brazilfiscalreport.dacte", "Dacte"),
        ModeloDocumento.MDFE: ("brazilfiscalreport.damdfe", "Damdfe"),
    }

    def gerar(self, xml: bytes, modelo: ModeloDocumento, destino: str | Path) -> Path:
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        classe = self._classe(ModeloDocumento.normalizar(modelo))
        documento = classe(xml=xml)  # bytes: respeita o encoding declarado no XML
        documento.output(str(destino))
        return destino

    def _classe(self, modelo: ModeloDocumento):
        nome_modulo, nome_classe = self.CLASSES[modelo]
        try:
            modulo = importlib.import_module(nome_modulo)
        except ImportError as erro:
            raise DependenciaAusenteErro(
                "Geração de PDF requer BrazilFiscalReport: pip install 'freedfe[pdf]'.") from erro
        return getattr(modulo, nome_classe)
