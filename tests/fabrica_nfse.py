"""Dados sintéticos de NFS-e Nacional para os testes (sem rede, sem dados reais)."""

from __future__ import annotations

from freedfe.validadores import _digito_modulo_11

from fabrica_xml import CNPJ_INTERESSADO

MUNICIPIO = "2927408"  # Salvador/BA (código IBGE de 7 dígitos)
MUNICIPIO_TOMADOR = "2910800"  # Feira de Santana/BA
CNPJ_PRESTADOR = CNPJ_INTERESSADO
CNPJ_TOMADOR = "11222333000181"
CPF_TOMADOR = "52998224725"


def gerar_chave_nfse(municipio: str = MUNICIPIO, documento: str = CNPJ_PRESTADOR,
                     numero: int = 1, aamm: str = "2610", codigo: int = 123456789,
                     ambiente_gerador: str = "2") -> str:
    """Chave de 50 posições; o DV usa módulo 11 por hipótese (a validação é estrutural)."""
    tipo = "2" if len(documento) == 14 else "1"
    inscricao = documento.rjust(14, "0")
    corpo = f"{municipio}{ambiente_gerador}{tipo}{inscricao}{numero:013d}{aamm}{codigo:09d}"
    return corpo + str(_digito_modulo_11(corpo, 9))
