"""Emissão de NFS-e: fluxo feliz, rejeição, idempotência e reconciliação (sem rede)."""

import datetime as dt
import json

import pytest
from lxml import etree

from freedfe import Ambiente, FiltroConsulta, FreeDFe, SituacaoDocumento
from freedfe.assinatura import PERFIL_NFSE, verificar_assinatura
from freedfe.excecoes import (
    CertificadoErro,
    ComunicacaoErro,
    EmissaoIncertaErro,
    RejeicaoNfseErro,
    RespostaInvalidaErro,
    SincronizacaoEmAndamentoErro,
    ValidacaoErro,
)
from freedfe.nfse.envelope import descomprimir_b64
from freedfe.nfse.estado_emissao import SituacaoEmissao
from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.nfse.montador_dps import MontadorDps
from freedfe.nfse.servico import ConfiguracaoNfseLocal
from freedfe.transporte import RespostaHttp

from conftest import RelogioFalso, TransporteRestFalso
from fabrica_nfse import (
    CNPJ_PRESTADOR,
    DATA_EMISSAO,
    dps_minima,
    ibs_cbs_minimo,
    nfse_xml,
    resposta_emissao,
    resposta_erro,
    servico_com_nbs,
)

NS = {"n": NAMESPACE_NFSE}
BASE = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"


@pytest.fixture
def relogio():
    """Cinco minutos depois da emissão fixa das fábricas."""
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


@pytest.fixture
def rest():
    return TransporteRestFalso()


@pytest.fixture
def dfe(tmp_path, certificado, relogio, rest):
    return FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                   pasta=tmp_path, transporte_rest=rest, relogio=relogio,
                   config_nfse=ConfiguracaoNfseLocal(esperas_consulta=()))


def dps_enviada(rest, indice=0) -> bytes:
    """XML da DPS contido no corpo do n-ésimo POST."""
    corpo = rest.chamadas("POST")[indice][2]
    return descomprimir_b64(json.loads(corpo)["dpsXmlGZipB64"])


def head(status):
    return RespostaHttp(status, b"")


def corpo_chave(chave):
    return RespostaHttp(200, json.dumps({"chaveAcesso": chave}).encode())


def estado_de(dfe, id_dps):
    return next(r for r in dfe.nfse.emissoes() if r.id_dps == id_dps)


# ---------------------------------------------------------------------------
# Fluxo feliz
# ---------------------------------------------------------------------------

def test_emissao_feliz_grava_tudo_e_assina_com_o_perfil_nfse(dfe, rest, tmp_path):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    rest.respostas = [resposta_emissao(xml, chave)]

    retorno = dfe.nfse.emitir(dps)

    assert retorno.chave == chave and retorno.xml_nfse == xml and retorno.status_http == 201
    [(metodo, url, _, cabecalhos)] = rest.enviados
    assert (metodo, url) == ("POST", f"{BASE}/nfse")
    enviada = etree.fromstring(dps_enviada(rest))
    assert verificar_assinatura(enviada, PERFIL_NFSE)
    assert [etree.QName(f).localname for f in enviada] == ["infDPS", "Signature"]
    assert enviada.find("n:infDPS/n:tpAmb", NS).text == "2"

    [registro] = dfe.nfse.emissoes()
    assert registro.situacao is SituacaoEmissao.AUTORIZADA and registro.chave == chave
    assert registro.serie == 900 and registro.numero == 6 and registro.tentativas == 1
    base = tmp_path / "homologacao" / CNPJ_PRESTADOR
    assert (base / registro.caminho_dps).read_bytes() == dps_enviada(rest)  # exatamente o enviado
    assert json.loads((base / registro.caminho_resposta).read_text())["chaveAcesso"] == chave
    [nota] = dfe.consultar(FiltroConsulta(modelos=["nfse"]))
    assert nota.chave == chave and nota.situacao is SituacaoDocumento.AUTORIZADO
    assert dfe.repositorio.ler_xml(nota) == xml


def test_auditoria_nao_guarda_xml(dfe, rest, tmp_path):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    rest.respostas = [resposta_emissao(xml, chave)]
    dfe.nfse.emitir(dps)
    bruto = (tmp_path / "homologacao" / CNPJ_PRESTADOR / "_estado" / "chamadas.jsonl").read_text()
    assert "<" not in bruto and "xDescServ" not in bruto and "Desenvolvimento" not in bruto
    operacoes = [json.loads(linha)["operacao"] for linha in bruto.splitlines()]
    assert operacoes == ["emitir", "emitir"]  # chamada HTTP e conclusão


def test_emitir_de_novo_apos_autorizada_nao_chama_a_sefin(dfe, rest):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    rest.respostas = [resposta_emissao(xml, chave)]
    primeira = dfe.nfse.emitir(dps)
    segunda = dfe.nfse.emitir(dps)  # a fábrica de respostas está vazia: qualquer chamada falharia
    assert segunda.chave == primeira.chave and segunda.xml_nfse == xml
    assert len(rest.enviados) == 1


def test_numeracao_automatica_por_serie(dfe, rest):
    for esperado in (1, 2):
        dps = dps_minima(numero=None)
        chave, xml = nfse_xml(dps, numero_nfse=esperado)
        rest.respostas = [resposta_emissao(xml, chave)]
        dfe.nfse.emitir(dps)
        assert dfe.nfse.emissoes()[-1].numero == esperado
    outra_serie = dps_minima(numero=None, serie=1)
    chave, xml = nfse_xml(outra_serie)
    rest.respostas = [resposta_emissao(xml, chave)]
    dfe.nfse.emitir(outra_serie)
    assert [(r.serie, r.numero) for r in dfe.nfse.emissoes()] == [(1, 1), (900, 1), (900, 2)]


def test_dhemi_automatica_respeita_a_folga(dfe, rest, relogio):
    dps = dps_minima(data_emissao=None)
    chave, xml = nfse_xml(dps)
    rest.respostas = [resposta_emissao(xml, chave)]
    dfe.nfse.emitir(dps)
    dh = etree.fromstring(dps_enviada(rest)).find("n:infDPS/n:dhEmi", NS).text
    esperado = relogio().astimezone(dt.timezone(dt.timedelta(hours=-3))) - dt.timedelta(seconds=60)
    assert dh == esperado.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Rejeição
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("estilo", ["camel", "pascal"])
def test_rejeicao_marca_o_estado_e_preserva_a_evidencia(dfe, rest, tmp_path, estilo):
    rest.respostas = [resposta_erro("E0712", "totTrib incompatível", estilo=estilo,
                                    complemento="infDPS/valores/trib/totTrib")]
    with pytest.raises(RejeicaoNfseErro) as capturado:
        dfe.nfse.emitir(dps_minima())
    assert capturado.value.codigos == ["E0712"] and capturado.value.id_dps
    [registro] = dfe.nfse.emissoes()
    assert registro.situacao is SituacaoEmissao.REJEITADA
    assert registro.erros[0]["codigo"] == "E0712"
    assert registro.erros[0]["complemento"] == "infDPS/valores/trib/totTrib"
    assert dfe.consultar(FiltroConsulta(modelos=["nfse"])) == []
    base = tmp_path / "homologacao" / CNPJ_PRESTADOR
    assert (base / registro.caminho_dps).exists() and (base / registro.caminho_resposta).exists()


def test_corrigir_e_reemitir_apos_rejeicao_guarda_as_duas_tentativas(dfe, rest, tmp_path):
    rest.respostas = [resposta_erro("E0712")]
    with pytest.raises(RejeicaoNfseErro):
        dfe.nfse.emitir(dps_minima())
    corrigida = dps_minima(valores=dps_minima().valores)
    chave, xml = nfse_xml(corrigida)
    rest.respostas = [resposta_emissao(xml, chave)]
    dfe.nfse.emitir(corrigida)
    [registro] = dfe.nfse.emissoes()
    assert registro.situacao is SituacaoEmissao.AUTORIZADA and registro.tentativas == 2
    pasta = tmp_path / "homologacao" / CNPJ_PRESTADOR / "nfse" / "dps" / "2026-10"
    assert sorted(p.name.split("-", 1)[1] for p in pasta.iterdir()) == [
        "dps-2.xml", "dps.xml", "resposta-2.json", "resposta.json"]


def test_e0014_e_tratado_como_ja_emitida(dfe, rest):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    rest.respostas = [resposta_erro("E0014", "DPS já gerou NFS-e"), head(200),
                      corpo_chave(chave), resposta_emissao(xml, chave, status=200)]
    retorno = dfe.nfse.emitir(dps)
    assert retorno.chave == chave and len(rest.chamadas("POST")) == 1
    assert [e[0] for e in rest.enviados] == ["POST", "HEAD", "GET", "GET"]
    assert dfe.nfse.emissoes()[0].situacao is SituacaoEmissao.AUTORIZADA


def test_e0014_sem_confirmacao_na_sefin_continua_sendo_rejeicao(dfe, rest):
    rest.respostas = [resposta_erro("E0014"), head(404)]
    with pytest.raises(RejeicaoNfseErro, match="E0014"):
        dfe.nfse.emitir(dps_minima())
    assert dfe.nfse.emissoes()[0].situacao is SituacaoEmissao.REJEITADA


# ---------------------------------------------------------------------------
# Timeout, 5xx e reconciliação: o POST nunca é repetido às cegas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("falha", [ComunicacaoErro("timeout"), RespostaHttp(503, b"")])
def test_falha_na_emissao_com_nota_existente_reconcilia_sem_segundo_post(dfe, rest, falha):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    rest.respostas = [falha, head(200), corpo_chave(chave),
                      resposta_emissao(xml, chave, status=200)]
    retorno = dfe.nfse.emitir(dps)
    assert retorno.chave == chave and retorno.id_dps
    assert len(rest.chamadas("POST")) == 1
    assert dfe.nfse.emissoes()[0].situacao is SituacaoEmissao.AUTORIZADA
    assert dfe.consultar(FiltroConsulta(modelos=["nfse"]))[0].chave == chave


def test_falha_sem_nota_na_sefin_levanta_incerta_e_o_proximo_emitir_reenvia(dfe, rest):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    rest.respostas = [ComunicacaoErro("timeout"), head(404)]
    with pytest.raises(EmissaoIncertaErro) as capturado:
        dfe.nfse.emitir(dps)
    assert capturado.value.nao_localizada and "seguro" in str(capturado.value)
    assert len(rest.chamadas("POST")) == 1
    assert dfe.nfse.emissoes()[0].situacao is SituacaoEmissao.INCERTA

    # Nova chamada: reconcilia primeiro (HEAD 404) e só então reenvia o POST.
    rest.respostas = [head(404), resposta_emissao(xml, chave)]
    retorno = dfe.nfse.emitir(dps)
    assert retorno.chave == chave and len(rest.chamadas("POST")) == 2
    assert [e[0] for e in rest.enviados[-2:]] == ["HEAD", "POST"]
    assert dfe.nfse.emissoes()[0].situacao is SituacaoEmissao.AUTORIZADA


def test_reconciliacao_indisponivel_mantem_a_emissao_incerta(dfe, rest):
    rest.respostas = [ComunicacaoErro("timeout"), ComunicacaoErro("rede fora")]
    with pytest.raises(EmissaoIncertaErro) as capturado:
        dfe.nfse.emitir(dps_minima())
    assert not capturado.value.nao_localizada and "reconciliar" in str(capturado.value)
    assert dfe.nfse.emissoes()[0].situacao is SituacaoEmissao.INCERTA


def test_resposta_2xx_ilegivel_tambem_e_incerta(dfe, rest):
    rest.respostas = [RespostaHttp(201, b"nao e json"), head(404)]
    with pytest.raises(EmissaoIncertaErro):
        dfe.nfse.emitir(dps_minima())


def test_reconciliar_publico_e_pendentes(dfe, rest):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    rest.respostas = [ComunicacaoErro("timeout"), ComunicacaoErro("rede fora")]
    with pytest.raises(EmissaoIncertaErro) as capturado:
        dfe.nfse.emitir(dps)
    id_dps = capturado.value.id_dps
    rest.respostas = [head(200), corpo_chave(chave), resposta_emissao(xml, chave, status=200)]
    resultado = dfe.nfse.reconciliar_pendentes()
    assert resultado[id_dps].chave == chave
    assert dfe.nfse.emissoes("incerta") == [] and len(dfe.nfse.emissoes("autorizada")) == 1
    assert dfe.nfse.reconciliar(id_dps).chave == chave  # já autorizada: sem rede


def test_reconciliar_descartando_nao_localizada_libera_a_numeracao(dfe, rest):
    rest.respostas = [ComunicacaoErro("timeout"), head(404)]
    with pytest.raises(EmissaoIncertaErro) as capturado:
        dfe.nfse.emitir(dps_minima(numero=None))
    # Com uma emissão incerta na série, a numeração automática é recusada.
    with pytest.raises(ValidacaoErro, match="reconciliar_pendentes"):
        dfe.nfse.emitir(dps_minima(numero=None))
    rest.respostas = [head(404)]
    assert dfe.nfse.reconciliar(capturado.value.id_dps, descartar_nao_localizada=True) is None
    assert dfe.nfse.emissoes()[0].situacao is SituacaoEmissao.REJEITADA
    dps = dps_minima(numero=None)
    chave, xml = nfse_xml(dps, numero_nfse=2)
    rest.respostas = [resposta_emissao(xml, chave)]
    dfe.nfse.emitir(dps)
    assert [r.numero for r in dfe.nfse.emissoes()] == [1, 2]


def test_id_da_nfse_devolvida_diferente_da_chave(dfe, rest):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    outra_chave, _ = nfse_xml(dps, numero_nfse=99)
    rest.respostas = [resposta_emissao(xml, outra_chave)]
    with pytest.raises(RespostaInvalidaErro, match="não confere"):
        dfe.nfse.emitir(dps)
    assert dfe.nfse.emissoes()[0].situacao is SituacaoEmissao.INCERTA
    assert dfe.consultar(FiltroConsulta(modelos=["nfse"])) == []


# ---------------------------------------------------------------------------
# DPS em XML pronto
# ---------------------------------------------------------------------------

def dps_xml_nao_assinada(relogio, ambiente=Ambiente.HOMOLOGACAO, **alteracoes) -> bytes:
    return MontadorDps(ambiente, relogio).serializar(dps_minima(**alteracoes))


def test_xml_pronto_sem_assinatura_e_assinado_pela_biblioteca(dfe, rest, relogio):
    xml = dps_xml_nao_assinada(relogio)
    chave, nfse = nfse_xml(dps_minima())
    rest.respostas = [resposta_emissao(nfse, chave)]
    dfe.nfse.emitir(xml)
    assert verificar_assinatura(etree.fromstring(dps_enviada(rest)), PERFIL_NFSE)


def test_xml_ja_assinado_e_enviado_exatamente_como_esta(dfe, rest, relogio):
    assinada = dfe.nfse.gerar_dps_assinada(dps_minima())
    assert dfe.nfse.emissoes() == []  # conferência não reserva número
    chave, nfse = nfse_xml(dps_minima())
    rest.respostas = [resposta_emissao(nfse, chave)]
    dfe.nfse.emitir(assinada)
    assert dps_enviada(rest) == assinada


def test_tpamb_divergente_falha_antes_de_qualquer_chamada(dfe, rest, relogio):
    xml = dps_xml_nao_assinada(relogio, Ambiente.PRODUCAO)
    with pytest.raises(ValidacaoErro, match="E0006"):
        dfe.nfse.emitir(xml)
    assert rest.enviados == [] and dfe.nfse.emissoes() == []


def test_id_inconsistente_com_os_campos_falha_antes_de_enviar(dfe, rest, relogio):
    xml = dps_xml_nao_assinada(relogio).replace(b"<serie>900</serie>", b"<serie>901</serie>")
    with pytest.raises(ValidacaoErro, match="E0004"):
        dfe.nfse.emitir(xml)
    assert rest.enviados == []


@pytest.mark.parametrize("xml, mensagem", [
    (b"<DPS", "malformado"), (b"<outra/>", "raiz DPS"),
    (f'<DPS xmlns="{NAMESPACE_NFSE}"/>'.encode(), "infDPS"),
    (f'<DPS xmlns="{NAMESPACE_NFSE}"><infDPS Id="x"/></DPS>'.encode(), "Id da DPS"),
    ("texto", "Dps ou o XML")])
def test_entradas_invalidas(dfe, rest, xml, mensagem):
    with pytest.raises(ValidacaoErro, match=mensagem):
        dfe.nfse.emitir(xml)
    assert rest.enviados == []


# ---------------------------------------------------------------------------
# Configuração, leiaute, XSD, trava e certificado
# ---------------------------------------------------------------------------

def test_trava_impede_emissao_concorrente(dfe, rest, tmp_path):
    trava = tmp_path / "homologacao" / CNPJ_PRESTADOR / "_estado" / "emissao_nfse.lock"
    trava.parent.mkdir(parents=True)
    trava.write_text("pid=1")
    with pytest.raises(SincronizacaoEmAndamentoErro):
        dfe.nfse.emitir(dps_minima())
    assert rest.enviados == []


def test_trava_e_liberada_apos_erro(dfe, rest, tmp_path):
    rest.respostas = [resposta_erro("E0712")]
    with pytest.raises(RejeicaoNfseErro):
        dfe.nfse.emitir(dps_minima())
    assert not (tmp_path / "homologacao" / CNPJ_PRESTADOR / "_estado"
                / "emissao_nfse.lock").exists()


def test_sem_certificado_nao_emite(tmp_path):
    somente_leitura = FreeDFe(cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO, pasta=tmp_path)
    with pytest.raises(CertificadoErro):
        somente_leitura.nfse.emitir(dps_minima())


def test_validacao_xsd_configurada_bloqueia_dps_fora_do_schema(tmp_path, certificado, relogio,
                                                               rest):
    (tmp_path / "xsd").mkdir()
    (tmp_path / "xsd" / "DPS_v1.01.xsd").write_text(
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        f'targetNamespace="{NAMESPACE_NFSE}" xmlns="{NAMESPACE_NFSE}" '
        'elementFormDefault="qualified"><xs:element name="DPS"><xs:complexType><xs:sequence>'
        '<xs:element name="infDPS" type="xs:string"/></xs:sequence></xs:complexType>'
        "</xs:element></xs:schema>")
    dfe = FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                  pasta=tmp_path / "dados", transporte_rest=rest, relogio=relogio,
                  config_nfse=ConfiguracaoNfseLocal(
                      pasta_xsd={VersaoLeiaute.NT009_V101: tmp_path / "xsd"}))
    with pytest.raises(ValidacaoErro, match="fora do schema"):
        dfe.nfse.emitir(dps_minima())
    assert rest.enviados == [] and dfe.nfse.emissoes() == []


def test_leiaute_por_ambiente_e_explicito(tmp_path, certificado, relogio):
    """Com IBS/CBS, NT 004 mantém indDest em IBSCBS; NT 009 o move para a raiz."""
    dps = dps_minima(servico=servico_com_nbs(), ibs_cbs=ibs_cbs_minimo())

    pastas = iter(range(100))

    def posts(ambiente, leiaute=None):
        rest = TransporteRestFalso([RespostaHttp(500, b""), head(404)])
        dfe = FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=ambiente,
                      pasta=tmp_path / f"dados{next(pastas)}", transporte_rest=rest,
                      relogio=relogio, config_nfse=ConfiguracaoNfseLocal(esperas_consulta=()))
        with pytest.raises(EmissaoIncertaErro):
            dfe.nfse.emitir(dps, leiaute=leiaute)
        return etree.fromstring(dps_enviada(rest))

    def indest_na_raiz(raiz):
        return raiz.find("n:infDPS/n:indDest", NS) is not None

    assert indest_na_raiz(posts(Ambiente.HOMOLOGACAO))  # produção restrita: NT 009
    assert not indest_na_raiz(posts(Ambiente.PRODUCAO))  # 29/10/2026: NT 004 em produção
    assert not indest_na_raiz(posts(Ambiente.HOMOLOGACAO, VersaoLeiaute.NT004_V2))  # explícito
    relogio.agora = dt.datetime(2026, 12, 2, 15, 0, tzinfo=dt.timezone.utc)
    assert indest_na_raiz(posts(Ambiente.PRODUCAO, None))  # a partir de 01/12/2026: NT 009


def test_consultar_chave_armazena_a_nfse(dfe, rest):
    chave, xml = nfse_xml(dps_minima())
    rest.respostas = [resposta_emissao(xml, chave, status=200)]
    retorno = dfe.nfse.consultar_chave(chave)
    assert retorno.xml_nfse == xml and retorno.id_dps is None
    assert dfe.consultar(FiltroConsulta(modelos=["nfse"]))[0].chave == chave
    rest.respostas = [resposta_emissao(xml, chave, status=200)]
    dfe.nfse.consultar_chave(chave, armazenar=False)
    assert rest.enviados[-1][1] == f"{BASE}/nfse/{chave}"


def test_reemissao_com_consulta_indisponivel_nao_reenvia(dfe, rest):
    dps = dps_minima()
    rest.respostas = [ComunicacaoErro("timeout"), head(404)]
    with pytest.raises(EmissaoIncertaErro):
        dfe.nfse.emitir(dps)
    rest.respostas = [RespostaHttp(403, b"")]  # HEAD ambíguo: não dá para confirmar
    with pytest.raises(EmissaoIncertaErro, match="consulta à Sefin falhou"):
        dfe.nfse.emitir(dps)
    assert len(rest.chamadas("POST")) == 1
