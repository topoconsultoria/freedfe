import http.client
import json

import pytest

import freedfe.transporte as transporte
from freedfe import __version__
from freedfe.excecoes import ComunicacaoErro, ValidacaoErro
from freedfe.transporte import ClienteRest, RespostaHttp, criar_contexto_tls


class ConexaoFalsa:
    """Substitui ``http.client.HTTPSConnection`` registrando a requisição."""

    historico: list[dict] = []
    resposta = (201, b'{"ok": true}', [("Content-Type", "application/json"), ("Retry-After", "7")])
    erro: Exception | None = None

    def __init__(self, host, porta, context=None, timeout=None):
        self.dados = {"host": host, "porta": porta, "contexto": context, "timeout": timeout}
        ConexaoFalsa.historico.append(self.dados)

    def request(self, metodo, caminho, body=None, headers=None):
        if ConexaoFalsa.erro:
            raise ConexaoFalsa.erro
        self.dados.update(metodo=metodo, caminho=caminho, corpo=body, cabecalhos=headers)

    def getresponse(self):
        status, corpo, cabecalhos = ConexaoFalsa.resposta
        return type("R", (), {"status": status, "read": lambda s: corpo,
                              "getheaders": lambda s: cabecalhos})()

    def close(self):
        self.dados["fechada"] = True


@pytest.fixture
def conexao(monkeypatch):
    ConexaoFalsa.historico = []
    ConexaoFalsa.erro = None
    ConexaoFalsa.resposta = (201, b'{"ok": true}', [("Content-Type", "application/json"),
                                                    ("Retry-After", "7")])
    monkeypatch.setattr(http.client, "HTTPSConnection", ConexaoFalsa)
    return ConexaoFalsa


def test_contexto_tls_exige_tls12_e_carrega_certificado(certificado):
    import ssl
    contexto = criar_contexto_tls(certificado)
    assert contexto.minimum_version == ssl.TLSVersion.TLSv1_2
    assert contexto.verify_mode == ssl.CERT_REQUIRED and contexto.check_hostname


def test_contexto_tls_criado_uma_unica_vez(certificado, conexao, monkeypatch):
    chamadas = []
    original = transporte.criar_contexto_tls
    monkeypatch.setattr(transporte, "criar_contexto_tls",
                        lambda *a, **k: chamadas.append(1) or original(*a, **k))
    cliente = ClienteRest(certificado)
    for _ in range(3):
        cliente.requisitar("GET", "https://sefin.exemplo.gov.br/x")
    assert len(chamadas) == 1


def test_post_json_com_cabecalhos_e_corpo(certificado, conexao):
    cliente = ClienteRest(certificado, timeout=60)
    corpo = json.dumps({"dpsXmlGZipB64": "abc"}).encode()
    resposta = cliente.requisitar("post", "https://sefin.exemplo.gov.br/SefinNacional/nfse?x=1",
                                  corpo, timeout=45)
    enviado = conexao.historico[0]
    assert enviado["metodo"] == "POST" and enviado["caminho"] == "/SefinNacional/nfse?x=1"
    assert enviado["timeout"] == 45 and enviado["porta"] == 443 and enviado["fechada"]
    assert enviado["corpo"] == corpo
    cabecalhos = enviado["cabecalhos"]
    assert cabecalhos["Content-Type"] == "application/json"
    assert cabecalhos["Accept"] == "application/json"
    assert cabecalhos["User-Agent"] == f"freedfe/{__version__}"
    assert cabecalhos["Content-Length"] == str(len(corpo))
    assert resposta.status == 201 and json.loads(resposta.corpo) == {"ok": True}
    assert resposta.cabecalho("RETRY-AFTER") == "7" and resposta.cabecalho("x", "pad") == "pad"


def test_get_sem_corpo_nao_envia_content_type(certificado, conexao):
    ClienteRest(certificado).requisitar("GET", "https://exemplo.gov.br/nfse/123")
    assert "Content-Type" not in conexao.historico[0]["cabecalhos"]
    assert conexao.historico[0]["timeout"] == 60


@pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
def test_status_de_erro_nao_levanta_excecao(certificado, conexao, status):
    conexao.resposta = (status, b'{"erros": []}', [])
    assert ClienteRest(certificado).requisitar("GET", "https://exemplo.gov.br/x").status == status


def test_head_le_e_descarta_o_corpo(certificado, conexao):
    conexao.resposta = (200, b"", [])
    resposta = ClienteRest(certificado).requisitar("HEAD", "https://exemplo.gov.br/dps/DPS1")
    assert resposta == RespostaHttp(status=200, corpo=b"", cabecalhos={})
    assert conexao.historico[0]["metodo"] == "HEAD"


@pytest.mark.parametrize("erro", [TimeoutError("timed out"), ConnectionResetError("reset"),
                                  http.client.RemoteDisconnected("fechou")])
def test_falha_de_rede_vira_comunicacao_erro(certificado, conexao, erro):
    conexao.erro = erro
    with pytest.raises(ComunicacaoErro, match="exemplo.gov.br"):
        ClienteRest(certificado).requisitar("POST", "https://exemplo.gov.br/nfse", b"{}")
    assert conexao.historico[0]["fechada"]


def test_rejeita_url_sem_https(certificado, conexao):
    with pytest.raises(ValidacaoErro, match="HTTPS"):
        ClienteRest(certificado).requisitar("GET", "http://exemplo.gov.br/x")
    assert conexao.historico == []
