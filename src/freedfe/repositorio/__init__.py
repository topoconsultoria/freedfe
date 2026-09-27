"""Armazenamento local e consulta dos documentos."""

from freedfe.repositorio.arquivos import RepositorioArquivos
from freedfe.repositorio.base import RepositorioDocumentos, ResultadoArmazenamento
from freedfe.repositorio.filtro import Direcao, FiltroConsulta
from freedfe.repositorio.importador import ImportadorXml

__all__ = [
    "Direcao",
    "FiltroConsulta",
    "ImportadorXml",
    "RepositorioArquivos",
    "RepositorioDocumentos",
    "ResultadoArmazenamento",
]
