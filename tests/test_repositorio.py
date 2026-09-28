import datetime as dt

import pytest

from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.excecoes import ValidacaoErro
from freedfe.repositorio.arquivos import RepositorioArquivos
from freedfe.repositorio.filtro import Direcao, FiltroConsulta
from freedfe.repositorio.importador import ImportadorXml
from freedfe.tipos import ModeloDocumento, SituacaoDocumento

from fabrica_xml import (
    CNPJ_FORNECEDOR,
    CNPJ_INTERESSADO,
    CNPJ_TRANSPORTADORA,
    cte_proc,
    gerar_chave,
    nfe_proc,
    proc_evento_nfe,
    res_nfe,
)


@pytest.fixture
def repo(tmp_path):
    return RepositorioArquivos(tmp_path, CNPJ_INTERESSADO)


def recebido(xml, nsu=None):
    return DocumentoRecebido(xml=xml, nsu=f"{nsu:015d}" if nsu is not None else None)


def test_xml_completo_substitui_resumo(repo):
    chave = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    repo.armazenar([recebido(res_nfe(chave), 1)], ModeloDocumento.NFE)
    assert not repo.obter(ModeloDocumento.NFE, chave).xml_completo
    repo.armazenar([recebido(nfe_proc(chave), 2)], ModeloDocumento.NFE)
    resumo = repo.obter(ModeloDocumento.NFE, chave)
    assert resumo.xml_completo and resumo.caminho_xml.startswith("nfe/2026-07/")
    assert repo.ler_xml(resumo) == nfe_proc(chave)  # XML preservado sem alteração


def test_resumo_nao_rebaixa_xml_completo(repo):
    chave = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    repo.armazenar([recebido(nfe_proc(chave)), recebido(res_nfe(chave))])
    assert repo.obter(ModeloDocumento.NFE, chave).xml_completo


def test_cancelamento_altera_situacao(repo):
    chave = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    resultado = repo.armazenar([recebido(proc_evento_nfe(chave)), recebido(nfe_proc(chave))])
    assert resultado.eventos == 1 and resultado.documentos == 1
    [documento] = repo.listar()
    assert documento.situacao is SituacaoDocumento.CANCELADO


def test_nao_reconhecido_e_preservado(repo, tmp_path):
    resultado = repo.armazenar([DocumentoRecebido(b"<novo/>", "000000000000009", "novo_v1.xsd")])
    assert resultado.nao_reconhecidos == 1
    assert (tmp_path / "nao_reconhecidos" / "000000000000009_novo_v1.xsd.xml").exists()


def test_lacunas_nsu(repo):
    docs = [recebido(res_nfe(gerar_chave("55", CNPJ_FORNECEDOR, n)), n) for n in (1, 2, 5)]
    repo.armazenar(docs, ModeloDocumento.NFE)
    assert repo.lacunas_nsu(ModeloDocumento.NFE) == ["000000000000003", "000000000000004"]


def test_indice_persistido(tmp_path):
    chave = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    RepositorioArquivos(tmp_path, CNPJ_INTERESSADO).armazenar([recebido(nfe_proc(chave))])
    assert RepositorioArquivos(tmp_path).obter(ModeloDocumento.NFE, chave) is not None


def test_filtro_periodo_modelo_e_direcao(repo):
    emitida = gerar_chave("55", CNPJ_INTERESSADO, 9)
    recebida_jul = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    recebida_ago = gerar_chave("55", CNPJ_FORNECEDOR, 2)
    frete = gerar_chave("57", CNPJ_TRANSPORTADORA, 1)
    repo.armazenar([
        recebido(nfe_proc(emitida, emitente=CNPJ_INTERESSADO, destinatario=CNPJ_FORNECEDOR,
                          dh_emi="2026-07-20T09:00:00-03:00")),
        recebido(nfe_proc(recebida_jul)),
        recebido(nfe_proc(recebida_ago, dh_emi="2026-08-10T09:00:00-03:00")),
        recebido(cte_proc(frete)),
    ])

    def chaves(**criterios):
        return [r.chave for r in repo.listar(FiltroConsulta(**criterios))]

    assert chaves(data_inicial=dt.date(2026, 7, 1), data_final=dt.date(2026, 7, 31)) == [
        recebida_jul, emitida]
    assert chaves(modelos=["cte"]) == [frete]
    assert chaves(cnpj_cpf=CNPJ_INTERESSADO, direcao=Direcao.EMITIDOS) == [emitida]
    assert set(chaves(cnpj_cpf=CNPJ_INTERESSADO, direcao="recebidos")) == {
        recebida_jul, recebida_ago, frete}


def test_filtro_periodo_invertido():
    with pytest.raises(ValidacaoErro):
        FiltroConsulta(data_inicial=dt.date(2026, 8, 1), data_final=dt.date(2026, 7, 1))


def test_importador_de_pasta(repo, tmp_path):
    origem = tmp_path / "erp" / "2026"
    origem.mkdir(parents=True)
    for n in (1, 2):
        chave = gerar_chave("55", CNPJ_INTERESSADO, n)
        (origem / f"{chave}.xml").write_bytes(
            nfe_proc(chave, emitente=CNPJ_INTERESSADO, destinatario=CNPJ_FORNECEDOR))
    resultado = ImportadorXml(repo, tamanho_lote=1).importar_pasta(tmp_path / "erp")
    assert resultado.documentos == 2
    assert len(repo.listar(FiltroConsulta(cnpj_cpf=CNPJ_INTERESSADO, direcao="emitidos"))) == 2
