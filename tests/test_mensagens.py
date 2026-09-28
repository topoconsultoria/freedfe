import pytest

from freedfe.distribuicao.mensagens import MontadorDistribuicao
from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.servicos import configuracao_distribuicao
from freedfe.tipos import Ambiente, ModeloDocumento

from fabrica_xml import CNPJ_INTERESSADO, gerar_chave

INTERESSADO = Interessado(cnpj=CNPJ_INTERESSADO, uf="29")


def montador(modelo):
    return MontadorDistribuicao(configuracao_distribuicao(modelo), Ambiente.HOMOLOGACAO)


def test_dist_nsu_nfe_exato():
    xml = montador(ModeloDocumento.NFE).distribuicao_nsu(INTERESSADO, 5)
    assert xml == ('<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">'
                   "<tpAmb>2</tpAmb><cUFAutor>29</cUFAutor><CNPJ>11222333000181</CNPJ>"
                   "<distNSU><ultNSU>000000000000005</ultNSU></distNSU></distDFeInt>")


def test_consulta_chave_somente_nfe():
    chave = gerar_chave("55", CNPJ_INTERESSADO, 1)
    assert f"<chNFe>{chave}</chNFe>" in montador(ModeloDocumento.NFE).consulta_chave(
        INTERESSADO, chave)
    with pytest.raises(ValidacaoErro):
        montador(ModeloDocumento.CTE).consulta_chave(INTERESSADO, chave)


def test_cte_exige_uf():
    with pytest.raises(ValidacaoErro):
        montador(ModeloDocumento.CTE).distribuicao_nsu(Interessado(cnpj=CNPJ_INTERESSADO), 0)


def test_mdfe_sem_cuf_autor_e_com_header():
    m = montador(ModeloDocumento.MDFE)
    dados = m.distribuicao_nsu(INTERESSADO, 0)
    assert "cUFAutor" not in dados
    envelope = m.envelope(dados, "29").decode()
    assert "<mdfeCabecMsg" in envelope and "<cUF>29</cUF>" in envelope
    assert "<mdfeDadosMsg" in envelope and "mdfeDistDFeInteresse" not in envelope


def test_envelope_nfe_sem_header():
    m = montador(ModeloDocumento.NFE)
    envelope = m.envelope(m.distribuicao_nsu(INTERESSADO, 0)).decode()
    assert "soap12:Header" not in envelope
    assert "<nfeDistDFeInteresse" in envelope and "<nfeDadosMsg>" in envelope
