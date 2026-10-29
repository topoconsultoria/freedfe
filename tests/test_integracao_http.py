"""Integração por sockets reais: HTTPS com mTLS, proxy CONNECT e o fluxo completo da NFS-e.

Complementa os testes com transporte falso: aqui o ``ClienteRest`` (e o ``ClienteSoap``) falam com
um servidor HTTPS local que **exige certificado cliente**, de modo que handshake, certificado,
cabeçalhos, timeouts e túnel de proxy são exercitados de verdade, sem a rede da Fazenda.
"""

import base64
import datetime as dt
import os
import ssl
import time
from pathlib import Path

import pytest

from freedfe import Ambiente, FiltroConsulta, FreeDFe, SituacaoDocumento
from freedfe.certificado import CertificadoA1
from freedfe.excecoes import ComunicacaoErro, RejeicaoNfseErro
from freedfe.nfse.estado_emissao import SituacaoEmissao
from freedfe.nfse.leiaute import VersaoLeiaute
from freedfe.nfse.servico import ConfiguracaoNfseLocal
from freedfe.nfse.validador_xsd import ValidadorXsd
from freedfe.transporte import ClienteRest, ClienteSoap
from freedfe.validadores import validar_chave_nfse
from servidor_sefin_falso import CAMINHO_SEFIN, ProxyConnectFalso, ServidorSefinFalso

from conftest import SENHA_TESTE, RelogioFalso, gerar_pfx
from fabrica_nfse import CNPJ_PRESTADOR, DATA_EMISSAO, MUNICIPIO, dps_minima

JUSTIFICATIVA = "Servico nao prestado conforme contrato"


def pem_do_certificado(certificado: CertificadoA1) -> bytes:
    with certificado.arquivos_pem() as (caminho_cert, _):
        return Path(caminho_cert).read_bytes()


@pytest.fixture
def servidor(certificado):
    with ServidorSefinFalso([pem_do_certificado(certificado)]) as instancia:
        yield instancia


@pytest.fixture
def relogio():
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


def criar_dfe(tmp_path, certificado, servidor, relogio, nome="principal", **opcoes) -> FreeDFe:
    config = ConfiguracaoNfseLocal(
        configuracao_api=opcoes.pop("configuracao", servidor.configuracao),
        esperas_consulta=(0,), pausa=lambda segundos: None, pausa_entre_lotes_adn=0,
        **opcoes)
    return FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                   pasta=tmp_path / nome, ca_bundle=servidor.ca_bundle, proxy=None,
                   relogio=relogio, config_nfse=config)


@pytest.fixture
def dfe(tmp_path, certificado, servidor, relogio):
    return criar_dfe(tmp_path, certificado, servidor, relogio)


def situacao(dfe) -> SituacaoDocumento:
    return dfe.consultar(FiltroConsulta(modelos=["nfse"]))[0].situacao


# ---------------------------------------------------------------------------
# Transporte: mTLS, cabeçalhos e falhas de handshake
# ---------------------------------------------------------------------------

def cliente(certificado, servidor, **extra) -> ClienteRest:
    return ClienteRest(certificado, ca_bundle=servidor.ca_bundle, proxy=None, timeout=5, **extra)


def url(servidor, caminho: str) -> str:
    return f"https://localhost:{servidor.porta}{caminho}"


def test_certificado_cliente_chega_ao_servidor_e_a_resposta_volta(certificado, servidor):
    resposta = cliente(certificado, servidor).requisitar(
        "GET", url(servidor, f"{CAMINHO_SEFIN}/nfse/{'0' * 50}"))
    assert resposta.status == 404  # 4xx não levanta exceção
    recebida = servidor.requisicoes[0]
    assert recebida.cn_cliente.startswith("EMPRESA TESTE:") and recebida.cn_cliente.endswith(
        CNPJ_PRESTADOR)
    assert recebida.cabecalhos["accept"] == "application/json"
    assert recebida.cabecalhos["user-agent"].startswith("freedfe/")
    assert resposta.cabecalho("Content-Type") == "application/json"


def test_head_e_post_json_em_sockets_reais(certificado, servidor):
    http = cliente(certificado, servidor)
    head = http.requisitar("HEAD", url(servidor, f"{CAMINHO_SEFIN}/dps/DPS{'0' * 42}"))
    assert head.status == 404 and head.corpo == b""
    post = http.requisitar("POST", url(servidor, f"{CAMINHO_SEFIN}/nfse"), b'{"outro": 1}',
                           {"X-Teste": "1"})
    assert post.status == 500  # campo JSON errado = 500 mudo, como na Sefin
    enviada = servidor.chamadas("POST")[0]
    assert enviada.cabecalhos["content-type"] == "application/json"
    assert enviada.cabecalhos["x-teste"] == "1" and enviada.corpo == b'{"outro": 1}'


def test_certificado_cliente_nao_confiavel_e_recusado_no_handshake(servidor):
    intruso = CertificadoA1(gerar_pfx("99888777000166"), SENHA_TESTE)
    with pytest.raises(ComunicacaoErro) as erro:
        cliente(intruso, servidor).requisitar("GET", url(servidor, f"{CAMINHO_SEFIN}/nfse/x"))
    assert servidor.requisicoes == []  # nada chegou à camada HTTP
    assert isinstance(erro.value.__cause__, OSError)
    assert "localhost" in str(erro.value)


def test_servidor_com_ca_desconhecida_sugere_o_ca_bundle(certificado, servidor):
    sem_bundle = ClienteRest(certificado, proxy=None, timeout=5)  # só as ACs do sistema
    with pytest.raises(ComunicacaoErro, match="ca_bundle") as erro:
        sem_bundle.requisitar("GET", url(servidor, "/"))
    assert isinstance(erro.value.__cause__, ssl.SSLCertVerificationError)


def test_timeout_vira_comunicacao_erro(certificado, servidor):
    servidor.atraso_geral = 2
    inicio = time.monotonic()
    with pytest.raises(ComunicacaoErro, match="timed out") as erro:
        cliente(certificado, servidor).requisitar(
            "GET", url(servidor, f"{CAMINHO_SEFIN}/nfse/x"), timeout=1)
    assert isinstance(erro.value.__cause__, TimeoutError) and time.monotonic() - inicio < 2


def test_conexao_recusada_vira_comunicacao_erro(certificado, servidor, tmp_path):
    porta = servidor.porta
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(Path(servidor.ca_bundle).read_bytes())
    servidor.__exit__()
    with pytest.raises(ComunicacaoErro, match="localhost"):
        ClienteRest(certificado, ca_bundle=str(bundle), proxy=None, timeout=2).requisitar(
            "GET", f"https://localhost:{porta}/")


def test_cliente_soap_usa_o_mesmo_mtls(certificado, servidor):
    servidor.status_forcado[("POST", "/soap")] = (
        200, b'<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"/>')
    soap = ClienteSoap(certificado, ca_bundle=servidor.ca_bundle, proxy=None, timeout=5)
    corpo = soap.enviar(url(servidor, "/soap"), b"<x/>", "acao")
    assert b"Envelope" in corpo
    assert servidor.requisicoes[0].cn_cliente.endswith(CNPJ_PRESTADOR)
    assert "action=\"acao\"" in servidor.requisicoes[0].cabecalhos["content-type"]


# ---------------------------------------------------------------------------
# Proxy com túnel CONNECT: o mTLS continua fim a fim
# ---------------------------------------------------------------------------

def test_proxy_explicito_repassa_o_mtls_pelo_tunel(certificado, servidor):
    with ProxyConnectFalso() as proxy:
        http = ClienteRest(certificado, ca_bundle=servidor.ca_bundle, proxy=proxy.url, timeout=5)
        resposta = http.requisitar("GET", url(servidor, f"{CAMINHO_SEFIN}/nfse/{'0' * 50}"))
    assert resposta.status == 404
    assert proxy.conexoes == [(f"localhost:{servidor.porta}", None)]
    # O certificado cliente atravessou o túnel até o servidor: o proxy não terminou o TLS.
    assert servidor.requisicoes[0].cn_cliente.endswith(CNPJ_PRESTADOR)


def test_proxy_com_credenciais_envia_proxy_authorization(certificado, servidor):
    esperado = "Basic " + base64.b64encode(b"usuario:s3nh@").decode()
    with ProxyConnectFalso(credencial=esperado) as proxy:
        endereco = proxy.url.replace("http://", "http://usuario:s3nh%40@")
        http = ClienteRest(certificado, ca_bundle=servidor.ca_bundle, proxy=endereco, timeout=5)
        assert http.requisitar("HEAD", url(servidor, f"{CAMINHO_SEFIN}/dps/x")).status == 404
        sem_credencial = ClienteRest(certificado, ca_bundle=servidor.ca_bundle, proxy=proxy.url,
                                     timeout=5)
        with pytest.raises(ComunicacaoErro, match="recusou o túnel") as erro:
            sem_credencial.requisitar("HEAD", url(servidor, f"{CAMINHO_SEFIN}/dps/x"))
    assert "s3nh" not in str(erro.value) and "407" in str(erro.value)


def test_proxy_que_recusa_o_connect_explica_o_motivo(certificado, servidor):
    with ProxyConnectFalso(recusar=True) as proxy:
        http = ClienteRest(certificado, ca_bundle=servidor.ca_bundle, proxy=proxy.url, timeout=5)
        with pytest.raises(ComunicacaoErro, match="recusou o túnel CONNECT") as erro:
            http.requisitar("GET", url(servidor, "/"))
    assert "403" in str(erro.value) and servidor.requisicoes == []


def test_proxy_automatico_le_https_proxy_e_respeita_no_proxy(certificado, servidor, monkeypatch):
    with ProxyConnectFalso() as proxy:
        monkeypatch.setenv("HTTPS_PROXY", proxy.url)
        http = ClienteRest(certificado, ca_bundle=servidor.ca_bundle, timeout=5)  # "auto"
        assert http.requisitar("HEAD", url(servidor, f"{CAMINHO_SEFIN}/dps/x")).status == 404
        assert len(proxy.conexoes) == 1
        monkeypatch.setenv("NO_PROXY", "localhost")
        assert http.requisitar("HEAD", url(servidor, f"{CAMINHO_SEFIN}/dps/x")).status == 404
        assert len(proxy.conexoes) == 1  # NO_PROXY: conexão direta
    assert len(servidor.requisicoes) == 2


def test_fachada_aceita_proxy_para_os_dois_transportes(tmp_path, certificado, servidor):
    with ProxyConnectFalso() as proxy:
        dfe = FreeDFe(certificado, cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO,
                      pasta=tmp_path, ca_bundle=servidor.ca_bundle, proxy=proxy.url,
                      config_nfse=ConfiguracaoNfseLocal(configuracao_api=servidor.configuracao,
                                                        esperas_consulta=()))
        servidor.convenios[MUNICIPIO] = {"aderente_ambiente_nacional": True}
        assert dfe.nfse.convenio(MUNICIPIO).aderente is True
    assert len(proxy.conexoes) == 1


# ---------------------------------------------------------------------------
# Fluxo completo da NFS-e contra o servidor local
# ---------------------------------------------------------------------------

def test_emissao_ponta_a_ponta(dfe, servidor):
    retorno = dfe.nfse.emitir(dps_minima(numero=11))
    assert validar_chave_nfse(retorno.chave)
    post = servidor.chamadas("POST", f"{CAMINHO_SEFIN}/nfse")
    assert len(post) == 1 and list(post[0].json) == ["dpsXmlGZipB64"]
    assert post[0].cn_cliente.endswith(CNPJ_PRESTADOR)
    # A DPS saiu assinada (o servidor confere a assinatura criptográfica) e a nota foi gravada.
    assert situacao(dfe) is SituacaoDocumento.AUTORIZADO
    assert [(e.situacao, e.chave) for e in dfe.nfse.emissoes()] == [
        (SituacaoEmissao.AUTORIZADA, retorno.chave)]
    assert retorno.chave in servidor.nfses


def test_emissao_valida_contra_o_xsd_oficial_quando_disponivel(tmp_path, certificado, relogio):
    pasta = os.environ.get("FREEDFE_XSD_NFSE_NT009")
    if not pasta:
        pytest.skip("defina FREEDFE_XSD_NFSE_NT009 com a pasta dos XSD oficiais da NT 009")
    validador = ValidadorXsd(pasta)
    with ServidorSefinFalso([pem_do_certificado(certificado)], validador_xsd=validador) as srv:
        dfe = criar_dfe(tmp_path, certificado, srv, relogio, leiaute=VersaoLeiaute.NT009_V101)
        assert validar_chave_nfse(dfe.nfse.emitir(dps_minima(numero=12)).chave)


def test_rejeicao_de_negocio_vira_excecao_e_nao_deixa_nota(dfe, servidor):
    servidor.proxima_rejeicao = ("E0715", 400)
    with pytest.raises(RejeicaoNfseErro) as erro:
        dfe.nfse.emitir(dps_minima(numero=13))
    assert erro.value.codigos == ["E0715"] and servidor.nfses == {}
    assert [e.situacao for e in dfe.nfse.emissoes()] == [SituacaoEmissao.REJEITADA]
    assert dfe.consultar(FiltroConsulta(modelos=["nfse"])) == []


def test_timeout_na_emissao_reconcilia_sem_segundo_post(tmp_path, certificado, servidor, relogio):
    servidor.atraso_emissao = 3  # a nota é registrada e a resposta só sai depois do timeout
    config = servidor.configuracao.com(timeout_emissao=1)
    dfe = criar_dfe(tmp_path, certificado, servidor, relogio, configuracao=config)
    retorno = dfe.nfse.emitir(dps_minima(numero=14))
    assert retorno.chave in servidor.nfses
    assert len(servidor.chamadas("POST", f"{CAMINHO_SEFIN}/nfse")) == 1  # nunca repete o POST
    assert servidor.chamadas("HEAD", f"{CAMINHO_SEFIN}/dps/")  # reconciliou por HEAD /dps/{id}
    assert situacao(dfe) is SituacaoDocumento.AUTORIZADO


def test_dps_ja_emitida_e0014_devolve_a_nota_existente(tmp_path, certificado, servidor, relogio):
    primeira = criar_dfe(tmp_path, certificado, servidor, relogio, nome="a")
    emitida = primeira.nfse.emitir(dps_minima(numero=15))
    # Outra instalação (sem o estado local) reenvia a mesma DPS: E0014, reconciliação, mesma nota.
    segunda = criar_dfe(tmp_path, certificado, servidor, relogio, nome="b")
    repetida = segunda.nfse.emitir(dps_minima(numero=15))
    assert repetida.chave == emitida.chave and len(servidor.nfses) == 1
    assert len(servidor.chamadas("POST", f"{CAMINHO_SEFIN}/nfse")) == 2


def test_cancelamento_ponta_a_ponta(dfe, servidor):
    chave = dfe.nfse.emitir(dps_minima(numero=16)).chave
    retorno = dfe.nfse.cancelar(chave, 2, JUSTIFICATIVA)
    assert retorno is not None and situacao(dfe) is SituacaoDocumento.CANCELADO
    evento = servidor.chamadas("POST", f"{CAMINHO_SEFIN}/nfse/{chave}/eventos")[0]
    assert list(evento.json) == ["pedidoRegistroEventoXmlGZipB64"]
    with pytest.raises(Exception) as erro:  # já cancelada: recusado localmente (E0840)
        dfe.nfse.cancelar(chave, 2, JUSTIFICATIVA)
    assert "E0840" in str(erro.value) or "cancelad" in str(erro.value).lower()
    assert len(servidor.chamadas("POST", f"{CAMINHO_SEFIN}/nfse/{chave}/eventos")) == 1


def test_cancelamento_de_nota_inexistente_e_rejeitado_pela_sefin(dfe):
    chave = dfe.nfse.emitir(dps_minima(numero=17)).chave
    outra = chave[:-14] + "9" * 13 + chave[-1]
    with pytest.raises(RejeicaoNfseErro) as erro:
        dfe.nfse.cancelar(outra, 1, JUSTIFICATIVA)
    assert "E0801" in erro.value.codigos


def test_sincronizacao_adn_traz_notas_e_eventos_e_e_incremental(
        tmp_path, certificado, servidor, relogio):
    emissor = criar_dfe(tmp_path, certificado, servidor, relogio, nome="emissor")
    chave = emissor.nfse.emitir(dps_minima(numero=18)).chave
    emissor.nfse.emitir(dps_minima(numero=19))
    emissor.nfse.cancelar(chave, 1, JUSTIFICATIVA)
    espelho = criar_dfe(tmp_path, certificado, servidor, relogio, nome="espelho",
                        bloqueio_adn_segundos=0)
    resultado = espelho.nfse.sincronizar()
    assert resultado.documentos == 3 and int(resultado.ult_nsu) == 3
    consulta = [r for r in servidor.chamadas("GET", "/adn/contribuintes/DFe/")]
    assert "tipoNSU=DISTRIBUICAO" in consulta[0].caminho and "lote=true" in consulta[0].caminho
    notas = {r.chave: r.situacao for r in espelho.consultar(FiltroConsulta(modelos=["nfse"]))}
    assert notas[chave] is SituacaoDocumento.CANCELADO and len(notas) == 2
    antes = len(servidor.requisicoes)
    assert espelho.nfse.sincronizar().documentos == 0  # retoma do último NSU
    assert servidor.requisicoes[antes].caminho.split("?")[0].endswith("/DFe/3")


def test_convenio_e_cache_ponta_a_ponta(dfe, servidor):
    servidor.convenios[MUNICIPIO] = {"aderente_ambiente_nacional": True,
                                     "dataAtivacao": "2026-01-01"}
    assert dfe.nfse.convenio(MUNICIPIO).aderente is True
    dfe.nfse.convenio(MUNICIPIO)  # segundo acesso vem do cache em disco
    assert len(servidor.chamadas("GET", "/adn/parametrizacao")) == 1


def test_danfse_da_nota_emitida_tem_qr_para_a_consulta_publica(dfe, servidor, tmp_path):
    chave = dfe.nfse.emitir(dps_minima(numero=20)).chave
    caminho = dfe.nfse.gerar_danfse(chave, tmp_path / "nota.pdf")
    assert caminho.read_bytes().startswith(b"%PDF")
    assert dfe.nfse.configuracao.url_consulta_publica(chave) == (
        f"https://localhost:{servidor.porta}/ConsultaPublica/?tpc=1&chave={chave}")
