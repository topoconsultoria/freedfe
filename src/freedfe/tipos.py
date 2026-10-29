"""Enumerações compartilhadas pela biblioteca."""

from __future__ import annotations

import datetime as dt
from enum import Enum, IntEnum

from freedfe.excecoes import ValidacaoErro

# Horário de Brasília (sem horário de verão desde 2019); usado em dhEvento/dhEmi.
FUSO_BRASILIA = dt.timezone(dt.timedelta(hours=-3))


class ModeloDocumento(str, Enum):
    """Modelos de documentos fiscais eletrônicos atendidos pela biblioteca.

    NF-e, CT-e e MDF-e vêm dos Web Services de Distribuição (SOAP). A NFS-e Nacional
    (``NFSE``) tem contrato próprio (REST/JSON) e a chave de acesso tem 50 posições, sem
    código de modelo.
    """

    NFE = "nfe"
    CTE = "cte"
    MDFE = "mdfe"
    NFSE = "nfse"

    @property
    def codigo(self) -> str:
        """Código do modelo fiscal (posições 21-22 da chave de acesso de 44 posições)."""
        try:
            return _CODIGOS_MODELO[self]
        except KeyError:
            raise ValidacaoErro(
                f"O modelo {self.value} não possui código de modelo na chave de acesso."
            ) from None

    @classmethod
    def de_codigo(cls, codigo: str) -> ModeloDocumento:
        """Obtém o modelo a partir do código fiscal (``55``, ``57``, ``58``)."""
        for modelo, cod in _CODIGOS_MODELO.items():
            if cod == codigo:
                return modelo
        raise ValidacaoErro(f"Modelo de documento não suportado: {codigo}")

    @classmethod
    def de_chave(cls, chave: str) -> ModeloDocumento:
        """Obtém o modelo a partir da chave de acesso (44 posições, ou 50 para NFS-e)."""
        if len(chave) == TAMANHO_CHAVE_NFSE:
            return cls.NFSE
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


TAMANHO_CHAVE_NFSE = 50

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
    SUBSTITUIDO = "substituido"
