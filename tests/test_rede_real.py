"""Verificações contra os servidores reais da NFS-e Nacional (produção restrita e produção).

**Opt-in**: só rodam com ``FREEDFE_TESTE_REDE=1``. Não usam certificado ICP-Brasil (nem precisam):
conferem o que é observável sem credenciais, para detectar mudanças de contrato na infraestrutura
que os testes com transporte falso e com o servidor local não enxergam:

* o host da Sefin responde com TLS válido e recusa (HTTP 401/403) quem não tem certificado
  ICP-Brasil; o prefixo ``/API/SefinNacional`` existe na produção restrita, mas não na produção;
* os hosts do ADN e da parametrização recusam o certificado cliente já no handshake;
* a Consulta Pública do QR Code existe nos dois ambientes e devolve a página HTML da NFS-e.

Emissão, consulta e cancelamento reais exigem o A1: use
``examples/nfse_smoke_producao_restrita.py``.
O proxy do ambiente (``HTTPS_PROXY``) é usado automaticamente.
"""

import os
import ssl
import time
import urllib.request

import pytest

from freedfe.certificado import CertificadoA1
from freedfe.excecoes import ComunicacaoErro
from freedfe.servicos import configuracao_nfse
from freedfe.tipos import Ambiente
from freedfe.transporte import ClienteRest

from conftest import SENHA_TESTE, gerar_pfx

pytestmark = [
    pytest.mark.rede,
    pytest.mark.skipif(os.environ.get("FREEDFE_TESTE_REDE") != "1",
                       reason="teste de rede: defina FREEDFE_TESTE_REDE=1"),
]

NOMES_PROXY = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy")
PROXY_DO_AMBIENTE = {n: os.environ[n] for n in NOMES_PROXY if n in os.environ}  # antes dos fixtures

CHAVE_FICTICIA = "0" * 50
ID_DPS_FICTICIO = "DPS" + "0" * 42
AMBIENTES = [Ambiente.HOMOLOGACAO, Ambiente.PRODUCAO]


@pytest.fixture(autouse=True)
def proxy_do_ambiente(monkeypatch, ambiente_sem_proxy):
    """Devolve o proxy da máquina, que o conftest remove dos demais testes (rede só via proxy)."""
    for nome, valor in PROXY_DO_AMBIENTE.items():
        monkeypatch.setenv(nome, valor)


@pytest.fixture(scope="module")
def cliente() -> ClienteRest:
    """Cliente com um certificado autoassinado: serve para provar que o servidor o recusa."""
    return ClienteRest(CertificadoA1(gerar_pfx(), SENHA_TESTE), timeout=30)


def com_novas_tentativas(operacao, tentativas: int = 4, espera: float = 3.0):
    """Os servidores gov.br resetam conexões de forma intermitente; repete antes de falhar."""
    for numero in range(1, tentativas + 1):
        try:
            return operacao()
        except (ConnectionResetError, ComunicacaoErro) as erro:
            if numero == tentativas:
                raise
            time.sleep(espera * numero)
            del erro


PROTEGIDO = (401, 403)  # aplicação existe e exige ICP-Brasil (o IIS recusa antes de rotear)
INEXISTENTE = (404,)


@pytest.mark.parametrize("ambiente, prefixo, esperado", [
    (Ambiente.HOMOLOGACAO, "", PROTEGIDO),
    (Ambiente.HOMOLOGACAO, "/API", PROTEGIDO),  # a restrita monta as duas aplicações
    (Ambiente.PRODUCAO, "", PROTEGIDO),
    (Ambiente.PRODUCAO, "/API", INEXISTENTE),  # a produção só monta /SefinNacional
], ids=["restrita", "restrita-API", "producao", "producao-API"])
def test_sefin_recusa_quem_nao_tem_certificado_e_revela_os_prefixos_montados(
        cliente, ambiente, prefixo, esperado):
    cfg = configuracao_nfse(ambiente, prefixo_api=prefixo)
    for metodo, rota in (("GET", f"/nfse/{CHAVE_FICTICIA}"), ("HEAD", f"/dps/{ID_DPS_FICTICIO}")):
        resposta = com_novas_tentativas(
            lambda m=metodo, r=rota: cliente.requisitar(m, cfg.url_sefin(r)))
        assert resposta.status in esperado, (cfg.url_sefin(rota), resposta.status)


@pytest.mark.parametrize("ambiente", AMBIENTES)
def test_caminho_fora_da_aplicacao_da_sefin_e_404_e_nao_403(cliente, ambiente):
    """Controle: o 403 acima é da aplicação protegida, não de um bloqueio geral do host."""
    cfg = configuracao_nfse(ambiente)
    resposta = com_novas_tentativas(
        lambda: cliente.requisitar("GET", f"{cfg.host_sefin}/rota-inexistente"))
    assert resposta.status == 404


@pytest.mark.parametrize("ambiente", AMBIENTES)
def test_adn_e_parametrizacao_recusam_o_certificado_no_handshake(cliente, ambiente):
    cfg = configuracao_nfse(ambiente)
    for url in (cfg.url_adn("/DFe/0?tipoNSU=DISTRIBUICAO&lote=true"),
                cfg.url_parametrizacao("/2927408/convenio")):
        with pytest.raises(ComunicacaoErro) as erro:
            cliente.requisitar("GET", url)
        assert isinstance(erro.value.__cause__, (ssl.SSLError, ConnectionResetError)), (
            f"{url}: {erro.value!r}")


@pytest.mark.parametrize("ambiente", AMBIENTES)
def test_consulta_publica_do_qr_code_existe_e_devolve_a_pagina_da_nfse(ambiente):
    url = configuracao_nfse(ambiente).url_consulta_publica(CHAVE_FICTICIA)
    pedido = urllib.request.Request(url, headers={"User-Agent": "freedfe-teste-rede"})

    def buscar():
        with urllib.request.urlopen(pedido, timeout=30) as resposta:  # noqa: S310 (https fixo)
            return resposta.status, resposta.headers.get("content-type", ""), resposta.read()

    try:
        status, tipo, corpo = com_novas_tentativas(buscar)
    except OSError as erro:  # URLError(ConnectionResetError) vem embrulhada
        pytest.skip(f"Consulta Pública indisponível neste momento: {erro!r}")
    assert status == 200 and tipo.startswith("text/html")
    assert b"Consulta P\xc3\xbablica de NFS-e" in corpo
