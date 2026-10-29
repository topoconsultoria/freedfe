"""Cancelamento (evento 101101) e consulta de eventos da NFS-e."""

import datetime as dt
import json
import os

import pytest
from lxml import etree

from freedfe import Ambiente, FiltroConsulta, FreeDFe, SituacaoDocumento
from freedfe.assinatura import PERFIL_NFSE, AssinadorXml, verificar_assinatura
from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.excecoes import (
    ComunicacaoErro,
    RejeicaoNfseErro,
    SincronizacaoEmAndamentoErro,
    ValidacaoErro,
)
from freedfe.interessado import Interessado
from freedfe.nfse.envelope import descomprimir_b64
from freedfe.nfse.eventos import MontadorPedidoEvento, MotivoCancelamento, TipoEventoNfse
from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.nfse.servico import ConfiguracaoNfseLocal
from freedfe.nfse.validador_xsd import ValidadorXsd
from freedfe.transporte import RespostaHttp

from conftest import RelogioFalso, TransporteRestFalso
from fabrica_nfse import (
    CNPJ_PRESTADOR,
    CPF_TOMADOR,
    DATA_EMISSAO,
    dps_minima,
    evento_nfse_xml,
    gerar_chave_nfse,
    nfse_xml,
    resposta_erro,
    resposta_evento,
    resposta_lista_eventos,
)

NS = {"n": NAMESPACE_NFSE}
BASE = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"
JUSTIFICATIVA = "Servico nao prestado conforme contrato"


@pytest.fixture
def relogio():
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


@pytest.fixture
def rest():
    return TransporteRestFalso()


@pytest.fixture
def dfe(tmp_path, certificado, relogio, rest):
    instancia = FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                        pasta=tmp_path, transporte_rest=rest, relogio=relogio,
                        config_nfse=ConfiguracaoNfseLocal(esperas_consulta=()))
    chave, xml = nfse_xml(dps_minima())
    instancia.repositorio.armazenar([DocumentoRecebido(xml=xml)])
    instancia.chave_teste = chave
    return instancia


def pedido_enviado(rest) -> etree._Element:
    corpo = rest.chamadas("POST")[0][2]
    return etree.fromstring(descomprimir_b64(json.loads(corpo)["pedidoRegistroEventoXmlGZipB64"]))


def situacao(dfe) -> SituacaoDocumento:
    return dfe.consultar(FiltroConsulta(modelos=["nfse"]))[0].situacao


# ---------------------------------------------------------------------------
# Montagem do pedido
# ---------------------------------------------------------------------------

def montador(relogio, autor=None, leiaute=VersaoLeiaute.NT009_V101):
    return MontadorPedidoEvento(Ambiente.HOMOLOGACAO, autor or Interessado(cnpj=CNPJ_PRESTADOR),
                                leiaute, relogio)


def test_pedido_de_cancelamento_segue_o_leiaute(relogio):
    chave = gerar_chave_nfse()
    raiz = montador(relogio).cancelamento(chave, MotivoCancelamento.SERVICO_NAO_PRESTADO,
                                          JUSTIFICATIVA)
    assert raiz.get("versao") == "1.01" and [etree.QName(f).localname for f in raiz] == [
        "infPedReg"]
    inf = raiz[0]
    assert inf.get("Id") == f"PRE{chave}101101" and len(inf.get("Id")) == 59
    assert [etree.QName(f).localname for f in inf] == [
        "tpAmb", "verAplic", "dhEvento", "CNPJAutor", "chNFSe", "e101101"]
    assert inf.findtext("n:tpAmb", namespaces=NS) == "2"
    assert inf.findtext("n:CNPJAutor", namespaces=NS) == CNPJ_PRESTADOR
    evento = inf.find("n:e101101", NS)
    assert [(etree.QName(f).localname, f.text) for f in evento] == [
        ("xDesc", "Cancelamento de NFS-e"), ("cMotivo", "2"), ("xMotivo", JUSTIFICATIVA)]


def test_autor_pessoa_fisica_e_dhevento_com_folga(relogio):
    raiz = montador(relogio, Interessado(cpf=CPF_TOMADOR)).cancelamento(
        gerar_chave_nfse(), 1, JUSTIFICATIVA)
    assert raiz[0].findtext("n:CPFAutor", namespaces=NS) == CPF_TOMADOR
    esperado = relogio().astimezone(dt.timezone(dt.timedelta(hours=-3))) - dt.timedelta(seconds=60)
    assert raiz[0].findtext("n:dhEvento", namespaces=NS) == (
        esperado.replace(microsecond=0).isoformat())


@pytest.mark.parametrize("argumentos, mensagem", [
    (("123", 1, JUSTIFICATIVA), "inválida"),
    ((gerar_chave_nfse(), 3, JUSTIFICATIVA), "Motivo"),
    ((gerar_chave_nfse(), "x", JUSTIFICATIVA), "Motivo"),
    ((gerar_chave_nfse(), 1, "curta"), "15 a 255"),
    ((gerar_chave_nfse(), 1, "x" * 256), "15 a 255"),
    ((gerar_chave_nfse(), 1, "   " + "a" * 10 + "   "), "15 a 255"),
])
def test_validacoes_do_pedido(relogio, argumentos, mensagem):
    with pytest.raises(ValidacaoErro, match=mensagem):
        montador(relogio).cancelamento(*argumentos)


def test_justificativa_e_sanitizada(relogio):
    raiz = montador(relogio).cancelamento(
        gerar_chave_nfse(), 9, "Cancelamento “motivado” – erro de digitação")
    assert raiz[0].findtext("n:e101101/n:xMotivo", namespaces=NS) == (
        'Cancelamento "motivado" - erro de digitação')


def test_tipos_de_evento():
    assert TipoEventoNfse.CANCELAMENTO.cancela and not TipoEventoNfse.CANCELAMENTO.substitui
    assert TipoEventoNfse.CANCELAMENTO_DEFERIDO.cancela
    assert TipoEventoNfse.CANCELAMENTO_POR_OFICIO.cancela
    assert TipoEventoNfse.CANCELAMENTO_POR_SUBSTITUICAO.substitui
    assert not TipoEventoNfse.CONFIRMACAO_TOMADOR.cancela


@pytest.mark.parametrize("variavel, leiaute", [
    ("FREEDFE_XSD_NFSE_NT004", VersaoLeiaute.NT004_V2),
    ("FREEDFE_XSD_NFSE_NT009", VersaoLeiaute.NT009_V101)])
def test_pedido_assinado_valida_no_schema_oficial(relogio, certificado, variavel, leiaute):
    pasta = os.environ.get(variavel)
    if not pasta:
        pytest.skip(f"defina {variavel} com a pasta dos XSD oficiais")
    validador = ValidadorXsd(pasta, "pedRegEvento_v1.01.xsd")
    raiz = montador(relogio, leiaute=leiaute).cancelamento(gerar_chave_nfse(), 1, JUSTIFICATIVA)
    validador.validar(raiz)
    AssinadorXml(certificado, PERFIL_NFSE).assinar(raiz)
    reparsed = etree.fromstring(etree.tostring(raiz))
    validador.validar(reparsed)
    assert verificar_assinatura(reparsed, PERFIL_NFSE)


# ---------------------------------------------------------------------------
# ServicoNfse.cancelar
# ---------------------------------------------------------------------------

def test_cancelamento_feliz(dfe, rest, tmp_path):
    rest.respostas = [resposta_evento(dfe.chave_teste)]
    assert situacao(dfe) is SituacaoDocumento.AUTORIZADO
    retorno = dfe.nfse.cancelar(dfe.chave_teste, 2, JUSTIFICATIVA)
    assert retorno.tipo_evento == "101101" and retorno.chave_nfse == dfe.chave_teste
    assert retorno.xml_evento is not None and retorno.versao_aplicativo == "SefinNacional_1.6.0"
    [(metodo, url, corpo, _)] = rest.enviados
    assert (metodo, url) == ("POST", f"{BASE}/nfse/{dfe.chave_teste}/eventos")
    assert list(json.loads(corpo)) == ["pedidoRegistroEventoXmlGZipB64"]
    enviado = pedido_enviado(rest)
    assert verificar_assinatura(enviado, PERFIL_NFSE)
    assert [etree.QName(f).localname for f in enviado] == ["infPedReg", "Signature"]
    assert situacao(dfe) is SituacaoDocumento.CANCELADO
    pasta = tmp_path / "homologacao" / CNPJ_PRESTADOR / "nfse" / "eventos_enviados"
    nomes = sorted(p.name.split("-", 1)[1] for p in pasta.rglob("*") if p.is_file())
    assert nomes == ["101101-pedido.xml", "101101-resposta.json"]
    # Sem a declaração UTF-8 a Sefin rejeita com E1229; a evidência é o que foi enviado.
    bruto = descomprimir_b64(json.loads(corpo)["pedidoRegistroEventoXmlGZipB64"])
    assert bruto.startswith(b'<?xml version="1.0" encoding="UTF-8"?><pedRegEvento ')
    assert next(pasta.rglob("*-pedido.xml")).read_bytes() == bruto


def test_resposta_sem_xml_busca_o_evento_na_lista(dfe, rest):
    evento = evento_nfse_xml(dfe.chave_teste)
    rest.respostas = [resposta_evento(dfe.chave_teste, com_xml=False),
                      resposta_lista_eventos([evento])]
    retorno = dfe.nfse.cancelar(dfe.chave_teste, 1, JUSTIFICATIVA)
    assert retorno.xml_evento is None and situacao(dfe) is SituacaoDocumento.CANCELADO
    assert rest.enviados[-1][:2] == ("GET", f"{BASE}/nfse/{dfe.chave_teste}/eventos")


@pytest.mark.parametrize("codigo", ["E0824", "E0840"])
def test_rejeicao_do_cancelamento(dfe, rest, codigo):
    rest.respostas = [resposta_erro(codigo, "regra municipal")]
    if codigo == "E0840":  # a nota já estava cancelada na Sefin: sincroniza os eventos
        rest.respostas.append(resposta_lista_eventos([evento_nfse_xml(dfe.chave_teste)]))
    with pytest.raises(RejeicaoNfseErro) as capturado:
        dfe.nfse.cancelar(dfe.chave_teste, 1, JUSTIFICATIVA)
    assert capturado.value.codigos == [codigo]
    esperado = (SituacaoDocumento.CANCELADO if codigo == "E0840"
                else SituacaoDocumento.AUTORIZADO)
    assert situacao(dfe) is esperado


def test_nota_ja_cancelada_localmente_nao_chama_a_sefin(dfe, rest):
    dfe.repositorio.armazenar([DocumentoRecebido(xml=evento_nfse_xml(dfe.chave_teste))])
    with pytest.raises(ValidacaoErro, match="já consta como cancelada"):
        dfe.nfse.cancelar(dfe.chave_teste, 1, JUSTIFICATIVA)
    assert rest.enviados == []


def test_nota_substituida_nao_pode_ser_cancelada(dfe, rest):
    dfe.repositorio.armazenar([DocumentoRecebido(xml=evento_nfse_xml(dfe.chave_teste, "105102"))])
    with pytest.raises(ValidacaoErro, match="substituída"):
        dfe.nfse.cancelar(dfe.chave_teste, 1, JUSTIFICATIVA)


@pytest.mark.parametrize("falha", [ComunicacaoErro("timeout"), RespostaHttp(503, b"")])
def test_falha_com_cancelamento_registrado_converge_sem_reenviar(dfe, rest, falha):
    rest.respostas = [falha, resposta_lista_eventos([evento_nfse_xml(dfe.chave_teste)])]
    retorno = dfe.nfse.cancelar(dfe.chave_teste, 1, JUSTIFICATIVA)
    assert retorno.status_http == 200 and situacao(dfe) is SituacaoDocumento.CANCELADO
    assert len(rest.chamadas("POST")) == 1


def test_falha_sem_evento_registrado_levanta_e_orienta(dfe, rest):
    rest.respostas = [ComunicacaoErro("timeout"), RespostaHttp(404, b"{}")]
    with pytest.raises(ComunicacaoErro, match="listar_eventos"):
        dfe.nfse.cancelar(dfe.chave_teste, 1, JUSTIFICATIVA)
    assert situacao(dfe) is SituacaoDocumento.AUTORIZADO


def test_validacao_local_nao_envia_nada(dfe, rest):
    with pytest.raises(ValidacaoErro, match="15 a 255"):
        dfe.nfse.cancelar(dfe.chave_teste, 1, "curta")
    assert rest.enviados == []


def test_trava_impede_cancelamento_durante_emissao(dfe, rest, tmp_path):
    trava = tmp_path / "homologacao" / CNPJ_PRESTADOR / "_estado" / "emissao_nfse.lock"
    trava.parent.mkdir(parents=True)
    trava.write_text("pid=1")
    with pytest.raises(SincronizacaoEmAndamentoErro):
        dfe.nfse.cancelar(dfe.chave_teste, 1, JUSTIFICATIVA)
    assert rest.enviados == []


# ---------------------------------------------------------------------------
# Lista de eventos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("duplo", [True, False])
def test_listar_eventos_aceita_base64_duplo(dfe, rest, duplo):
    rest.respostas = [resposta_lista_eventos([evento_nfse_xml(dfe.chave_teste)], duplo)]
    eventos = dfe.nfse.listar_eventos(dfe.chave_teste)
    assert [e.tipo_evento for e in eventos] == ["101101"] and situacao(dfe) is (
        SituacaoDocumento.CANCELADO)


def test_listar_eventos_sem_eventos(dfe, rest):
    rest.respostas = [RespostaHttp(404, b"{}")]
    assert dfe.nfse.listar_eventos(dfe.chave_teste) == []
    rest.respostas = [RespostaHttp(200, b'{"eventos": []}')]
    assert dfe.nfse.listar_eventos(dfe.chave_teste) == []


def test_listar_eventos_nao_duplica(dfe, rest):
    for _ in range(2):
        rest.respostas = [resposta_lista_eventos([evento_nfse_xml(dfe.chave_teste)])]
        eventos = dfe.nfse.listar_eventos(dfe.chave_teste)
    assert len(eventos) == 1
