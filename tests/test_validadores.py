import pytest

from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.validadores import formatar_nsu, validar_chave, validar_cnpj, validar_cpf

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
