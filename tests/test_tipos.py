import pytest

from freedfe.excecoes import (
    ComunicacaoErro,
    EmissaoIncertaErro,
    ErroNfse,
    RejeicaoNfseErro,
    ValidacaoErro,
)
from freedfe.tipos import ModeloDocumento, SituacaoDocumento

from fabrica_nfse import gerar_chave_nfse
from fabrica_xml import CNPJ_FORNECEDOR, gerar_chave


def test_modelo_nfse_por_chave_de_50_posicoes():
    assert ModeloDocumento.de_chave(gerar_chave_nfse()) is ModeloDocumento.NFSE
    assert ModeloDocumento.de_chave(gerar_chave("55", CNPJ_FORNECEDOR, 1)) is ModeloDocumento.NFE
    assert ModeloDocumento.normalizar("NFSe") is ModeloDocumento.NFSE


def test_nfse_nao_tem_codigo_de_modelo():
    with pytest.raises(ValidacaoErro):
        ModeloDocumento.NFSE.codigo  # noqa: B018
    with pytest.raises(ValidacaoErro):
        ModeloDocumento.de_codigo("99")
    assert ModeloDocumento.NFE.codigo == "55"


def test_situacao_substituido():
    assert SituacaoDocumento("substituido") is SituacaoDocumento.SUBSTITUIDO


def test_rejeicao_nfse_expoe_codigos_e_mensagem():
    erro = RejeicaoNfseErro([ErroNfse("E0712", "totTrib incompatível", "infDPS/valores"),
                             ErroNfse("E0004")], status_http=400, id_dps="DPS1")
    assert erro.codigos == ["E0712", "E0004"]
    assert "E0712: totTrib incompatível (infDPS/valores)" in str(erro)
    assert "HTTP 400" in str(erro) and erro.id_dps == "DPS1"


def test_emissao_incerta_e_erro_de_comunicacao_e_manda_reconciliar():
    erro = EmissaoIncertaErro("Timeout.", "DPS123")
    assert isinstance(erro, ComunicacaoErro) and erro.id_dps == "DPS123"
    assert "reconciliar" in str(erro)
