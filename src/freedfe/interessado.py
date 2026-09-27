"""Identificação do interessado (titular do CNPJ ou CPF consultado)."""

from __future__ import annotations

from dataclasses import dataclass

from freedfe.excecoes import ValidacaoErro
from freedfe.validadores import validar_cnpj, validar_cpf, validar_uf


@dataclass(frozen=True)
class Interessado:
    """CNPJ ou CPF de interesse e UF do autor da consulta.

    Attributes:
        cnpj: CNPJ com 14 posições (numérico ou alfanumérico), sem máscara.
        cpf: CPF com 11 dígitos, sem máscara.
        uf: código IBGE da UF (``cUFAutor``). Obrigatório para CT-e e MDF-e.
    """

    cnpj: str | None = None
    cpf: str | None = None
    uf: str | None = None

    def __post_init__(self) -> None:
        if bool(self.cnpj) == bool(self.cpf):
            raise ValidacaoErro("Informe CNPJ ou CPF (exatamente um).")
        if self.cnpj:
            object.__setattr__(self, "cnpj", self.cnpj.strip().upper())
            if not validar_cnpj(self.cnpj):
                raise ValidacaoErro(f"CNPJ inválido: {self.cnpj}")
        if self.cpf:
            object.__setattr__(self, "cpf", self.cpf.strip())
            if not validar_cpf(self.cpf):
                raise ValidacaoErro(f"CPF inválido: {self.cpf}")
        if self.uf is not None and not validar_uf(self.uf):
            raise ValidacaoErro(f"UF (código IBGE) inválida: {self.uf}")

    @property
    def documento(self) -> str:
        """CNPJ ou CPF, conforme o que foi informado."""
        return self.cnpj or self.cpf

    @property
    def tag(self) -> str:
        """Nome da tag XML do documento (``CNPJ`` ou ``CPF``)."""
        return "CNPJ" if self.cnpj else "CPF"

    def elemento_xml(self) -> str:
        """Trecho XML ``<CNPJ>..</CNPJ>`` ou ``<CPF>..</CPF>``."""
        return f"<{self.tag}>{self.documento}</{self.tag}>"
