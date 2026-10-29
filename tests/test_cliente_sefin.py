import json

import pytest

from freedfe.auditoria import RegistroAuditoria
from freedfe.excecoes import ComunicacaoErro, RespostaInvalidaErro, ValidacaoErro
from freedfe.nfse.cliente_sefin import ClienteSefin
from freedfe.nfse.envelope import descomprimir_b64
from freedfe.servicos import configuracao_nfse
from freedfe.tipos import Ambiente
from freedfe.transporte import RespostaHttp

from conftest import TransporteRestFalso
from fabrica_nfse import gerar_chave_nfse, resposta_erro

ID_DPS = "DPS" + "2927408" + "2" + "11222333000181" + "00900" + "000000000000006"
CHAVE = gerar_chave_nfse()
BASE = "https://sefin.producaorestrita.nfse.gov.br/SefinNacional"


@pytest.fixture
def pausas():
    return []


@pytest.fixture
def montar(tmp_path, pausas):
    def _montar(respostas, ambiente=Ambiente.HOMOLOGACAO, prefixo="", esperas=(2.0, 4.0, 8.0)):
        transporte = TransporteRestFalso(respostas)
        cliente = ClienteSefin(configuracao_nfse(ambiente, prefixo), transporte,
                               RegistroAuditoria(tmp_path / "chamadas.jsonl"),
                               pausa=pausas.append, esperas=esperas)
        return cliente, transporte
    return _montar


def auditoria(tmp_path):
    texto = (tmp_path / "chamadas.jsonl").read_text()
    return [json.loads(linha) for linha in texto.splitlines()]


def test_emitir_envia_json_com_dps_compactada_em_post_unico(montar):
    cliente, transporte = montar([RespostaHttp(201, b"{}")])
    cliente.emitir(b"<DPS>conteudo</DPS>", ID_DPS)
    [(metodo, url, corpo, _)] = transporte.enviados
    assert (metodo, url) == ("POST", f"{BASE}/nfse")
    assert list(json.loads(corpo)) == ["dpsXmlGZipB64"]
    assert descomprimir_b64(json.loads(corpo)["dpsXmlGZipB64"]) == b"<DPS>conteudo</DPS>"


def test_post_nao_e_repetido_nem_em_falha_de_rede_nem_em_5xx(montar, pausas):
    cliente, transporte = montar([ComunicacaoErro("timeout")])
    with pytest.raises(ComunicacaoErro):
        cliente.emitir(b"<DPS/>", ID_DPS)
    assert len(transporte.enviados) == 1 and pausas == []
    cliente, transporte = montar([RespostaHttp(503, b"{}")])
    assert cliente.emitir(b"<DPS/>", ID_DPS).status == 503
    assert len(transporte.enviados) == 1 and pausas == []


def test_urls_por_ambiente_e_prefixo(montar):
    cliente, transporte = montar([RespostaHttp(200, b"{}")] * 3, Ambiente.PRODUCAO)
    cliente.consultar_nfse(CHAVE)
    assert transporte.enviados[0][1] == f"https://sefin.nfse.gov.br/SefinNacional/nfse/{CHAVE}"
    cliente, transporte = montar([RespostaHttp(200, b"{}")], prefixo="/API")
    cliente.consultar_nfse(CHAVE)
    assert transporte.enviados[0][1] == (
        f"https://sefin.producaorestrita.nfse.gov.br/API/SefinNacional/nfse/{CHAVE}")


def test_get_e_repetido_com_espera_crescente_em_5xx_e_falha_de_rede(montar, pausas):
    ok = RespostaHttp(200, b'{"a": 1}')
    cliente, transporte = montar([ComunicacaoErro("reset"), RespostaHttp(503, b""), ok])
    assert cliente.consultar_nfse(CHAVE) == ok
    assert len(transporte.enviados) == 3 and pausas == [2.0, 4.0]


def test_get_esgota_tentativas_e_devolve_a_ultima_resposta(montar, pausas):
    cliente, transporte = montar([RespostaHttp(503, b"")] * 4)
    assert cliente.consultar_nfse(CHAVE).status == 503
    assert len(transporte.enviados) == 4 and pausas == [2.0, 4.0, 8.0]
    cliente, _ = montar([ComunicacaoErro("a")] * 4)
    with pytest.raises(ComunicacaoErro):
        cliente.consultar_nfse(CHAVE)


def test_4xx_nao_e_repetido(montar, pausas):
    cliente, transporte = montar([resposta_erro("E0900", status=404)])
    assert cliente.consultar_nfse(CHAVE).status == 404
    assert len(transporte.enviados) == 1 and pausas == []


@pytest.mark.parametrize("status, esperado", [(200, True), (404, False)])
def test_dps_processada(montar, status, esperado):
    cliente, transporte = montar([RespostaHttp(status)])
    assert cliente.dps_processada(ID_DPS) is esperado
    assert transporte.enviados[0][:2] == ("HEAD", f"{BASE}/dps/{ID_DPS}")


@pytest.mark.parametrize("status", [401, 403, 400])
def test_dps_processada_com_status_ambiguo_levanta(montar, status):
    cliente, _ = montar([RespostaHttp(status)])
    with pytest.raises(RespostaInvalidaErro, match=str(status)):
        cliente.dps_processada(ID_DPS)


def test_codigos_de_head_sao_parametrizaveis(tmp_path):
    cliente = ClienteSefin(configuracao_nfse(Ambiente.HOMOLOGACAO), TransporteRestFalso(
        [RespostaHttp(204), RespostaHttp(410)]), RegistroAuditoria(tmp_path / "a.jsonl"),
        status_dps_existente=frozenset({204}), status_dps_inexistente=frozenset({410}))
    assert cliente.dps_processada(ID_DPS) is True and cliente.dps_processada(ID_DPS) is False


def test_chave_da_dps(montar):
    corpo = json.dumps({"ChaveAcesso": CHAVE}).encode()
    cliente, _ = montar([RespostaHttp(200, corpo), RespostaHttp(404, b"{}"),
                         RespostaHttp(200, b"{}"), RespostaHttp(403, b"{}")])
    assert cliente.chave_da_dps(ID_DPS) == CHAVE
    assert cliente.chave_da_dps(ID_DPS) is None
    with pytest.raises(RespostaInvalidaErro, match="sem a chave"):
        cliente.chave_da_dps(ID_DPS)
    with pytest.raises(RespostaInvalidaErro, match="403"):
        cliente.chave_da_dps(ID_DPS)


def test_registrar_evento_usa_o_campo_correto_do_corpo(montar):
    cliente, transporte = montar([RespostaHttp(201, b"{}")])
    cliente.registrar_evento(CHAVE, b"<pedRegEvento/>")
    [(metodo, url, corpo, _)] = transporte.enviados
    assert (metodo, url) == ("POST", f"{BASE}/nfse/{CHAVE}/eventos")
    assert list(json.loads(corpo)) == ["pedidoRegistroEventoXmlGZipB64"]  # outro nome => HTTP 500
    assert descomprimir_b64(json.loads(corpo)["pedidoRegistroEventoXmlGZipB64"]) == (
        b"<pedRegEvento/>")


def test_consulta_de_eventos(montar):
    cliente, transporte = montar([RespostaHttp(200, b"{}")] * 3)
    cliente.eventos(CHAVE)
    cliente.eventos(CHAVE, "101101")
    cliente.eventos(CHAVE, 101101, 1)
    assert [e[1] for e in transporte.enviados] == [
        f"{BASE}/nfse/{CHAVE}/eventos", f"{BASE}/nfse/{CHAVE}/eventos/101101",
        f"{BASE}/nfse/{CHAVE}/eventos/101101/1"]


def test_identificadores_invalidos_falham_antes_de_qualquer_chamada(montar):
    cliente, transporte = montar([])
    for chamada in (lambda: cliente.emitir(b"<x/>", "DPS123"),
                    lambda: cliente.consultar_nfse("../../etc"),
                    lambda: cliente.dps_processada("x"),
                    lambda: cliente.registrar_evento("123", b"<x/>"),
                    lambda: cliente.eventos(CHAVE, "abc")):
        with pytest.raises((ValidacaoErro, ValueError)):
            chamada()
    assert transporte.enviados == []


def test_auditoria_registra_codigos_sem_xml(montar, tmp_path):
    cliente, _ = montar([resposta_erro("E0712", status=400), RespostaHttp(201, b"{}"),
                         ComunicacaoErro("socket")], esperas=())
    cliente.emitir(b"<DPS><CPF>12345678909</CPF></DPS>", ID_DPS)
    cliente.registrar_evento(CHAVE, b"<pedRegEvento/>")
    with pytest.raises(ComunicacaoErro):
        cliente.consultar_nfse(CHAVE)
    registros = auditoria(tmp_path)
    assert [(r["operacao"], r["status_http"], r["codigos"]) for r in registros] == [
        ("emitir", 400, ["E0712"]), ("registrar_evento", 201, []), ("consultar_nfse", None, [])]
    assert registros[0]["id_dps"] == ID_DPS and registros[1]["chave"] == CHAVE
    assert registros[2]["erro"] == "socket" and registros[0]["servico"] == "nfse-sefin"
    bruto = (tmp_path / "chamadas.jsonl").read_text()
    assert "<" not in bruto and "12345678909" not in bruto  # nenhum XML/CPF na trilha
