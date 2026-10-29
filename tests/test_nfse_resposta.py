import json

import pytest

from freedfe.excecoes import EmissaoIncertaErro, RejeicaoNfseErro, RespostaInvalidaErro
from freedfe.nfse.envelope import comprimir_b64
from freedfe.nfse.resposta import (
    campo,
    extrair_erros,
    interpretar_consulta_nfse,
    interpretar_emissao,
    ler_chave_do_xml,
)
from freedfe.transporte import RespostaHttp

from fabrica_nfse import gerar_chave_nfse, resposta_emissao, resposta_erro

CHAVE = gerar_chave_nfse()
XML_NFSE = (f'<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse"><infNFSe Id="NFS{CHAVE}">'
            "<nNFSe>1</nNFSe></infNFSe></NFSe>").encode()
ID_DPS = "DPS" + "1" * 42


def test_campo_tolerante_a_caixa():
    assert campo({"ChaveAcesso": "x"}, "chaveAcesso") == "x"
    assert campo({"a": 1}, "b", "A") == 1
    assert campo({"a": 1}, "b", padrao=9) == 9
    assert campo(["nao-dict"], "a", padrao="p") == "p"


@pytest.mark.parametrize("estilo", ["camel", "pascal"])
def test_emissao_com_sucesso(estilo):
    retorno = interpretar_emissao(resposta_emissao(XML_NFSE, CHAVE, estilo=estilo), ID_DPS)
    assert retorno.chave == CHAVE and retorno.xml_nfse == XML_NFSE
    assert retorno.status_http == 201 and retorno.id_dps == ID_DPS
    assert retorno.tipo_ambiente == 2 and retorno.versao_aplicativo == "SefinNacional_1.6.0"
    assert retorno.data_processamento.startswith("2026-10-29") and retorno.alertas == []


def test_emissao_com_alertas_e_chave_deduzida_do_xml():
    corpo = {"nfseXmlGZipB64": comprimir_b64(XML_NFSE),
             "alertas": [{"codigo": "A001", "descricao": "Atenção"}]}
    retorno = interpretar_emissao(RespostaHttp(201, json.dumps(corpo).encode()))
    assert retorno.chave == CHAVE
    assert [(a.codigo, a.descricao) for a in retorno.alertas] == [("A001", "Atenção")]
    assert retorno.tipo_ambiente is None


@pytest.mark.parametrize("estilo", ["camel", "pascal"])
def test_rejeicao_4xx_com_erros(estilo):
    with pytest.raises(RejeicaoNfseErro) as capturado:
        interpretar_emissao(resposta_erro("E0712", "totTrib incompatível", estilo=estilo,
                                          complemento="infDPS/valores"), ID_DPS)
    erro = capturado.value
    assert erro.codigos == ["E0712"] and erro.status_http == 400 and erro.id_dps == ID_DPS
    assert erro.erros[0].descricao == "totTrib incompatível"
    assert erro.erros[0].complemento == "infDPS/valores"


def test_rejeicao_sem_json_usa_codigo_http():
    with pytest.raises(RejeicaoNfseErro) as capturado:
        interpretar_emissao(RespostaHttp(403, b"<html>Forbidden</html>"), ID_DPS)
    assert capturado.value.codigos == ["HTTP403"]
    assert "Forbidden" in capturado.value.erros[0].descricao


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_status_incerto_levanta_emissao_incerta(status):
    with pytest.raises(EmissaoIncertaErro) as capturado:
        interpretar_emissao(RespostaHttp(status, b"{}"), ID_DPS)
    assert capturado.value.id_dps == ID_DPS


@pytest.mark.parametrize("corpo", [b"", b"[]", b"{}", b'{"chaveAcesso": "x"}', b"nao json"])
def test_sucesso_sem_xml_ou_malformado(corpo):
    with pytest.raises(RespostaInvalidaErro):
        interpretar_emissao(RespostaHttp(201, corpo), ID_DPS)


def test_consulta_nfse():
    retorno = interpretar_consulta_nfse(resposta_emissao(XML_NFSE, CHAVE, status=200))
    assert retorno.chave == CHAVE and retorno.id_dps is None
    with pytest.raises(RejeicaoNfseErro):
        interpretar_consulta_nfse(RespostaHttp(404, b'{"erros":[{"codigo":"E0900"}]}'))


def test_extrair_erros_formatos():
    assert extrair_erros({"erro": {"Codigo": "E1", "Descricao": "d"}})[0].codigo == "E1"
    assert [e.codigo for e in extrair_erros({"Erros": [{"codigo": "A"}, {"codigo": "B"}]})] == [
        "A", "B"]
    assert extrair_erros({"erros": ["texto solto"]})[0].descricao == "texto solto"
    assert extrair_erros({"x": 1}) == [] and extrair_erros(None) == []
    assert str(extrair_erros({"erros": [{"codigo": "E1", "descricao": "d",
                                         "complemento": "c"}]})[0]) == "E1: d (c)"


def test_ler_chave_do_xml():
    assert ler_chave_do_xml(XML_NFSE) == CHAVE
    assert ler_chave_do_xml(b"<outro/>") is None
    assert ler_chave_do_xml(b"nao xml") is None
