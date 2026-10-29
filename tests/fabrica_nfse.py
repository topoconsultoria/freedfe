"""Dados sintéticos de NFS-e Nacional para os testes (sem rede, sem dados reais)."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from freedfe.nfse.envelope import comprimir_b64
from freedfe.nfse.modelo import (
    BeneficioMunicipal,
    Destinatario,
    DiferimentoIbsCbs,
    Dps,
    Endereco,
    ExigibilidadeSuspensa,
    IbsCbs,
    Intermediario,
    Obra,
    OpcaoSimplesNacional,
    PisCofins,
    Prestador,
    RegimeApuracaoSimples,
    RegimeEspecialTributacao,
    RetencaoIssqn,
    Servico,
    Substituicao,
    TipoSuspensao,
    Tomador,
    TotTrib,
    TribFederal,
    TribMunicipal,
    TributacaoRegularIbsCbs,
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


def servico_com_nbs(**alteracoes) -> Servico:
    dados = dict(codigo_tributacao_nacional="010101",
                 descricao="Desenvolvimento de sistema sob encomenda",
                 codigo_municipio_prestacao=MUNICIPIO, codigo_nbs="115022000")
    dados.update(alteracoes)
    return Servico(**dados)


def ibs_cbs_minimo(**alteracoes) -> IbsCbs:
    dados = dict(codigo_indicador_operacao="100301", indicador_destinatario=0, cst="000",
                 codigo_classificacao="000001")
    dados.update(alteracoes)
    return IbsCbs(**dados)


def ibs_cbs_completo() -> IbsCbs:
    return IbsCbs(
        codigo_indicador_operacao="100301", indicador_destinatario=1, cst="000",
        codigo_classificacao="000001", indicador_consumidor_final=1, tipo_operacao=1,
        ente_governamental=1, notas_referenciadas=(gerar_chave_nfse(numero=5),),
        destinatario=Destinatario(cnpj=CNPJ_TOMADOR, nome="DESTINATARIO LTDA",
                                  endereco=endereco_tomador(), telefone="7133334444",
                                  email="dest@exemplo.com.br"),
        codigo_credito_presumido="01",
        tributacao_regular=TributacaoRegularIbsCbs("000", "000001"),
        diferimento=DiferimentoIbsCbs("10.00", "20.00", "30.00"),
        ajuste_base_calculo="12.34")


def dps_completa(**alteracoes) -> Dps:
    """DPS com todos os grupos opcionais suportados (emitente prestador, sem endereço)."""
    dados = dict(
        serie=7, numero=1234, data_competencia=dt.date(2026, 10, 28), data_emissao=DATA_EMISSAO,
        codigo_municipio_emissor=MUNICIPIO,
        prestador=Prestador(cnpj=CNPJ_PRESTADOR, inscricao_municipal="152422",
                            opcao_simples=OpcaoSimplesNacional.NAO_OPTANTE,
                            regime_especial=RegimeEspecialTributacao.SOCIEDADE_DE_PROFISSIONAIS,
                            telefone="7133334444", email="fiscal@prestador.com.br"),
        tomador=tomador_pj(inscricao_municipal="9988", endereco=endereco_tomador(),
                           telefone="7199998888", email="tomador@exemplo.com.br"),
        intermediario=Intermediario(cpf=CPF_TOMADOR, nome="Intermediario Pessoa Fisica"),
        servico=servico_com_nbs(
            codigo_tributacao_nacional="070201", descricao="Obra de engenharia\nSegunda linha",
            codigo_municipio_prestacao=MUNICIPIO_TOMADOR, codigo_tributacao_municipal="001",
            codigo_interno="ABC123",
            obra=Obra(codigo_obra="OB-1", inscricao_imobiliaria="IMOB123"),
            informacoes_complementares="Contrato 123/2026", pedido="PED-9",
            documento_referencia="DOC-1", documento_tecnico="DRT-1"),
        valores=Valores("1500.50", recebido="1500.50", desconto_incondicionado="10.00",
                        desconto_condicionado="5.00", deducao_valor="100.00"),
        trib_municipal=TribMunicipal(
            retencao=RetencaoIssqn.RETIDO_PELO_TOMADOR, aliquota="2.50",
            suspensao=ExigibilidadeSuspensa(TipoSuspensao.DECISAO_JUDICIAL, "1" * 30),
            beneficio=BeneficioMunicipal("1" * 14, reducao_percentual="10.00")),
        trib_federal=TribFederal(
            PisCofins("01", base_calculo="1400.50", aliquota_pis="0.65", aliquota_cofins="3.00",
                      valor_pis="9.10", valor_cofins="42.02", tipo_retencao=0),
            retencao_cp="0.00", retencao_irrf="21.00", retencao_csll="14.00"),
        tot_trib=TotTrib.percentuais("13.45", "0.00", "2.50"),
        substituicao=Substituicao(gerar_chave_nfse(numero=3), "01"),
        ibs_cbs=ibs_cbs_completo())
    dados.update(alteracoes)
    return Dps(**dados)
