"""Sincronização das NFS-e e eventos pelo ADN (distribuição por NSU)."""

import datetime as dt
import json

import pytest

from freedfe import Ambiente, FiltroConsulta, FreeDFe, SituacaoDocumento
from freedfe.excecoes import ComunicacaoErro, RejeicaoNfseErro, SincronizacaoEmAndamentoErro
from freedfe.nfse.cliente_adn import interpretar_lote
from freedfe.nfse.servico import ConfiguracaoNfseLocal
from freedfe.transporte import RespostaHttp

from conftest import RelogioFalso, TransporteRestFalso
from fabrica_nfse import (
    CNPJ_PRESTADOR,
    CNPJ_TOMADOR,
    DATA_EMISSAO,
    dps_minima,
    evento_nfse_xml,
    nfse_xml,
    resposta_adn,
    resposta_adn_vazia,
)

ADN = "https://adn.producaorestrita.nfse.gov.br/contribuintes"
FILTRO = FiltroConsulta(modelos=["nfse"])


@pytest.fixture
def pausas():
    return []


@pytest.fixture
def relogio():
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


@pytest.fixture
def rest():
    return TransporteRestFalso()


@pytest.fixture
def dfe(tmp_path, certificado, relogio, rest, pausas):
    return FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                   pasta=tmp_path, transporte_rest=rest, relogio=relogio,
                   config_nfse=ConfiguracaoNfseLocal(
                       esperas_consulta=(), pausa=pausas.append, pausa_entre_lotes_adn=0.5))


def urls(rest):
    return [u.removeprefix(ADN) for _, u, *_ in rest.enviados]


def test_dois_lotes_e_termino_em_lote_vazio(dfe, rest, pausas):
    chave1, nota1 = nfse_xml(dps_minima(), numero_nfse=1)
    chave2, nota2 = nfse_xml(dps_minima(numero=7), numero_nfse=2)
    rest.respostas = [
        resposta_adn([(1, nota1), (2, nota2)]),
        resposta_adn([(3, evento_nfse_xml(chave1))]),
        resposta_adn_vazia()]
    resultado = dfe.nfse.sincronizar()
    assert resultado.executado and resultado.lotes == 3 and resultado.documentos == 3
    assert resultado.ult_nsu == "000000000000003" and resultado.ultimo_cstat == (
        "NENHUM_DOCUMENTO_LOCALIZADO")
    assert urls(rest) == ["/DFe/0?tipoNSU=DISTRIBUICAO&lote=true",
                          "/DFe/2?tipoNSU=DISTRIBUICAO&lote=true",
                          "/DFe/3?tipoNSU=DISTRIBUICAO&lote=true"]
    assert pausas == [0.5, 0.5]
    situacoes = {r.chave: r.situacao for r in dfe.consultar(FILTRO)}
    assert situacoes == {chave1: SituacaoDocumento.CANCELADO, chave2: SituacaoDocumento.AUTORIZADO}
    assert dfe.estado_nsu("nfse")["ult_nsu"] == "000000000000003"


def test_execucao_esgotada_bloqueia_ate_o_intervalo_configurado(dfe, rest, relogio):
    rest.respostas = [resposta_adn_vazia()]
    primeira = dfe.nfse.sincronizar()
    assert primeira.executado and primeira.proxima_consulta_em_s == 15 * 60
    segunda = dfe.nfse.sincronizar()
    assert not segunda.executado and segunda.proxima_consulta_em_s == 15 * 60
    assert "bloqueada" in segunda.motivo and len(rest.enviados) == 1
    relogio.avancar(minutes=16)
    rest.respostas = [resposta_adn_vazia()]
    assert dfe.nfse.sincronizar().executado and len(rest.enviados) == 2


def test_limite_de_lotes_nao_bloqueia(dfe, rest):
    _, nota = nfse_xml(dps_minima())
    rest.respostas = [resposta_adn([(1, nota)])]
    resultado = dfe.nfse.sincronizar(max_lotes=1)
    assert resultado.lotes == 1 and resultado.proxima_consulta_em_s == 0
    chave, nota2 = nfse_xml(dps_minima(numero=9), numero_nfse=2)
    rest.respostas = [resposta_adn([(2, nota2)]), resposta_adn_vazia()]
    assert dfe.nfse.sincronizar().ult_nsu == "000000000000002"


def test_documento_repetido_na_semantica_inclusiva_nao_duplica_nem_repete_em_laco(dfe, rest):
    chave, nota = nfse_xml(dps_minima())
    rest.respostas = [resposta_adn([(1, nota)]), resposta_adn([(1, nota)])]
    resultado = dfe.nfse.sincronizar()
    assert resultado.lotes == 2 and len(dfe.consultar(FILTRO)) == 1
    assert urls(rest)[1].startswith("/DFe/1?")  # pede o último NSU recebido


@pytest.mark.parametrize("estilo, duplo", [("pascal", False), ("camel", False), ("pascal", True)])
def test_formatos_de_resposta_tolerados(dfe, rest, estilo, duplo):
    chave, nota = nfse_xml(dps_minima())
    rest.respostas = [resposta_adn([(5, nota)], estilo=estilo, base64_duplo=duplo),
                      resposta_adn_vazia()]
    resultado = dfe.nfse.sincronizar()
    assert resultado.ult_nsu == "000000000000005"
    assert [r.chave for r in dfe.consultar(FILTRO)] == [chave]


def test_item_sem_arquivo_e_ignorado():
    corpo = {"StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
             "LoteDFe": [{"NSU": 1}, {"ArquivoXml": "x"}]}
    lote = interpretar_lote(RespostaHttp(200, json.dumps(corpo).encode()))
    assert lote.vazio and lote.maior_nsu is None


def test_lacunas_de_nsu_ficam_visiveis(dfe, rest):
    _, a = nfse_xml(dps_minima(), numero_nfse=1)
    _, b = nfse_xml(dps_minima(numero=8), numero_nfse=2)
    rest.respostas = [resposta_adn([(1, a), (4, b)]), resposta_adn_vazia()]
    dfe.nfse.sincronizar()
    assert dfe.lacunas_nsu("nfse") == ["000000000000002", "000000000000003"]


def test_429_respeita_retry_after_e_continua(dfe, rest, pausas):
    _, nota = nfse_xml(dps_minima())
    rest.respostas = [RespostaHttp(429, b"", {"Retry-After": "7"}), resposta_adn([(1, nota)]),
                      resposta_adn_vazia()]
    resultado = dfe.nfse.sincronizar()
    assert resultado.ult_nsu == "000000000000001" and 7.0 in pausas


def test_429_persistente_interrompe_sem_perder_o_progresso(dfe, rest, pausas):
    _, nota = nfse_xml(dps_minima())
    rest.respostas = [resposta_adn([(1, nota)]), RespostaHttp(429, b""), RespostaHttp(429, b""),
                      RespostaHttp(429, b"")]
    resultado = dfe.nfse.sincronizar()
    assert resultado.executado and resultado.ultimo_cstat == "429" and "429" in resultado.motivo
    assert resultado.ult_nsu == "000000000000001" and len(dfe.consultar(FILTRO)) == 1
    assert 60.0 in pausas  # espera padrão sem Retry-After
    assert resultado.proxima_consulta_em_s == 0  # não esgotou: pode tentar logo mais


def test_404_significa_sem_documentos(dfe, rest):
    rest.respostas = [RespostaHttp(404, b"{}")]
    resultado = dfe.nfse.sincronizar()
    assert resultado.executado and resultado.documentos == 0 and resultado.lotes == 1


def test_4xx_e_5xx_levantam_apos_salvar_o_que_veio(dfe, rest):
    _, nota = nfse_xml(dps_minima())
    rejeicao = RespostaHttp(403, b'{"erros":[{"codigo":"E4007"}]}')
    rest.respostas = [resposta_adn([(1, nota)]), rejeicao]
    with pytest.raises(RejeicaoNfseErro, match="E4007"):
        dfe.nfse.sincronizar()
    assert dfe.estado_nsu("nfse")["ult_nsu"] == "000000000000001"
    rest.respostas = [RespostaHttp(503, b"")]
    with pytest.raises(ComunicacaoErro, match="503"):
        dfe.nfse.sincronizar()


def test_cnpj_de_consulta_tem_sequencia_propria(dfe, rest, tmp_path):
    rest.respostas = [resposta_adn_vazia()]
    dfe.nfse.sincronizar(cnpj_consulta=CNPJ_TOMADOR)
    assert urls(rest)[0] == f"/DFe/0?tipoNSU=DISTRIBUICAO&lote=true&cnpjConsulta={CNPJ_TOMADOR}"
    estado = tmp_path / "homologacao" / CNPJ_PRESTADOR / "_estado"
    assert (estado / f"nsu_nfse_{CNPJ_TOMADOR}.json").exists()
    assert not (estado / "nsu_nfse.json").exists()
    with pytest.raises(ValueError, match="inválido"):
        dfe.nfse.sincronizar(cnpj_consulta="123")


def test_trava_impede_sincronizacao_concorrente(dfe, rest, tmp_path):
    trava = tmp_path / "homologacao" / CNPJ_PRESTADOR / "_estado" / "nsu_nfse.lock"
    trava.parent.mkdir(parents=True)
    trava.write_text("pid=1")
    with pytest.raises(SincronizacaoEmAndamentoErro):
        dfe.nfse.sincronizar()
    assert rest.enviados == []


def test_sincronizar_nfse_pela_fachada(dfe, rest):
    chave, nota = nfse_xml(dps_minima())
    rest.respostas = [resposta_adn([(1, nota)]), resposta_adn_vazia()]
    [resultado] = dfe.sincronizar(["nfse"])
    assert resultado.modelo.value == "nfse" and resultado.documentos == 1
    # A sincronização padrão continua sem NFS-e (não consulta o ADN).
    assert FreeDFe._modelos(None) and "nfse" not in [m.value for m in FreeDFe._modelos(None)]
