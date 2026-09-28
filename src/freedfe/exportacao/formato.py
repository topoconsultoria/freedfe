"""Formatos de saída da listagem de documentos."""

from __future__ import annotations

from enum import Enum

from freedfe.excecoes import ValidacaoErro


class FormatoSaida(str, Enum):
    """Formato do retorno de ``FreeDFe.consultar``.

    ``LISTA`` devolve objetos ``ResumoDocumento``; os demais devolvem o conteúdo
    serializado (``str`` para CSV/JSON/XML e ``bytes`` para XLSX).
    """

    LISTA = "lista"
    CSV = "csv"
    JSON = "json"
    XML = "xml"
    XLSX = "xlsx"

    @classmethod
    def normalizar(cls, valor: FormatoSaida | str | None) -> FormatoSaida:
        """Aceita o enum, o texto (``"csv"``, ``"XLSX"``) ou ``None`` (= ``LISTA``)."""
        if valor is None:
            return cls.LISTA
        if isinstance(valor, cls):
            return valor
        try:
            return cls(str(valor).strip().lower())
        except ValueError as erro:
            opcoes = ", ".join(f.value for f in cls)
            raise ValidacaoErro(f"Formato inválido: {valor}. Use: {opcoes}.") from erro
