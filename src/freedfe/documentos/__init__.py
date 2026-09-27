"""Leitura e extração de dados dos XMLs fiscais."""

from freedfe.documentos.leitor import LeitorDocumentoFiscal
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento

__all__ = ["EventoDocumento", "LeitorDocumentoFiscal", "ResumoDocumento"]
