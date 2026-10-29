import datetime as dt

import pytest

from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.servicos import configuracao_nfse
from freedfe.tipos import Ambiente


@pytest.mark.parametrize("ambiente, data, esperado", [
    (Ambiente.PRODUCAO, dt.date(2026, 10, 29), VersaoLeiaute.NT004_V2),
    (Ambiente.PRODUCAO, dt.date(2026, 11, 30), VersaoLeiaute.NT004_V2),
    (Ambiente.PRODUCAO, dt.date(2026, 12, 1), VersaoLeiaute.NT009_V101),
    (Ambiente.PRODUCAO, dt.date(2027, 3, 1), VersaoLeiaute.NT009_V101),
    (Ambiente.HOMOLOGACAO, dt.date(2026, 10, 29), VersaoLeiaute.NT009_V101),
    (Ambiente.HOMOLOGACAO, dt.date(2026, 1, 1), VersaoLeiaute.NT009_V101),
])
def test_selecao_por_ambiente_e_data(ambiente, data, esperado):
    assert VersaoLeiaute.selecionar(ambiente, data) is esperado


def test_selecao_sem_data_usa_hoje():
    assert VersaoLeiaute.selecionar(Ambiente.PRODUCAO) in set(VersaoLeiaute)


def test_atributos_dos_leiautes():
    for leiaute in VersaoLeiaute:
        assert leiaute.namespace == NAMESPACE_NFSE
        assert leiaute.tamanho_id_evento == 59
        assert leiaute.versao_dps == "1.01"
    assert VersaoLeiaute.NT004_V2.descricao == "NT 004 v2.0"


def test_configuracao_nfse_por_ambiente():
    producao = configuracao_nfse(Ambiente.PRODUCAO)
    assert producao.base_sefin == "https://sefin.nfse.gov.br/SefinNacional"
    assert producao.url_sefin("/nfse") == "https://sefin.nfse.gov.br/SefinNacional/nfse"
    assert producao.url_adn("/DFe/0") == "https://adn.nfse.gov.br/contribuintes/DFe/0"
    restrita = configuracao_nfse(Ambiente.HOMOLOGACAO)
    assert restrita.base_sefin == "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"
    assert restrita.url_parametrizacao("/2927408/convenio").startswith(
        "https://adn.producaorestrita.nfse.gov.br/parametrizacao/")


def test_prefixo_api_configuravel_e_imutabilidade():
    com_prefixo = configuracao_nfse(Ambiente.HOMOLOGACAO, prefixo_api="/API")
    assert com_prefixo.base_sefin == "https://sefin.producaorestrita.nfse.gov.br/API/SefinNacional"
    assert configuracao_nfse(Ambiente.HOMOLOGACAO).prefixo_api == ""
    with pytest.raises(AttributeError):
        com_prefixo.prefixo_api = "/x"  # type: ignore[misc]


def test_url_consulta_publica_leva_a_chave():
    url = configuracao_nfse(Ambiente.PRODUCAO).url_consulta_publica("1" * 50)
    assert url.startswith("https://www.nfse.gov.br/ConsultaPublica") and "1" * 50 in url
