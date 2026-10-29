"""Dados sintéticos de NFS-e Nacional para os testes (sem rede, sem dados reais)."""

from __future__ import annotations

import json

from freedfe.nfse.envelope import comprimir_b64
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
