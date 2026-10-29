import pytest

from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.validadores import (
    exigir_chave_nfse,
    exigir_codigo_ibge,
    exigir_id_dps,
    formatar_nsu,
    validar_chave,
    validar_chave_nfse,
    validar_cnpj,
    validar_codigo_ibge,
    validar_cpf,
    validar_id_dps,
)

from fabrica_nfse import gerar_chave_nfse
from fabrica_xml import CNPJ_INTERESSADO, gerar_chave


@pytest.mark.parametrize("cnpj", ["11222333000181", "12ABC34501DE35", "12abc34501de35"])
def test_cnpj_valido_numerico_e_alfanumerico(cnpj):
    assert validar_cnpj(cnpj)


@pytest.mark.parametrize("cnpj", ["11222333000182", "00000000000000", "1122233300018", "",
                                  "12ABC34501DE36"])
def test_cnpj_invalido(cnpj):
    assert not validar_cnpj(cnpj)


def test_cpf():
    assert validar_cpf("52998224725")
    assert not validar_cpf("52998224724")
    assert not validar_cpf("11111111111")


def test_chave_numerica_e_com_cnpj_alfanumerico():
    assert validar_chave(gerar_chave("55", CNPJ_INTERESSADO, 123))
    assert validar_chave(gerar_chave("55", "12ABC34501DE35", 7))
    chave = gerar_chave("55", CNPJ_INTERESSADO, 123)
    assert not validar_chave(chave[:-1] + str((int(chave[-1]) + 1) % 10))


def test_formatar_nsu():
    assert formatar_nsu(0) == "000000000000000"
    assert formatar_nsu("123") == "000000000000123"
    with pytest.raises(ValidacaoErro):
        formatar_nsu("-1")
    with pytest.raises(ValidacaoErro):
        formatar_nsu(10 ** 15)


def test_interessado_exige_exatamente_um_documento():
    with pytest.raises(ValidacaoErro):
        Interessado()
    with pytest.raises(ValidacaoErro):
        Interessado(cnpj=CNPJ_INTERESSADO, cpf="52998224725")
    with pytest.raises(ValidacaoErro):
        Interessado(cnpj=CNPJ_INTERESSADO, uf="99")
    assert Interessado(cpf=" 52998224725 ").elemento_xml() == "<CPF>52998224725</CPF>"


def test_chave_nfse_estrutura():
    chave = gerar_chave_nfse()
    assert len(chave) == 50 and validar_chave_nfse(chave)
    assert validar_chave_nfse(gerar_chave_nfse(documento="12ABC34501DE35"))
    assert validar_chave_nfse(gerar_chave_nfse(documento="52998224725"))
    assert exigir_chave_nfse(f" {chave.lower()} ") == chave


@pytest.mark.parametrize("chave", ["", "123", "x" * 50, gerar_chave_nfse()[:-1],
                                   gerar_chave_nfse() + "0",
                                   gerar_chave_nfse()[:7] + "9" + gerar_chave_nfse()[8:]])
def test_chave_nfse_invalida(chave):
    assert not validar_chave_nfse(chave)
    with pytest.raises(ValidacaoErro):
        exigir_chave_nfse(chave)


def test_id_dps():
    valido = "DPS" + "2927408" + "2" + CNPJ_INTERESSADO + "00001" + "000000000000001"
    assert len(valido) == 45 and validar_id_dps(valido)
    assert exigir_id_dps(valido.lower()) == valido
    tipo_invalido = valido[:10] + "3" + valido[11:]
    for invalido in ("", valido[:-1], valido.replace("DPS", "XYZ"), tipo_invalido):
        assert not validar_id_dps(invalido)
    with pytest.raises(ValidacaoErro):
        exigir_id_dps("DPS123")


def test_codigo_ibge():
    assert validar_codigo_ibge("2927408") and validar_codigo_ibge("1100015")
    for invalido in ("", "292740", "29274081", "9927408", "29274a8"):
        assert not validar_codigo_ibge(invalido)
    assert exigir_codigo_ibge("2927408") == "2927408"
    with pytest.raises(ValidacaoErro, match="tomador"):
        exigir_codigo_ibge("123", campo="tomador")
