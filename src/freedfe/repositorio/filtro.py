"""Critérios de consulta aos documentos armazenados."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

from freedfe.documentos.resumo import ResumoDocumento
from freedfe.excecoes import ValidacaoErro
from freedfe.tipos import ModeloDocumento, SituacaoDocumento


class Direcao(str, Enum):
    """Papel do CNPJ/CPF no documento."""

    EMITIDOS = "emitidos"  # CNPJ/CPF é o emitente ("de")
    RECEBIDOS = "recebidos"  # CNPJ/CPF é destinatário, tomador, contratante etc. ("para")
    TODOS = "todos"


@dataclass
class FiltroConsulta:
    """Filtro por período de emissão, modelo, CNPJ/CPF e direção.

    Attributes:
        data_inicial: data de emissão inicial (inclusiva). ``None`` = sem limite.
        data_final: data de emissão final (inclusiva). ``None`` = sem limite.
        modelos: modelos desejados. Vazio = todos.
        cnpj_cpf: CNPJ/CPF de referência para a direção. ``None`` = o interessado.
        direcao: documentos emitidos pelo CNPJ/CPF, recebidos por ele ou ambos.
        somente_xml_completo: ignora resumos (``resNFe``) sem XML completo.
        situacoes: situações aceitas. Vazio = todas.
    """

    data_inicial: dt.date | None = None
    data_final: dt.date | None = None
    modelos: list[ModeloDocumento] = field(default_factory=list)
    cnpj_cpf: str | None = None
    direcao: Direcao = Direcao.TODOS
    somente_xml_completo: bool = False
    situacoes: list[SituacaoDocumento] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.modelos = [ModeloDocumento.normalizar(m) for m in _como_lista(self.modelos)]
        self.situacoes = [SituacaoDocumento(s) for s in _como_lista(self.situacoes)]
        self.direcao = Direcao(self.direcao)
        if self.cnpj_cpf:
            self.cnpj_cpf = self.cnpj_cpf.strip().upper()
        if self.data_inicial and self.data_final and self.data_inicial > self.data_final:
            raise ValidacaoErro("data_inicial deve ser menor ou igual a data_final.")

    def aceita(self, resumo: ResumoDocumento) -> bool:
        """True se o documento atende a todos os critérios."""
        return (self._aceita_modelo(resumo) and self._aceita_periodo(resumo)
                and self._aceita_direcao(resumo) and self._aceita_situacao(resumo)
                and (resumo.xml_completo or not self.somente_xml_completo))

    def _aceita_modelo(self, resumo: ResumoDocumento) -> bool:
        return not self.modelos or resumo.modelo in self.modelos

    def _aceita_periodo(self, resumo: ResumoDocumento) -> bool:
        if not (self.data_inicial or self.data_final):
            return True
        if resumo.data_emissao is None:
            return False
        # Data "de calendário" do próprio XML (fuso do emissor), sem conversão.
        emissao = resumo.data_emissao.date()
        if self.data_inicial and emissao < self.data_inicial:
            return False
        return not (self.data_final and emissao > self.data_final)

    def _aceita_direcao(self, resumo: ResumoDocumento) -> bool:
        if not self.cnpj_cpf:
            return True
        emitido = resumo.emitente_documento == self.cnpj_cpf
        recebido = self.cnpj_cpf in resumo.participantes()
        if self.direcao is Direcao.EMITIDOS:
            return emitido
        if self.direcao is Direcao.RECEBIDOS:
            return recebido
        return emitido or recebido

    def _aceita_situacao(self, resumo: ResumoDocumento) -> bool:
        return not self.situacoes or resumo.situacao in self.situacoes


def _como_lista(valor) -> list:
    """Aceita item único, lista ou ``None``."""
    if valor is None:
        return []
    if isinstance(valor, (str, Enum)):
        return [valor]
    if isinstance(valor, Iterable):
        return list(valor)
    return [valor]
