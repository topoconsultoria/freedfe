"""Leitura da NFS-e Nacional e de seus eventos: extratores, repositório e fachada."""

import datetime as dt
import os
from decimal import Decimal

import pytest

from freedfe import (
    Ambiente,
    Direcao,
    FiltroConsulta,
    FormatoSaida,
    FreeDFe,
    ModeloDocumento,
    SituacaoDocumento,
    ValidacaoErro,
)
from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.documentos.leitor import LeitorDocumentoFiscal
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento
from freedfe.nfse.leiaute import VersaoLeiaute
from freedfe.nfse.modelo import Prestador
from freedfe.nfse.validador_xsd import ValidadorXsd
from freedfe.repositorio.arquivos import RepositorioArquivos

from fabrica_nfse import (
    CNPJ_PRESTADOR,
    CNPJ_TOMADOR,
    CPF_TOMADOR,
    MUNICIPIO,
    dps_completa,
    dps_minima,
    evento_nfse_xml,
    nfse_xml,
    tomador_pj,
)
from fabrica_xml import CNPJ_FORNECEDOR, gerar_chave, proc_evento_nfe

leitor = LeitorDocumentoFiscal()


def recebido(xml, nsu=None):
    return DocumentoRecebido(xml=xml, nsu=f"{nsu:015d}" if nsu is not None else None)


# ---------------------------------------------------------------------------
# Extratores
# ---------------------------------------------------------------------------

def test_nfse_minima():
    chave, xml = nfse_xml(dps_minima(tomador=tomador_pj()), numero_nfse=42)
    resumo = leitor.ler(xml)
    assert isinstance(resumo, ResumoDocumento)
    assert resumo.chave == chave and len(chave) == 50
    assert resumo.modelo is ModeloDocumento.NFSE and resumo.tipo_xml == "NFSe"
    assert resumo.xml_completo and resumo.situacao is SituacaoDocumento.AUTORIZADO
    assert resumo.numero == "42" and resumo.serie == "900"
    assert resumo.data_emissao == dt.datetime(2026, 10, 29, 10, 0,
                                              tzinfo=dt.timezone(dt.timedelta(hours=-3)))
    assert resumo.emitente_documento == CNPJ_PRESTADOR
    assert resumo.emitente_nome == "EMPRESA PRESTADORA DE TESTE LTDA"
    assert resumo.destinatario_documento == CNPJ_TOMADOR
    assert resumo.destinatario_nome == "EMPRESA TOMADORA DE TESTE LTDA"
    assert resumo.valor_total == Decimal("1000.00") and resumo.protocolo is None


def test_nfse_sem_tomador_e_participantes_da_dps_completa():
    _, xml = nfse_xml(dps_minima())
    resumo = leitor.ler(xml)
    assert resumo.destinatario_documento is None and resumo.participantes() == set()
    completa = leitor.ler(nfse_xml(dps_completa())[1])
    assert completa.valor_total == Decimal("1500.50")
    assert completa.destinatario_documento == CNPJ_TOMADOR
    assert completa.demais_participantes == [CPF_TOMADOR, CNPJ_TOMADOR]


def test_nfse_sem_id_valido_nao_e_reconhecida():
    _, xml = nfse_xml()
    assert leitor.ler(xml.replace(b'Id="NFS', b'Id="XXX')) is None


def test_evento_de_cancelamento():
    chave, _ = nfse_xml()
    evento = leitor.ler(evento_nfse_xml(chave))
    assert isinstance(evento, EventoDocumento)
    assert evento.chave == chave and evento.modelo is ModeloDocumento.NFSE
    assert evento.tipo_evento == "101101" and evento.descricao == "Cancelamento de NFS-e"
    assert evento.data_evento == dt.datetime(2026, 10, 29, 11, 0,
                                             tzinfo=dt.timezone(dt.timedelta(hours=-3)))
    assert evento.protocolo == f"EVT{chave}101101001"


def test_evento_de_nfse_nao_colide_com_eventos_da_nfe():
    chave_nfe = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    evento_nfe = leitor.ler(proc_evento_nfe(chave_nfe))
    assert evento_nfe.modelo is ModeloDocumento.NFE and evento_nfe.chave == chave_nfe
    chave, _ = nfse_xml()
    assert leitor.ler(evento_nfse_xml(chave)).modelo is ModeloDocumento.NFSE


def test_serializacao_ida_e_volta():
    _, xml = nfse_xml(dps_completa())
    resumo = leitor.ler(xml)
    assert ResumoDocumento.de_dict(resumo.para_dict()) == resumo


# ---------------------------------------------------------------------------
# Repositório
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    return RepositorioArquivos(tmp_path, CNPJ_PRESTADOR)


def test_armazenar_nfse_preserva_o_xml_e_organiza_pastas(repo, tmp_path):
    chave, xml = nfse_xml(dps_minima(tomador=tomador_pj()))
    resultado = repo.armazenar([recebido(xml, 7)], ModeloDocumento.NFSE)
    assert resultado.documentos == 1
    resumo = repo.obter(ModeloDocumento.NFSE, chave)
    assert resumo.caminho_xml == f"nfse/2026-10/{chave}-NFSe.xml"
    assert repo.ler_xml(resumo) == xml
    assert (tmp_path / "nfse" / "indice.json").exists()
    assert repo.lacunas_nsu(ModeloDocumento.NFSE) == []


def test_cancelamento_e_substituicao_alteram_a_situacao(repo):
    cancelada, xml_cancelada = nfse_xml(numero_nfse=1)
    substituida, xml_substituida = nfse_xml(numero_nfse=2)
    ativa, xml_ativa = nfse_xml(numero_nfse=3)
    repo.armazenar([recebido(xml_cancelada), recebido(xml_substituida), recebido(xml_ativa)])
    repo.armazenar([recebido(evento_nfse_xml(cancelada, "101101")),
                    recebido(evento_nfse_xml(substituida, "105102"))])
    situacoes = {r.chave: r.situacao for r in repo.listar(FiltroConsulta(modelos=["nfse"]))}
    assert situacoes == {cancelada: SituacaoDocumento.CANCELADO,
                         substituida: SituacaoDocumento.SUBSTITUIDO,
                         ativa: SituacaoDocumento.AUTORIZADO}


@pytest.mark.parametrize("tipo", ["101101", "105104", "305101"])
def test_eventos_de_cancelamento_da_nfse(repo, tipo):
    chave, xml = nfse_xml()
    repo.armazenar([recebido(xml), recebido(evento_nfse_xml(chave, tipo))])
    assert repo.obter(ModeloDocumento.NFSE, chave) is not None
    assert repo.listar()[0].situacao is SituacaoDocumento.CANCELADO


def test_cancelamento_tem_precedencia_sobre_substituicao(repo):
    chave, xml = nfse_xml()
    repo.armazenar([recebido(xml), recebido(evento_nfse_xml(chave, "105102")),
                    recebido(evento_nfse_xml(chave, "101101"))])
    assert repo.listar()[0].situacao is SituacaoDocumento.CANCELADO


def test_evento_repetido_nao_duplica(repo, tmp_path):
    chave, xml = nfse_xml()
    repo.armazenar([recebido(xml)])
    evento = evento_nfse_xml(chave)
    repo.armazenar([recebido(evento)])
    repo.armazenar([recebido(evento, 9)])  # mesmo evento, agora vindo do ADN
    assert len(repo.eventos(ModeloDocumento.NFSE, chave)) == 1
    arquivos = list((tmp_path / "nfse" / "eventos").rglob("*.xml"))
    assert len(arquivos) == 1 and "EVT" in arquivos[0].name


def test_lacunas_de_nsu_da_sequencia_nfse(repo):
    _, a = nfse_xml(numero_nfse=1)
    _, b = nfse_xml(numero_nfse=2)
    repo.armazenar([recebido(a, 1), recebido(b, 4)], ModeloDocumento.NFSE)
    assert repo.lacunas_nsu(ModeloDocumento.NFSE) == ["000000000000002", "000000000000003"]


def test_filtro_por_direcao(repo):
    emitida, xml_emitida = nfse_xml(dps_minima(tomador=tomador_pj()), numero_nfse=1)
    tomada, xml_tomada = nfse_xml(
        dps_minima(prestador=Prestador(cnpj=CNPJ_FORNECEDOR, opcao_simples=1),
                   tomador=tomador_pj(cnpj=CNPJ_PRESTADOR)), numero_nfse=2)
    repo.armazenar([recebido(xml_emitida), recebido(xml_tomada)])
    emitidos = repo.listar(FiltroConsulta(cnpj_cpf=CNPJ_PRESTADOR, direcao=Direcao.EMITIDOS))
    recebidos = repo.listar(FiltroConsulta(cnpj_cpf=CNPJ_PRESTADOR, direcao=Direcao.RECEBIDOS))
    assert [r.chave for r in emitidos] == [emitida]
    assert [r.chave for r in recebidos] == [tomada]
    assert recebidos[0].emitente_documento == CNPJ_FORNECEDOR


# ---------------------------------------------------------------------------
# Fachada
# ---------------------------------------------------------------------------

@pytest.fixture
def dfe(tmp_path):
    instancia = FreeDFe(cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO, pasta=tmp_path)
    xmls = tmp_path / "entrada"
    xmls.mkdir()
    chave, xml = nfse_xml(dps_minima(tomador=tomador_pj()))
    (xmls / "nota.xml").write_bytes(xml)
    instancia.importar_xmls(xmls)
    instancia.chave_teste = chave
    return instancia


def test_importar_e_consultar_nfse(dfe):
    notas = dfe.consultar(FiltroConsulta(modelos=["nfse"]))
    assert [n.chave for n in notas] == [dfe.chave_teste]
    assert dfe.consultar(FiltroConsulta(modelos=["nfe"])) == []
    assert dfe.consultar(FiltroConsulta(direcao="emitidos"))[0].modelo is ModeloDocumento.NFSE


def test_exportar_csv_com_chave_de_50_posicoes(dfe):
    csv = dfe.consultar(FiltroConsulta(modelos=["nfse"]), formato=FormatoSaida.CSV)
    assert dfe.chave_teste in csv and "nfse" in csv


def test_baixar_xml_da_nfse(dfe, tmp_path):
    [arquivo] = dfe.baixar_xmls(FiltroConsulta(modelos=["nfse"]), tmp_path / "saida")
    assert arquivo.name == f"{dfe.chave_teste}.xml"


def test_consultar_nsu_pontual_nao_existe_para_nfse(dfe):
    with pytest.raises(ValidacaoErro, match="dfe.nfse"):
        dfe.consultar_nsu("nfse", 1)


def test_sincronizacao_padrao_exclui_nfse():
    assert ModeloDocumento.NFSE not in FreeDFe._modelos(None)
    assert MUNICIPIO  # fábrica carregada


@pytest.mark.parametrize("variavel, leiaute", [
    ("FREEDFE_XSD_NFSE_NT004", VersaoLeiaute.NT004_V2),
    ("FREEDFE_XSD_NFSE_NT009", VersaoLeiaute.NT009_V101)])
def test_xml_sinteticos_validam_no_schema_oficial(variavel, leiaute):
    """Garante que as fábricas de teste espelham o leiaute real da NFS-e e do evento."""
    pasta = os.environ.get(variavel)
    if not pasta:
        pytest.skip(f"defina {variavel} com a pasta dos XSD oficiais")
    chave, xml = nfse_xml(dps_completa(), leiaute=leiaute)
    assert ValidadorXsd(pasta, "NFSe_v1.01.xsd").mensagens(xml) == []
    assert ValidadorXsd(pasta, "evento_v1.01.xsd").mensagens(evento_nfse_xml(chave)) == []
