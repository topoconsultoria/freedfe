"""Contrato dos exportadores e layout padronizado das colunas."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from freedfe.documentos.resumo import ResumoDocumento

# Ordem e nomes das colunas, iguais em todos os formatos.
COLUNAS = (
    "chave", "modelo", "numero", "serie", "data_emissao",
    "emitente_documento", "emitente_nome", "destinatario_documento", "destinatario_nome",
    "valor_total", "situacao", "protocolo", "xml_completo", "tipo_xml", "nsu", "caminho_xml",
)


def linha(resumo: ResumoDocumento) -> dict:
    """Valores de um documento com tipos nativos (datetime, Decimal, bool, str)."""
    return {
        "chave": resumo.chave,
        "modelo": resumo.modelo.name,
        "numero": resumo.numero,
        "serie": resumo.serie,
        "data_emissao": resumo.data_emissao,
        "emitente_documento": resumo.emitente_documento,
        "emitente_nome": resumo.emitente_nome,
        "destinatario_documento": resumo.destinatario_documento,
        "destinatario_nome": resumo.destinatario_nome,
        "valor_total": resumo.valor_total,
        "situacao": resumo.situacao.value,
        "protocolo": resumo.protocolo,
        "xml_completo": resumo.xml_completo,
        "tipo_xml": resumo.tipo_xml,
        "nsu": resumo.nsu,
        "caminho_xml": resumo.caminho_xml,
    }


class Exportador(ABC):
    """Converte uma lista de ``ResumoDocumento`` em um formato de arquivo."""

    extensao: str = ""
    codificacao_arquivo: str = "utf-8"

    @abstractmethod
    def exportar(self, resumos: Sequence[ResumoDocumento]) -> str | bytes:
        """Conteúdo serializado."""

    def gravar(self, resumos: Sequence[ResumoDocumento], destino: str | Path) -> Path:
        """Grava o conteúdo em arquivo e devolve o caminho."""
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        conteudo = self.exportar(resumos)
        if isinstance(conteudo, bytes):
            destino.write_bytes(conteudo)
        else:
            destino.write_text(conteudo, encoding=self.codificacao_arquivo, newline="")
        return destino
