"""Resolução de proxy dos transportes (sem sockets) e mensagens de falha de TLS."""

import base64
import ssl

import pytest

from freedfe.excecoes import ValidacaoErro
from freedfe.transporte import (
    PROXY_AUTOMATICO,
    ProxyHttp,
    abrir_conexao,
    descrever_falha,
    resolver_proxy,
)

DESTINO = "sefin.producaorestrita.nfse.gov.br"


def test_sem_proxy_nao_resolve_nada(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    assert resolver_proxy(None, DESTINO) is None and resolver_proxy("", DESTINO) is None


def test_automatico_sem_variavel_e_conexao_direta():
    assert resolver_proxy(PROXY_AUTOMATICO, DESTINO) is None


def test_automatico_le_https_proxy_do_ambiente(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:8080")
    assert resolver_proxy(PROXY_AUTOMATICO, DESTINO) == ProxyHttp("proxy.local", 8080)


def test_automatico_respeita_no_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:8080")
    monkeypatch.setenv("NO_PROXY", ".nfse.gov.br")
    assert resolver_proxy(PROXY_AUTOMATICO, DESTINO) is None
    assert resolver_proxy(PROXY_AUTOMATICO, "outro.exemplo.com") is not None


def test_url_explicita_com_credenciais_codificadas():
    proxy = resolver_proxy("http://us%40er:p%3Ass@proxy.local", DESTINO)
    assert (proxy.host, proxy.porta) == ("proxy.local", 3128)  # porta padrão do Squid
    esperado = "Basic " + base64.b64encode(b"us@er:p:ss").decode()
    assert proxy.cabecalhos_tunel() == {"Proxy-Authorization": esperado}
    assert "p:ss" not in repr(proxy) and "p%3Ass" not in repr(proxy) and str(proxy) == (
        "proxy.local:3128")


def test_proxy_sem_esquema_assume_http():
    assert resolver_proxy("proxy.local:3128", DESTINO) == ProxyHttp("proxy.local", 3128)


@pytest.mark.parametrize("proxy", ["https://proxy.local:3128", "socks5://proxy.local:1080",
                                   "http://"])
def test_so_aceita_proxy_http(proxy):
    with pytest.raises(ValidacaoErro, match="proxy http"):
        resolver_proxy(proxy, DESTINO)


def test_abrir_conexao_direta_e_via_tunel():
    contexto = ssl.create_default_context()
    direta = abrir_conexao(DESTINO, 443, contexto, 7, None)
    assert (direta.host, direta.port, direta.timeout) == (DESTINO, 443, 7)
    assert direta._tunnel_host is None
    via = abrir_conexao(DESTINO, 443, contexto, 7, "http://u:s@proxy.local:3128")
    assert (via.host, via.port) == ("proxy.local", 3128)
    assert (via._tunnel_host, via._tunnel_port) == (DESTINO, 443)
    assert "Proxy-Authorization" in via._tunnel_headers


def test_mensagens_de_falha_orientam_sem_vazar_segredos():
    verificacao = ssl.SSLCertVerificationError("self-signed certificate")
    assert "ca_bundle" in descrever_falha(DESTINO, "GET", verificacao, None)
    handshake = ssl.SSLError("TLSV1_ALERT_UNKNOWN_CA")
    assert "certificado cliente ICP-Brasil" in descrever_falha(DESTINO, "POST", handshake, None)
    assert "encerrou a conexão" in descrever_falha(
        DESTINO, "GET", ConnectionResetError(104, "Connection reset by peer"), None)
    tunel = OSError("Tunnel connection failed: 403 Forbidden")
    mensagem = descrever_falha(DESTINO, "GET", tunel, "http://u:segredo@proxy.local:3128")
    assert "proxy.local:3128" in mensagem and "segredo" not in mensagem
    assert "Tunnel connection failed" in descrever_falha(DESTINO, "GET", tunel, None)
    assert descrever_falha(DESTINO, "GET", TimeoutError("timed out"), None).endswith(
        "timed out")
