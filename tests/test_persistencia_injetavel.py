"""Persistência injetável da NFS-e: estado de emissão, travas e NSU do ADN fora de arquivos.

Cobre os contratos ``EstadoEmissao``, ``Trava`` e ``EstadoSequenciaNsu`` com implementações em
memória (``memoria_nfse``), o uso delas por ``FreeDFe``/``ServicoNfse`` contra o
``ServidorSefinFalso`` e o descarte do número reservado quando a DPS falha antes do envio.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import pytest
from lxml import etree

from freedfe import Ambiente, FreeDFe, SituacaoEmissao
from freedfe.distribuicao.estado import BLOQUEIO_SEGUNDOS, EstadoNSU, EstadoSequenciaNsu
from freedfe.distribuicao.trava import Trava, TravaArquivo
from freedfe.excecoes import (
    FreeDFeErro,
    LimiteConsultaErro,
    RejeicaoNfseErro,
    SincronizacaoEmAndamentoErro,
    ValidacaoErro,
)
from freedfe.nfse.envelope import descomprimir_b64
from freedfe.nfse.estado_emissao import EstadoEmissaoArquivo
from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.nfse.servico import ConfiguracaoNfseLocal
from freedfe.transporte import RespostaHttp
from servidor_sefin_falso import CAMINHO_SEFIN, ServidorSefinFalso

from conftest import RelogioFalso, TransporteRestFalso
from fabrica_nfse import (
    CNPJ_PRESTADOR,
    CNPJ_TOMADOR,
    DATA_EMISSAO,
    dps_minima,
    nfse_xml,
    resposta_emissao,
    resposta_erro,
)
from memoria_nfse import EstadoEmissaoMemoria, EstadoNsuMemoria, TravaMemoria, TravasMemoria

NS = {"n": NAMESPACE_NFSE}
XSD_FORA_DO_SCHEMA = (
    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    f'targetNamespace="{NAMESPACE_NFSE}" xmlns="{NAMESPACE_NFSE}" '
    'elementFormDefault="qualified"><xs:element name="DPS"><xs:complexType><xs:sequence>'
    '<xs:element name="infDPS" type="xs:string"/></xs:sequence></xs:complexType>'
    "</xs:element></xs:schema>")


@pytest.fixture
def relogio():
    """Cinco minutos depois da emissão fixa das fábricas."""
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


@pytest.fixture
def servidor(certificado):
    with certificado.arquivos_pem() as (caminho_cert, _):
        pem = Path(caminho_cert).read_bytes()
    with ServidorSefinFalso([pem]) as instancia:
        yield instancia


@pytest.fixture
def memoria(relogio):
    """Estado de emissão, travas e armazenamento de NSU em memória."""
    return {"estado": EstadoEmissaoMemoria(relogio), "travas": TravasMemoria(), "nsu": {}}


def injecao(memoria, relogio, bloqueio_nsu: int = 0) -> dict:
    """Argumentos de ``FreeDFe`` que trocam os três arquivos de estado pela memória."""
    return dict(estado_emissao_nfse=memoria["estado"], travas_nfse=memoria["travas"],
                estado_nsu_nfse=EstadoNsuMemoria.fabrica(memoria["nsu"], relogio, bloqueio_nsu))


def dfe_no_servidor(tmp_path, certificado, servidor, relogio, **injetado) -> FreeDFe:
    config = ConfiguracaoNfseLocal(
        configuracao_api=servidor.configuracao, esperas_consulta=(0,),
        pausa=lambda segundos: None, pausa_entre_lotes_adn=0, bloqueio_adn_segundos=0)
    return FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                   pasta=tmp_path, ca_bundle=servidor.ca_bundle, proxy=None, relogio=relogio,
                   config_nfse=config, **injetado)


def dfe_com_transporte_falso(pasta, certificado, relogio, rest, config=None,
                             **injetado) -> FreeDFe:
    return FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                   pasta=pasta, transporte_rest=rest, relogio=relogio,
                   config_nfse=config or ConfiguracaoNfseLocal(esperas_consulta=()), **injetado)


def dps_enviada(rest, indice: int = 0) -> bytes:
    """XML da DPS contido no corpo do n-ésimo POST."""
    return descomprimir_b64(json.loads(rest.chamadas("POST")[indice][2])["dpsXmlGZipB64"])


def head(status: int) -> RespostaHttp:
    return RespostaHttp(status, b"")


def ndps(xml: bytes) -> int:
    return int(etree.fromstring(xml).find("n:infDPS/n:nDPS", NS).text)


# ---------------------------------------------------------------------------
# Emissão e sincronização com estado, trava e NSU em memória
# ---------------------------------------------------------------------------

def test_emissao_completa_com_estado_e_trava_em_memoria(
        tmp_path, certificado, servidor, relogio, memoria):
    dfe = dfe_no_servidor(tmp_path, certificado, servidor, relogio, **injecao(memoria, relogio))

    retorno = dfe.nfse.emitir(dps_minima(numero=None))

    pasta_estado = dfe.pasta / "_estado"
    assert not (pasta_estado / "emissoes_nfse.json").exists()
    assert not (pasta_estado / "emissao_nfse.lock").exists()
    assert memoria["travas"].eventos == [("adquirida", "emissao_nfse"),
                                         ("liberada", "emissao_nfse")]
    registro = memoria["estado"].obter(retorno.id_dps)
    assert registro.situacao is SituacaoEmissao.AUTORIZADA and registro.chave == retorno.chave
    assert (registro.serie, registro.numero, registro.tentativas) == (900, 1, 1)
    assert [r.id_dps for r in dfe.nfse.emissoes()] == [retorno.id_dps]  # lido da memória
    assert len(servidor.chamadas("POST", f"{CAMINHO_SEFIN}/nfse")) == 1
    assert list(servidor.dps_para_chave) == [retorno.id_dps]


def test_emissoes_seguidas_numeram_pela_memoria(tmp_path, certificado, servidor, relogio, memoria):
    dfe = dfe_no_servidor(tmp_path, certificado, servidor, relogio, **injecao(memoria, relogio))
    ids = [dfe.nfse.emitir(dps_minima(numero=None)).id_dps for _ in range(3)]
    assert [int(i[30:45]) for i in ids] == [1, 2, 3]
    assert [r.numero for r in memoria["estado"].listar()] == [1, 2, 3]
    assert memoria["travas"].recursos("adquirida") == ["emissao_nfse"] * 3
    assert memoria["travas"].recursos("liberada") == ["emissao_nfse"] * 3


def test_trava_injetada_ocupada_impede_a_emissao_sem_tocar_no_estado(
        tmp_path, certificado, servidor, relogio, memoria):
    dfe = dfe_no_servidor(tmp_path, certificado, servidor, relogio, **injecao(memoria, relogio))
    with memoria["travas"]("emissao_nfse"):  # "outro processo" segurando a emissão
        with pytest.raises(SincronizacaoEmAndamentoErro):
            dfe.nfse.emitir(dps_minima(numero=None))
    assert memoria["estado"].listar() == [] and servidor.requisicoes == []
    assert dfe.nfse.emitir(dps_minima(numero=None)).id_dps.endswith("000000000000001")


def test_sincronizacao_usa_a_fabrica_de_nsu_e_a_de_travas(
        tmp_path, certificado, servidor, relogio, memoria):
    dfe = dfe_no_servidor(tmp_path, certificado, servidor, relogio, **injecao(memoria, relogio))
    dfe.nfse.emitir(dps_minima(numero=None))

    resultado = dfe.nfse.sincronizar()

    assert resultado.documentos == 1 and resultado.ult_nsu == "000000000000001"
    assert memoria["nsu"]["nsu_nfse"]["ult_nsu"] == "000000000000001"
    assert ("adquirida", "nsu_nfse") in memoria["travas"].eventos
    assert ("liberada", "nsu_nfse") in memoria["travas"].eventos
    pasta_estado = dfe.pasta / "_estado"
    assert not (pasta_estado / "nsu_nfse.json").exists()
    assert not (pasta_estado / "nsu_nfse.lock").exists()
    # A fachada lê a mesma sequência pela fábrica de NSU da NFS-e.
    assert dfe.estado_nsu("nfse")["ult_nsu"] == "000000000000001"
    assert dfe.sincronizar(["nfse"])[0].documentos == 0  # retoma do NSU gravado em memória


def test_sincronizacao_com_cnpj_de_consulta_usa_o_recurso_do_cnpj(
        tmp_path, certificado, servidor, relogio, memoria):
    dfe = dfe_no_servidor(tmp_path, certificado, servidor, relogio, **injecao(memoria, relogio))
    dfe.nfse.emitir(dps_minima(numero=None))

    dfe.nfse.sincronizar(cnpj_consulta=CNPJ_TOMADOR)

    recurso = f"nsu_nfse_{CNPJ_TOMADOR}"
    assert list(memoria["nsu"]) == [recurso]
    assert ("adquirida", recurso) in memoria["travas"].eventos
    assert memoria["nsu"][recurso]["ult_nsu"] == "000000000000001"


def test_padrao_em_arquivo_continua_usando_os_mesmos_arquivos(
        tmp_path, certificado, servidor, relogio):
    dfe = dfe_no_servidor(tmp_path, certificado, servidor, relogio)
    dfe.nfse.emitir(dps_minima(numero=None))
    dfe.nfse.sincronizar()
    dfe.nfse.sincronizar(cnpj_consulta=CNPJ_TOMADOR)
    pasta_estado = dfe.pasta / "_estado"
    assert sorted(p.name for p in pasta_estado.iterdir()) == [
        "chamadas.jsonl", "emissoes_nfse.json", "nsu_nfse.json",
        f"nsu_nfse_{CNPJ_TOMADOR}.json"]  # nenhuma trava ficou para trás
    assert dfe.estado_nsu("nfse")["ult_nsu"] == "000000000000001"
    emissoes = json.loads((pasta_estado / "emissoes_nfse.json").read_text())["emissoes"]
    assert [e["numero"] for e in emissoes.values()] == [1]


# ---------------------------------------------------------------------------
# Falha local depois de reservar_proximo: o número não é consumido
# ---------------------------------------------------------------------------

@pytest.fixture(params=["arquivo", "memoria"])
def backend(request, tmp_path, relogio):
    """``(argumentos de injeção, função que lista os números gravados)`` de cada backend.

    O padrão em arquivo não injeta nada; o JSON fica na pasta ``dados`` do teste.
    """
    if request.param == "memoria":
        estado = EstadoEmissaoMemoria(relogio)
        return {"estado_emissao_nfse": estado}, lambda: [r.numero for r in estado.listar()]
    arquivo = tmp_path / "dados" / "homologacao" / CNPJ_PRESTADOR / "_estado" / "emissoes_nfse.json"
    return {}, lambda: [r.numero for r in EstadoEmissaoArquivo(arquivo, relogio).listar()]


def test_xsd_invalido_nao_consome_o_numero(tmp_path, certificado, relogio, backend):
    injetado, numeros_no_estado = backend
    (tmp_path / "xsd").mkdir()
    (tmp_path / "xsd" / "DPS_v1.01.xsd").write_text(XSD_FORA_DO_SCHEMA)
    rest = TransporteRestFalso()
    com_xsd = dfe_com_transporte_falso(
        tmp_path / "dados", certificado, relogio, rest, **injetado,
        config=ConfiguracaoNfseLocal(pasta_xsd={VersaoLeiaute.NT009_V101: tmp_path / "xsd"}))

    with pytest.raises(ValidacaoErro, match="fora do schema"):
        com_xsd.nfse.emitir(dps_minima(numero=None))

    assert rest.enviados == [] and numeros_no_estado() == []
    # A próxima emissão (sem o XSD problemático) reaproveita o mesmo número.
    dps = dps_minima(numero=None)
    chave, xml = nfse_xml(dps)
    rest.respostas = [resposta_emissao(xml, chave)]
    sem_xsd = dfe_com_transporte_falso(tmp_path / "dados", certificado, relogio, rest, **injetado)
    retorno = sem_xsd.nfse.emitir(dps)
    assert retorno.id_dps.endswith("000000000000001") and ndps(dps_enviada(rest)) == 1
    assert numeros_no_estado() == [1]


def test_falha_ao_gravar_a_evidencia_devolve_o_numero(tmp_path, certificado, relogio, memoria):
    rest = TransporteRestFalso()
    dfe = dfe_com_transporte_falso(tmp_path, certificado, relogio, rest,
                                   **injecao(memoria, relogio))
    dfe.pasta.mkdir(parents=True)
    (dfe.pasta / "nfse").write_text("arquivo no lugar da pasta de evidências")

    with pytest.raises(OSError):
        dfe.nfse.emitir(dps_minima(numero=None))

    assert rest.enviados == [] and memoria["estado"].listar() == []
    assert memoria["estado"].reservar_proximo(900, lambda n: f"X{n}").numero == 1


def test_rejeicao_depois_do_envio_mantem_a_reserva_e_consome_o_numero(
        tmp_path, certificado, relogio, memoria):
    rest = TransporteRestFalso([resposta_erro("E0712")])
    dfe = dfe_com_transporte_falso(tmp_path, certificado, relogio, rest,
                                   **injecao(memoria, relogio))
    with pytest.raises(RejeicaoNfseErro):
        dfe.nfse.emitir(dps_minima(numero=None))
    [registro] = memoria["estado"].listar()
    assert registro.situacao is SituacaoEmissao.REJEITADA and registro.tentativas == 1
    assert memoria["estado"].proximo_numero(900) == 2  # evidência preservada: não reaproveita


def test_falha_ao_descartar_vai_para_o_log_e_o_erro_original_e_relancado(
        tmp_path, certificado, relogio, caplog):
    class EstadoQueFalhaAoDescartar(EstadoEmissaoMemoria):
        def descartar(self, id_dps):
            raise RuntimeError("banco indisponível")

    estado = EstadoQueFalhaAoDescartar(relogio)
    (tmp_path / "xsd").mkdir()
    (tmp_path / "xsd" / "DPS_v1.01.xsd").write_text(XSD_FORA_DO_SCHEMA)
    dfe = dfe_com_transporte_falso(
        tmp_path / "dados", certificado, relogio, TransporteRestFalso(),
        estado_emissao_nfse=estado,
        config=ConfiguracaoNfseLocal(pasta_xsd={VersaoLeiaute.NT009_V101: tmp_path / "xsd"}))

    with caplog.at_level(logging.ERROR, logger="freedfe.nfse.servico"):
        with pytest.raises(ValidacaoErro, match="fora do schema"):  # a ORIGINAL, não a do banco
            dfe.nfse.emitir(dps_minima(numero=None))

    assert any("não pôde ser descartada" in r.getMessage() and r.exc_info for r in caplog.records)
    assert [r.numero for r in estado.listar()] == [1]  # sem o descarte, a reserva permaneceu


def test_id_montado_diferente_do_reservado_e_recusado_e_devolve_o_numero(
        tmp_path, certificado, relogio, memoria, monkeypatch):
    from freedfe.nfse import servico
    from freedfe.nfse.identificadores import id_dps

    monkeypatch.setattr(servico, "montar_id_dps",
                        lambda municipio, documento, serie, numero: id_dps(
                            municipio, documento, serie, int(numero) + 1000))
    rest = TransporteRestFalso()
    dfe = dfe_com_transporte_falso(tmp_path, certificado, relogio, rest,
                                   **injecao(memoria, relogio))
    with pytest.raises(FreeDFeErro, match="difere do Id reservado"):
        dfe.nfse.emitir(dps_minima(numero=None))
    assert rest.enviados == [] and memoria["estado"].listar() == []


def test_gerar_dps_assinada_nao_reserva_numero(tmp_path, certificado, relogio, memoria):
    dfe = dfe_com_transporte_falso(tmp_path, certificado, relogio, TransporteRestFalso(),
                                   **injecao(memoria, relogio))
    primeira = dfe.nfse.gerar_dps_assinada(dps_minima(numero=None))
    segunda = dfe.nfse.gerar_dps_assinada(dps_minima(numero=None))
    assert primeira == segunda and ndps(primeira) == 1
    assert memoria["estado"].listar() == [] and memoria["travas"].eventos == []


def test_xml_da_numeracao_automatica_e_identico_ao_do_numero_informado(
        tmp_path, certificado, relogio, memoria):
    """A reserva atômica não pode alterar a DPS gerada (inclusive a assinatura)."""
    enviados = []
    for nome, dps, injetado in (("auto", dps_minima(numero=None), injecao(memoria, relogio)),
                                ("manual", dps_minima(numero=1), {})):
        rest = TransporteRestFalso([RespostaHttp(500, b""), head(404)])
        dfe = dfe_com_transporte_falso(tmp_path / nome, certificado, relogio, rest, **injetado)
        with pytest.raises(FreeDFeErro):  # 500 + HEAD 404: incerta; só interessa o que foi enviado
            dfe.nfse.emitir(dps)
        enviados.append(dps_enviada(rest))
    assert enviados[0] == enviados[1] and ndps(enviados[0]) == 1


# ---------------------------------------------------------------------------
# Contratos Trava e EstadoSequenciaNsu
# ---------------------------------------------------------------------------

def test_trava_e_um_contrato_abstrato():
    with pytest.raises(TypeError, match="abstract"):
        Trava()
    assert issubclass(TravaArquivo, Trava) and issubclass(TravaMemoria, Trava)


def test_trava_arquivo_e_fail_fast_e_libera_apos_excecao(tmp_path):
    arquivo = tmp_path / "x.lock"
    with TravaArquivo(arquivo):
        with pytest.raises(SincronizacaoEmAndamentoErro):
            with TravaArquivo(arquivo):
                pass
        assert arquivo.exists()  # a tentativa recusada não derrubou a trava de quem a segura
    assert not arquivo.exists()
    with pytest.raises(RuntimeError, match="dentro do bloco"):
        with TravaArquivo(arquivo):
            raise RuntimeError("falha dentro do bloco")
    assert not arquivo.exists()


def test_trava_em_memoria_e_exclusiva_por_recurso_e_libera_apos_excecao():
    travas = TravasMemoria()
    with travas("a"):
        with travas("b"):  # recursos diferentes não se bloqueiam
            pass
        with pytest.raises(SincronizacaoEmAndamentoErro):
            with travas("a"):
                pass
    with pytest.raises(RuntimeError, match="dentro do bloco"):
        with travas("a"):
            raise RuntimeError("falha dentro do bloco")
    with travas("a"):  # liberada mesmo após a exceção
        pass
    assert travas.recursos("adquirida") == ["a", "b", "a", "a"]
    assert travas.recursos("liberada") == ["b", "a", "a", "a"]


def test_estado_sequencia_nsu_e_um_contrato_abstrato():
    with pytest.raises(TypeError, match="abstract"):
        EstadoSequenciaNsu()
    assert issubclass(EstadoNSU, EstadoSequenciaNsu)
    assert issubclass(EstadoNsuMemoria, EstadoSequenciaNsu)


def test_estado_nsu_em_memoria_segue_as_regras_da_base(relogio):
    armazenamento: dict = {}
    estado = EstadoNsuMemoria(armazenamento, "nsu_nfse", relogio)
    assert estado.ult_nsu == "0" * 15 and armazenamento == {}  # nada gravado antes de salvar
    estado.atualizar_sequencia("000000000000042", "000000000000050")
    estado.registrar_execucao("200")
    estado.bloquear()
    estado.salvar()
    assert armazenamento["nsu_nfse"]["ult_nsu"] == "000000000000042"

    recarregado = EstadoNsuMemoria(armazenamento, "nsu_nfse", relogio)
    assert (recarregado.ult_nsu, recarregado.max_nsu) == ("000000000000042", "000000000000050")
    assert recarregado.ultimo_cstat == "200" and recarregado.ultima_execucao
    assert recarregado.segundos_para_liberar() == BLOQUEIO_SEGUNDOS
    relogio.avancar(seconds=BLOQUEIO_SEGUNDOS)
    assert recarregado.segundos_para_liberar() == 0
    assert recarregado.como_dict()["liberado_em_s"] == 0
    outro_recurso = EstadoNsuMemoria(armazenamento, "nsu_nfse_x", relogio)
    assert outro_recurso.ult_nsu == "0" * 15


def test_estado_nsu_em_memoria_nao_retem_referencia_ao_dicionario_de_trabalho(relogio):
    armazenamento: dict = {}
    estado = EstadoNsuMemoria(armazenamento, "r", relogio)
    estado.registrar_consulta_pontual()
    estado.salvar()
    estado.registrar_consulta_pontual()  # sem salvar: o gravado não muda
    assert len(armazenamento["r"]["consultas_pontuais"]) == 1


def test_estado_nsu_em_memoria_limita_20_consultas_pontuais_por_hora(relogio):
    estado = EstadoNsuMemoria({}, "r", relogio)
    for _ in range(20):
        estado.registrar_consulta_pontual()
    with pytest.raises(LimiteConsultaErro):
        estado.registrar_consulta_pontual()


def test_estado_nsu_em_arquivo_mantem_o_formato_json(tmp_path, relogio):
    arquivo = tmp_path / "_estado" / "nsu_nfe.json"
    estado = EstadoNSU(arquivo, relogio)
    assert estado.arquivo == arquivo
    estado.atualizar_sequencia("000000000000007", "000000000000009")
    estado.registrar_execucao("137")
    estado.salvar()
    assert json.loads(arquivo.read_text()) == {
        "ult_nsu": "000000000000007", "max_nsu": "000000000000009", "bloqueado_ate": None,
        "consultas_pontuais": [], "ultimo_cstat": "137",
        "ultima_execucao": relogio().isoformat()}
    assert arquivo.read_text().startswith('{\n  "ult_nsu"')  # indent=2, como antes
    assert not list(arquivo.parent.glob("*.tmp"))
