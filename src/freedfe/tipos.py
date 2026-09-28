"""Enumerações compartilhadas pela biblioteca."""

from __future__ import annotations

from enum import Enum, IntEnum

from freedfe.excecoes import ValidacaoErro


class ModeloDocumento(str, Enum):
    """Modelos de DF-e atendidos pelos Web Services de Distribuição."""

    NFE = "nfe"
    CTE = "cte"
    MDFE = "mdfe"

    @property
    def codigo(self) -> str:
        """Código do modelo fiscal (posições 21-22 da chave de acesso)."""
        return _CODIGOS_MODELO[self]

    @classmethod
    def de_codigo(cls, codigo: str) -> ModeloDocumento:
        """Obtém o modelo a partir do código fiscal (``55``, ``57``, ``58``)."""
        for modelo, cod in _CODIGOS_MODELO.items():
            if cod == codigo:
                return modelo
        raise ValidacaoErro(f"Modelo de documento não suportado: {codigo}")

    @classmethod
    def de_chave(cls, chave: str) -> ModeloDocumento:
        """Obtém o modelo a partir da chave de acesso de 44 posições."""
        return cls.de_codigo(chave[20:22])

    @classmethod
    def normalizar(cls, valor: ModeloDocumento | str) -> ModeloDocumento:
        """Aceita o enum ou o texto (``"nfe"``, ``"NFe"``) e devolve o enum."""
        if isinstance(valor, cls):
            return valor
        try:
            return cls(str(valor).strip().lower())
        except ValueError as erro:
            raise ValidacaoErro(f"Modelo de documento inválido: {valor}") from erro


_CODIGOS_MODELO = {
    ModeloDocumento.NFE: "55",
    ModeloDocumento.CTE: "57",
    ModeloDocumento.MDFE: "58",
}


class Ambiente(IntEnum):
    """Ambiente SEFAZ (``tpAmb``)."""

    PRODUCAO = 1
    HOMOLOGACAO = 2

    @property
    def nome_pasta(self) -> str:
        """Nome usado na estrutura de pastas para separar os ambientes."""
        return "producao" if self is Ambiente.PRODUCAO else "homologacao"


class SituacaoDocumento(str, Enum):
    """Situação do documento conhecida localmente."""

    AUTORIZADO = "autorizado"
    DENEGADO = "denegado"
    CANCELADO = "cancelado"
