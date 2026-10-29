"""Dados sintéticos de NFS-e Nacional para os testes (sem rede, sem dados reais)."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from freedfe.nfse.envelope import comprimir_b64
from freedfe.nfse.modelo import (
    Dps,
    Endereco,
    OpcaoSimplesNacional,
    Prestador,
    RegimeApuracaoSimples,
    Servico,
    Tomador,
    TotTrib,
    TribMunicipal,
    Valores,
)
from freedfe.tipos import FUSO_BRASILIA
from freedfe.transporte import RespostaHttp
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


def resposta_emissao(xml_nfse: bytes, chave: str, status: int = 201,
                     estilo: str = "camel", **extras) -> RespostaHttp:
    """Resposta de sucesso do ``POST /nfse`` (``estilo``: ``camel`` ou ``pascal``)."""
    corpo = {"chaveAcesso": chave, "nfseXmlGZipB64": comprimir_b64(xml_nfse), "alertas": [],
             "tipoAmbiente": 2, "versaoAplicativo": "SefinNacional_1.6.0",
             "dataHoraProcessamento": "2026-10-29T10:00:00-03:00", **extras}
    if estilo == "pascal":
        corpo = {nome[0].upper() + nome[1:]: valor for nome, valor in corpo.items()}
    return RespostaHttp(status, json.dumps(corpo).encode(), {"content-type": "application/json"})


def resposta_erro(codigo: str, descricao: str = "Erro de teste", status: int = 400,
                  estilo: str = "camel", complemento: str = "") -> RespostaHttp:
    """Rejeição de negócio: ``estilo`` ``camel`` (erros/codigo) ou ``pascal`` (erro/Codigo)."""
    if estilo == "pascal":
        corpo = {"erro": [{"Codigo": codigo, "Descricao": descricao, "Complemento": complemento}]}
    else:
        corpo = {"erros": [{"codigo": codigo, "descricao": descricao,
                            "complemento": complemento}]}
    return RespostaHttp(status, json.dumps(corpo).encode(), {"content-type": "application/json"})


# ---------------------------------------------------------------------------
# Modelo da DPS
# ---------------------------------------------------------------------------

DATA_EMISSAO = dt.datetime(2026, 10, 29, 10, 0, 0, tzinfo=FUSO_BRASILIA)


def prestador_simples(**alteracoes) -> Prestador:
    """Prestador ME/EPP optante do Simples (emitente: sem endereço)."""
    dados = dict(cnpj=CNPJ_PRESTADOR, inscricao_municipal="152422",
                 opcao_simples=OpcaoSimplesNacional.ME_EPP,
                 regime_apuracao_simples=RegimeApuracaoSimples.FEDERAIS_E_MUNICIPAL_PELO_SIMPLES)
    dados.update(alteracoes)
    return Prestador(**dados)


def tomador_pj(**alteracoes) -> Tomador:
    dados = dict(cnpj=CNPJ_TOMADOR, nome="EMPRESA TOMADORA DE TESTE LTDA")
    dados.update(alteracoes)
    return Tomador(**dados)


def endereco_tomador() -> Endereco:
    return Endereco.nacional("Rua das Flores", "100", "Centro", MUNICIPIO_TOMADOR, "44001000",
                             complemento="Sala 2")


def dps_minima(**alteracoes) -> Dps:
    """DPS mínima válida: prestador do Simples, sem tomador identificado."""
    dados = dict(
        serie=900, numero=6, data_competencia=dt.date(2026, 10, 29), data_emissao=DATA_EMISSAO,
        codigo_municipio_emissor=MUNICIPIO, prestador=prestador_simples(),
        servico=Servico("010101", "Desenvolvimento de sistema sob encomenda",
                        codigo_municipio_prestacao=MUNICIPIO),
        valores=Valores(Decimal("1000.00")),
        trib_municipal=TribMunicipal(), tot_trib=TotTrib.simples("6.00"))
    dados.update(alteracoes)
    return Dps(**dados)
