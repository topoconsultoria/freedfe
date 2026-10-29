import datetime as dt

import pytest

from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.identificadores import (
    chave_de_id_nfse,
    decompor_chave,
    formatar_chave,
    id_dps,
    id_nfse,
    id_pedido_evento,
    inscricao_14,
    normalizar_numero_dps,
    normalizar_serie,
    tipo_inscricao,
)
from freedfe.nfse.leiaute import VersaoLeiaute
from freedfe.validadores import validar_id_dps

from fabrica_nfse import CNPJ_PRESTADOR, CPF_TOMADOR, MUNICIPIO, gerar_chave_nfse


def test_id_dps_cnpj():
    identificador = id_dps(MUNICIPIO, CNPJ_PRESTADOR, 1, 123)
    assert identificador == f"DPS{MUNICIPIO}2{CNPJ_PRESTADOR}00001000000000000123"
    assert len(identificador) == 45 and validar_id_dps(identificador)


def test_id_dps_cpf_completa_com_zeros_e_tipo_1():
    identificador = id_dps(MUNICIPIO, CPF_TOMADOR, "7", "9")
    assert identificador == f"DPS{MUNICIPIO}1000{CPF_TOMADOR}00007000000000000009"
    assert validar_id_dps(identificador)


def test_id_dps_cnpj_alfanumerico():
    identificador = id_dps(MUNICIPIO, "12ABC34501DE35", 80001, 1)
    assert identificador == f"DPS{MUNICIPIO}212ABC34501DE3580001000000000000001"
    assert validar_id_dps(identificador)


@pytest.mark.parametrize("args", [
    ("123", CNPJ_PRESTADOR, 1, 1),            # IBGE inválido
    (MUNICIPIO, "11222333000182", 1, 1),      # DV do CNPJ
    (MUNICIPIO, "123", 1, 1),                 # documento
    (MUNICIPIO, CNPJ_PRESTADOR, 0, 1),        # série
    (MUNICIPIO, CNPJ_PRESTADOR, 100000, 1),   # série
    (MUNICIPIO, CNPJ_PRESTADOR, "ab", 1),
    (MUNICIPIO, CNPJ_PRESTADOR, 1, 0),        # nDPS
    (MUNICIPIO, CNPJ_PRESTADOR, 1, 10 ** 15),
])
def test_id_dps_entradas_invalidas(args):
    with pytest.raises(ValidacaoErro):
        id_dps(*args)


def test_tipo_e_inscricao():
    assert tipo_inscricao(CPF_TOMADOR) == "1" and tipo_inscricao(CNPJ_PRESTADOR) == "2"
    assert inscricao_14(CPF_TOMADOR) == "000" + CPF_TOMADOR
    assert normalizar_serie(" 00012 ") == 12 and normalizar_numero_dps("000042") == 42


def test_decompor_chave_cnpj_e_cpf():
    chave = gerar_chave_nfse(numero=77, aamm="2610", codigo=555)
    partes = decompor_chave(chave)
    assert (partes.municipio, partes.ambiente_gerador, partes.tipo_inscricao) == (
        MUNICIPIO, "2", "2")
    assert partes.documento == CNPJ_PRESTADOR and partes.numero == 77
    assert partes.codigo == "000000555" and partes.dv == chave[-1]
    assert partes.gerada_pela_sefin_nacional and partes.mes_emissao == dt.date(2026, 10, 1)
    cpf = decompor_chave(gerar_chave_nfse(documento=CPF_TOMADOR, ambiente_gerador="1"))
    assert cpf.documento == CPF_TOMADOR and not cpf.gerada_pela_sefin_nacional


def test_id_nfse_e_inverso():
    chave = gerar_chave_nfse()
    assert id_nfse(chave) == "NFS" + chave and len(id_nfse(chave)) == 53
    assert chave_de_id_nfse("NFS" + chave) == chave
    with pytest.raises(ValidacaoErro):
        chave_de_id_nfse("XYZ" + chave)


def test_id_pedido_evento_59_posicoes_em_ambos_os_leiautes():
    chave = gerar_chave_nfse()
    for leiaute in (None, VersaoLeiaute.NT004_V2, VersaoLeiaute.NT009_V101):
        identificador = id_pedido_evento(chave, "101101", leiaute)
        assert identificador == f"PRE{chave}101101" and len(identificador) == 59


def test_id_pedido_evento_legado_com_npedido_e_validacoes():
    chave = gerar_chave_nfse()
    assert id_pedido_evento(chave, "101101", n_pedido=1) == f"PRE{chave}101101001"
    for tipo in ("1011", "abcdef", ""):
        with pytest.raises(ValidacaoErro):
            id_pedido_evento(chave, tipo)
    with pytest.raises(ValidacaoErro):
        id_pedido_evento(chave, "101101", n_pedido=1000)
    with pytest.raises(ValidacaoErro):
        id_pedido_evento("123", "101101")


def test_formatar_chave_em_grupos_de_quatro():
    texto = formatar_chave(gerar_chave_nfse())
    grupos = texto.split(" ")
    assert len(grupos) == 13 and all(len(g) == 4 for g in grupos[:-1]) and len(grupos[-1]) == 2
    assert texto.replace(" ", "") == gerar_chave_nfse()
