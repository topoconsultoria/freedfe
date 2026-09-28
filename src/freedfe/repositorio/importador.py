"""Importação de XMLs externos (ERP, escritório contábil) para o repositório."""

from __future__ import annotations

import logging
from pathlib import Path

from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.repositorio.base import RepositorioDocumentos, ResultadoArmazenamento

logger = logging.getLogger(__name__)

TAMANHO_LOTE = 200


class ImportadorXml:
    """Importa XMLs de uma pasta (recursivamente) para o repositório.

    Cobre os documentos emitidos pelo próprio CNPJ, que o Web Service de
    distribuição não entrega ao emitente.
    """

    def __init__(self, repositorio: RepositorioDocumentos, tamanho_lote: int = TAMANHO_LOTE):
        self._repositorio = repositorio
        self._tamanho_lote = tamanho_lote

    def importar_pasta(self, pasta: str | Path) -> ResultadoArmazenamento:
        """Importa todos os ``*.xml`` da pasta e subpastas."""
        total = ResultadoArmazenamento()
        lote: list[DocumentoRecebido] = []
        for arquivo in sorted(Path(pasta).rglob("*.xml")):
            lote.append(DocumentoRecebido(xml=arquivo.read_bytes()))
            if len(lote) >= self._tamanho_lote:
                self._acumular(total, self._repositorio.armazenar(lote))
                lote = []
        if lote:
            self._acumular(total, self._repositorio.armazenar(lote))
        logger.info("Importação de %s: %s documentos, %s eventos, %s não reconhecidos.",
                    pasta, total.documentos, total.eventos, total.nao_reconhecidos)
        return total

    @staticmethod
    def _acumular(total: ResultadoArmazenamento, parcial: ResultadoArmazenamento) -> None:
        total.documentos += parcial.documentos
        total.eventos += parcial.eventos
        total.nao_reconhecidos += parcial.nao_reconhecidos
