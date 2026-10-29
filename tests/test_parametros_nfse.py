"""Parâmetros municipais: cliente, cache de 24 h e verificação pré-emissão."""

import datetime as dt
import json

import pytest

from freedfe import Ambiente, FreeDFe
from freedfe.auditoria import RegistroAuditoria
from freedfe.excecoes import ComunicacaoErro, RejeicaoNfseErro, ValidacaoErro
from freedfe.nfse.parametros import (
    CacheParametros,
    ClienteParametros,
    Convenio,
    interpretar_parametros,
)
from freedfe.nfse.servico import ConfiguracaoNfseLocal
from freedfe.servicos import configuracao_nfse
from freedfe.transporte import RespostaHttp

from conftest import RelogioFalso, TransporteRestFalso
from fabrica_nfse import (
    CNPJ_PRESTADOR,
    DATA_EMISSAO,
    MUNICIPIO,
    dps_minima,
    nfse_xml,
    resposta_emissao,
)

ADN = "https://adn.producaorestrita.nfse.gov.br/parametrizacao"


def json_ok(dados, status=200) -> RespostaHttp:
    return RespostaHttp(status, json.dumps(dados).encode())


def cliente(tmp_path, respostas, legada=False, ambiente=Ambiente.HOMOLOGACAO):
    transporte = TransporteRestFalso(respostas)
    return ClienteParametros(configuracao_nfse(ambiente), transporte,
                             RegistroAuditoria(tmp_path / "a.jsonl"), esperas=(),
                             rota_legada=legada), transporte


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------

def test_rotas_no_host_adn(tmp_path):
    api, transporte = cliente(tmp_path, [json_ok({})] * 3)
    api.convenio(MUNICIPIO)
    api.servico_municipal(MUNICIPIO, "01.01.01")
    api.contribuinte(MUNICIPIO, CNPJ_PRESTADOR)
    assert [(m, u) for m, u, *_ in transporte.enviados] == [
        ("GET", f"{ADN}/{MUNICIPIO}/convenio"), ("GET", f"{ADN}/{MUNICIPIO}/010101"),
        ("GET", f"{ADN}/{MUNICIPIO}/{CNPJ_PRESTADOR}")]


def test_producao_e_rota_legada(tmp_path):
    api, transporte = cliente(tmp_path, [json_ok({})], ambiente=Ambiente.PRODUCAO)
    api.convenio(MUNICIPIO)
    assert transporte.enviados[0][1] == f"https://adn.nfse.gov.br/parametrizacao/{MUNICIPIO}/convenio"
    api, transporte = cliente(tmp_path, [json_ok({})], legada=True)
    api.convenio(MUNICIPIO)
    assert transporte.enviados[0][1] == (
        f"https://sefin.producaorestrita.nfse.gov.br/SefinNacional/parametros_municipais/"
        f"{MUNICIPIO}/convenio")


def test_argumentos_invalidos_nao_chamam_a_rede(tmp_path):
    api, transporte = cliente(tmp_path, [])
    for chamada in (lambda: api.convenio("12"), lambda: api.servico_municipal(MUNICIPIO, "1"),
                    lambda: api.contribuinte(MUNICIPIO, "123")):
        with pytest.raises(ValidacaoErro):
            chamada()
    assert transporte.enviados == []


def test_interpretacao_das_respostas():
    assert interpretar_parametros(json_ok({"a": 1})) == {"a": 1}
    assert interpretar_parametros(json_ok([1, 2])) == {"itens": [1, 2]}
    assert interpretar_parametros(RespostaHttp(200, b"")) == {}
    with pytest.raises(RejeicaoNfseErro) as capturado:
        interpretar_parametros(RespostaHttp(404, b'{"erros":[{"codigo":"E0900"}]}'))
    assert capturado.value.codigos == ["E0900"]


@pytest.mark.parametrize("dados, aderente, ativacao", [
    ({"aderente_ambiente_nacional": True, "dataAtivacao": "2026-01-01"}, True,
     dt.date(2026, 1, 1)),
    ({"AderenteAmbienteNacional": "S", "DataAtivacao": "01/03/2026"}, True, dt.date(2026, 3, 1)),
    ({"aderente": "false", "dtAtivacao": "2026-01-01T00:00:00Z"}, False, dt.date(2026, 1, 1)),
    ({"aderente": 0}, False, None),
    ({"qualquer": "coisa"}, None, None),
])
def test_convenio_tolerante_a_nomes_e_formatos(dados, aderente, ativacao):
    convenio = Convenio.de_dict(MUNICIPIO, dados)
    assert convenio.aderente is aderente and convenio.data_ativacao == ativacao
    assert convenio.bruto == dados


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@pytest.fixture
def relogio():
    return RelogioFalso()


def test_cache_valido_nao_busca(tmp_path, relogio):
    cache = CacheParametros(tmp_path, relogio)
    chamadas = []
    buscar = lambda: chamadas.append(1) or {"v": len(chamadas)}  # noqa: E731
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar) == {"v": 1}
    relogio.avancar(hours=23)
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar) == {"v": 1}
    assert len(chamadas) == 1


def test_cache_expirado_e_atualizar_buscam_de_novo(tmp_path, relogio):
    cache = CacheParametros(tmp_path, relogio)
    chamadas = []
    buscar = lambda: chamadas.append(1) or {"v": len(chamadas)}  # noqa: E731
    cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar)
    relogio.avancar(hours=24, minutes=1)
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar) == {"v": 2}
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar, atualizar=True) == {"v": 3}


def test_cache_vencido_e_usado_quando_a_consulta_falha(tmp_path, relogio, caplog):
    cache = CacheParametros(tmp_path, relogio)
    cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"v": 1})
    relogio.avancar(days=3)

    def falha():
        raise ComunicacaoErro("sem internet")

    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", falha) == {"v": 1}
    assert "usando cache" in caplog.text
    with pytest.raises(ComunicacaoErro):
        cache.obter_ou_buscar(MUNICIPIO, "outro", falha)  # sem cache anterior: propaga


def test_rejeicao_nao_mascarada_pelo_cache(tmp_path, relogio):
    cache = CacheParametros(tmp_path, relogio)
    cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"v": 1})
    relogio.avancar(days=3)

    def rejeita():
        raise RejeicaoNfseErro([], status_http=404)

    with pytest.raises(RejeicaoNfseErro):
        cache.obter_ou_buscar(MUNICIPIO, "convenio", rejeita)


def test_cache_corrompido_e_refeito(tmp_path, relogio):
    cache = CacheParametros(tmp_path, relogio)
    cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"v": 1})
    (tmp_path / MUNICIPIO / "convenio.json").write_text("{nao e json")
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"v": 2}) == {"v": 2}


# ---------------------------------------------------------------------------
# ServicoNfse
# ---------------------------------------------------------------------------

@pytest.fixture
def rest():
    return TransporteRestFalso()


@pytest.fixture
def relogio_servico():
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


@pytest.fixture
def dfe(tmp_path, certificado, relogio_servico, rest):
    return FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                   pasta=tmp_path, transporte_rest=rest, relogio=relogio_servico,
                   config_nfse=ConfiguracaoNfseLocal(esperas_consulta=()))


def test_convenio_e_consultado_uma_vez_por_dia(dfe, rest, relogio_servico, tmp_path):
    rest.respostas = [json_ok({"aderente_ambiente_nacional": True, "dataAtivacao": "2026-01-01"})]
    for _ in range(3):
        assert dfe.nfse.convenio(MUNICIPIO).aderente is True
    assert len(rest.enviados) == 1
    cache = tmp_path / "homologacao" / CNPJ_PRESTADOR / "_estado" / "parametros_nfse"
    assert (cache / MUNICIPIO / "convenio.json").exists()
    relogio_servico.avancar(hours=25)
    rest.respostas = [json_ok({"aderente": False})]
    assert dfe.nfse.convenio(MUNICIPIO).aderente is False
    rest.respostas = [json_ok({"aderente": True})]
    assert dfe.nfse.convenio(MUNICIPIO, atualizar=True).aderente is True


def test_parametros_de_servico_e_contribuinte(dfe, rest):
    rest.respostas = [json_ok({"aliquota": 2.5}), json_ok({"retencoes": []})]
    assert dfe.nfse.parametros_servico(MUNICIPIO, "010101") == {"aliquota": 2.5}
    assert dfe.nfse.parametros_contribuinte(MUNICIPIO) == {"retencoes": []}
    assert rest.enviados[1][1].endswith(f"/{MUNICIPIO}/{CNPJ_PRESTADOR}")


def test_verificar_pre_emissao_bloqueia_municipio_nao_aderente(dfe, rest):
    rest.respostas = [json_ok({"aderente": False})]
    with pytest.raises(ValidacaoErro, match="E0037"):
        dfe.nfse.verificar_pre_emissao(dps_minima())


def test_verificar_pre_emissao_avisa_competencia_anterior_a_ativacao(dfe, rest, caplog):
    rest.respostas = [json_ok({"aderente": True, "dataAtivacao": "2026-11-01"})]
    avisos = dfe.nfse.verificar_pre_emissao(dps_minima())
    assert len(avisos) == 1 and "E0016" in avisos[0] and "E0016" in caplog.text


def test_resposta_desconhecida_nao_bloqueia(dfe, rest):
    rest.respostas = [json_ok({"formato": "novo"})]
    assert dfe.nfse.verificar_pre_emissao(dps_minima()) == []


def test_emitir_com_verificacao_bloqueia_antes_de_reservar_e_enviar(dfe, rest):
    rest.respostas = [json_ok({"aderente": False})]
    with pytest.raises(ValidacaoErro, match="E0037"):
        dfe.nfse.emitir(dps_minima(), verificar_parametros=True)
    assert [e[0] for e in rest.enviados] == ["GET"] and dfe.nfse.emissoes() == []


def test_emitir_com_verificacao_aderente_segue_o_fluxo(dfe, rest):
    dps = dps_minima()
    chave, xml = nfse_xml(dps)
    rest.respostas = [json_ok({"aderente": True, "dataAtivacao": "2026-01-01"}),
                      resposta_emissao(xml, chave)]
    assert dfe.nfse.emitir(dps, verificar_parametros=True).chave == chave
    assert [e[0] for e in rest.enviados] == ["GET", "POST"]
