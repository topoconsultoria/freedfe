"""Geração de PDF (DANFE, DACTE, DAMDFE) a partir do XML completo."""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from pathlib import Path

from freedfe.excecoes import DependenciaAusenteErro
from freedfe.tipos import ModeloDocumento, SituacaoDocumento


class GeradorPdf(ABC):
    """Contrato de geração do documento auxiliar em PDF."""

    @abstractmethod
    def gerar(self, xml: bytes, modelo: ModeloDocumento, destino: str | Path,
              situacao: SituacaoDocumento | None = None) -> Path:
        """Gera o PDF do XML completo e devolve o caminho gravado.

        ``situacao`` é informada apenas para NFS-e (marca d'água de cancelada/substituída);
        geradores que atendam NFS-e devem aceitá-la.
        """


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

    def gerar(self, xml: bytes, modelo: ModeloDocumento, destino: str | Path,
              situacao: SituacaoDocumento | None = None) -> Path:
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


class GeradorPdfPadrao(GeradorPdf):
    """Gerador padrão da fachada: DANFSe local para NFS-e e BrazilFiscalReport para o restante.

    Args:
        url_consulta: função ``chave -> URL`` do QR Code do DANFSe.
        documentos_dfe: gerador de NF-e/CT-e/MDF-e (padrão: ``GeradorPdfBrazilFiscalReport``).
        danfse: gerador de NFS-e (padrão: ``GeradorDanfse``).
    """

    def __init__(self, url_consulta=None, documentos_dfe: GeradorPdf | None = None,
                 danfse: GeradorPdf | None = None):
        self._url_consulta = url_consulta
        self._documentos_dfe = documentos_dfe or GeradorPdfBrazilFiscalReport()
        self._danfse = danfse

    def gerar(self, xml: bytes, modelo: ModeloDocumento, destino: str | Path,
              situacao: SituacaoDocumento | None = None) -> Path:
        modelo = ModeloDocumento.normalizar(modelo)
        if modelo is ModeloDocumento.NFSE:
            return self._gerador_danfse().gerar(xml, modelo, destino, situacao)
        return self._documentos_dfe.gerar(xml, modelo, destino)

    def _gerador_danfse(self) -> GeradorPdf:
        if self._danfse is None:
            from freedfe.pdf.danfse import GeradorDanfse  # reportlab só é exigido aqui
            self._danfse = GeradorDanfse(self._url_consulta)
        return self._danfse
