"""Transportes sobre TLS 1.2+ com autenticação mútua (certificado cliente).

``ClienteSoap`` atende a distribuição DF-e e a manifestação (SOAP 1.2); ``ClienteRest``
atende a NFS-e Nacional (REST/JSON). Ambos compartilham o contexto TLS de ``criar_contexto_tls``
e a mesma resolução de proxy (``resolver_proxy``).

**Proxy.** Por padrão (``proxy="auto"``) usam ``HTTPS_PROXY``/``NO_PROXY`` do ambiente, como
``requests`` e ``curl``. O túnel é aberto com ``CONNECT`` e o TLS fim a fim (inclusive o
certificado cliente) é negociado com o servidor da Fazenda, sem que o proxy veja o conteúdo.
Só proxies ``http://`` são suportados. Passe ``proxy=None`` para nunca usar proxy ou uma URL
(``http://usuario:senha@host:3128``) para forçar um. Proxies que interceptam o TLS (inspeção
com CA própria) só funcionam com o ``ca_bundle`` apontando para a CA do proxy e, de todo modo,
quebram o mTLS: o certificado cliente não chega ao servidor.
"""

from __future__ import annotations

import base64
import http.client
import ssl
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from freedfe._versao import __version__
from freedfe.certificado import CertificadoA1
from freedfe.excecoes import ComunicacaoErro, ValidacaoErro

SOAP12_NS = "http://www.w3.org/2003/05/soap-envelope"


def criar_contexto_tls(certificado: CertificadoA1, ca_bundle: str | None = None) -> ssl.SSLContext:
    """Contexto TLS 1.2+ com o certificado cliente carregado (mTLS).

    Os PEM temporários são apagados logo após o carregamento. A verificação do servidor
    nunca é desabilitada: use ``ca_bundle`` se a cadeia ICP-Brasil não estiver no sistema.
    """
    certificado.verificar_validade()
    contexto = ssl.create_default_context(cafile=ca_bundle)
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    with certificado.arquivos_pem() as (cert_pem, chave_pem):
        contexto.load_cert_chain(cert_pem, chave_pem)
    return contexto


PROXY_AUTOMATICO = "auto"
"""Valor de ``proxy`` que lê ``HTTPS_PROXY``/``NO_PROXY`` do ambiente (padrão)."""


@dataclass(frozen=True)
class ProxyHttp:
    """Proxy ``http://`` usado como túnel ``CONNECT``. A senha nunca aparece no ``repr``."""

    host: str
    porta: int
    autorizacao: str | None = field(default=None, repr=False)

    def __str__(self) -> str:
        return f"{self.host}:{self.porta}"

    def cabecalhos_tunel(self) -> dict[str, str]:
        """Cabeçalhos do ``CONNECT`` (``Proxy-Authorization`` básico, se houver credenciais)."""
        return {"Proxy-Authorization": self.autorizacao} if self.autorizacao else {}


def resolver_proxy(proxy: str | None, host_destino: str) -> ProxyHttp | None:
    """Decide se a conexão com ``host_destino`` passa por proxy.

    Args:
        proxy: ``"auto"`` (padrão: ``HTTPS_PROXY`` e ``NO_PROXY``), ``None``/``""`` (conexão
            direta) ou uma URL ``http://[usuario:senha@]host[:porta]``.
        host_destino: servidor final, para avaliar ``NO_PROXY``.

    Raises:
        ValidacaoErro: URL de proxy inválida ou com esquema diferente de ``http``.
    """
    if not proxy:
        return None
    if proxy == PROXY_AUTOMATICO:
        proxy = urllib.request.getproxies().get("https")
        if not proxy or urllib.request.proxy_bypass(host_destino):
            return None
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    partes = urllib.parse.urlsplit(proxy)
    if partes.scheme != "http" or not partes.hostname:
        raise ValidacaoErro("Só há suporte a proxy http://host:porta (túnel CONNECT).")
    autorizacao = None
    if partes.username:
        credencial = f"{urllib.parse.unquote(partes.username)}:" \
                     f"{urllib.parse.unquote(partes.password or '')}"
        autorizacao = "Basic " + base64.b64encode(credencial.encode()).decode()
    return ProxyHttp(partes.hostname, partes.port or 3128, autorizacao)


def abrir_conexao(host: str, porta: int, contexto: ssl.SSLContext, timeout: float,
                  proxy: str | None) -> http.client.HTTPSConnection:
    """``HTTPSConnection`` direta ou, quando há proxy, via túnel ``CONNECT`` até ``host``."""
    via = resolver_proxy(proxy, host)
    if via is None:
        return http.client.HTTPSConnection(host, porta, context=contexto, timeout=timeout)
    conexao = http.client.HTTPSConnection(via.host, via.porta, context=contexto, timeout=timeout)
    conexao.set_tunnel(host, porta, headers=via.cabecalhos_tunel())
    return conexao


def descrever_falha(host: str, operacao: str, erro: Exception, proxy: str | None) -> str:
    """Mensagem de ``ComunicacaoErro`` com a dica provável, sem expor segredos."""
    mensagem = f"Falha ao comunicar com {host} ({operacao}): {erro}"
    via = resolver_proxy(proxy, host) if _proxy_valido(proxy) else None
    if isinstance(erro, ssl.SSLCertVerificationError):
        mensagem += (". O certificado do servidor não foi aceito: confirme se a cadeia ICP-Brasil "
                     "está no ca_bundle (ou no repositório do sistema)")
    elif isinstance(erro, ssl.SSLError):
        mensagem += (". O handshake TLS foi recusado: servidores da NFS-e Nacional exigem "
                     "certificado cliente ICP-Brasil válido (A1 e dentro da validade)")
    elif isinstance(erro, ConnectionResetError):
        mensagem += (". O servidor encerrou a conexão: com mTLS isso costuma indicar certificado "
                     "cliente não aceito (ICP-Brasil) ou bloqueio de rede/proxy; se persistir "
                     "só em alguns acessos, é limitação momentânea do servidor")
    elif via is not None and "Tunnel connection failed" in str(erro):
        mensagem += f". O proxy {via} recusou o túnel CONNECT: o host precisa estar liberado nele"
    return mensagem


def _proxy_valido(proxy: str | None) -> bool:
    try:
        resolver_proxy(proxy, "")
    except ValidacaoErro:
        return False
    return True


class Transporte(ABC):
    """Contrato de envio de mensagens SOAP. Permite substituir o transporte em testes."""

    @abstractmethod
    def enviar(self, url: str, corpo: bytes, acao_soap: str) -> bytes:
        """Envia o envelope e devolve o corpo da resposta."""


class ClienteSoap(Transporte):
    """POST SOAP 1.2 com certificado A1, usando apenas a biblioteca padrão.

    Args:
        certificado: certificado A1 do interessado.
        ca_bundle: bundle PEM com a cadeia ICP-Brasil, se o TLS do servidor não validar
            com as ACs do sistema. Nunca desabilite a verificação.
        timeout: tempo máximo, em segundos, de cada requisição.
        proxy: ``"auto"`` (padrão, usa ``HTTPS_PROXY``/``NO_PROXY``), ``None`` (sem proxy) ou
            URL ``http://[usuario:senha@]host:porta``. Ver :func:`resolver_proxy`.
    """

    def __init__(self, certificado: CertificadoA1, ca_bundle: str | None = None,
                 timeout: int = 60, proxy: str | None = PROXY_AUTOMATICO):
        self._certificado = certificado
        self._ca_bundle = ca_bundle
        self._timeout = timeout
        self._proxy = proxy
        self._contexto: ssl.SSLContext | None = None

    def enviar(self, url: str, corpo: bytes, acao_soap: str) -> bytes:
        """Envia o envelope. Levanta ``ComunicacaoErro`` em falha de rede ou HTTP sem SOAP."""
        alvo = urllib.parse.urlsplit(url)
        conexao = abrir_conexao(alvo.hostname, alvo.port or 443, self._obter_contexto(),
                                self._timeout, self._proxy)
        try:
            conexao.request("POST", alvo.path, body=corpo,
                            headers=self._cabecalhos(corpo, acao_soap))
            resposta = conexao.getresponse()
            conteudo = resposta.read()
        except (OSError, http.client.HTTPException) as erro:
            raise ComunicacaoErro(
                descrever_falha(alvo.hostname, "SOAP", erro, self._proxy)) from erro
        finally:
            conexao.close()
        if resposta.status != 200 and b"Envelope" not in conteudo:
            raise ComunicacaoErro(f"HTTP {resposta.status} {resposta.reason}: {conteudo[:500]!r}")
        return conteudo

    @staticmethod
    def _cabecalhos(corpo: bytes, acao_soap: str) -> dict[str, str]:
        return {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{acao_soap}"',
            "Content-Length": str(len(corpo)),
        }

    def _obter_contexto(self) -> ssl.SSLContext:
        """Cria (uma vez) o contexto TLS. Os PEM temporários são apagados após o carregamento."""
        if self._contexto is None:
            self._contexto = criar_contexto_tls(self._certificado, self._ca_bundle)
        return self._contexto


# ---------------------------------------------------------------------------
# REST/JSON (NFS-e Nacional)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RespostaHttp:
    """Resposta HTTP já lida. Status 4xx/5xx não geram exceção: quem chama interpreta."""

    status: int
    corpo: bytes = b""
    cabecalhos: dict[str, str] = field(default_factory=dict)

    def cabecalho(self, nome: str, padrao: str | None = None) -> str | None:
        """Valor de um cabeçalho (sem diferenciar maiúsculas de minúsculas)."""
        alvo = nome.lower()
        for chave, valor in self.cabecalhos.items():
            if chave.lower() == alvo:
                return valor
        return padrao


class TransporteRest(ABC):
    """Contrato de requisições HTTP/REST. Permite substituir o transporte em testes."""

    @abstractmethod
    def requisitar(self, metodo: str, url: str, corpo: bytes | None = None,
                   cabecalhos: dict[str, str] | None = None,
                   timeout: int | None = None) -> RespostaHttp:
        """Executa a requisição e devolve a resposta, qualquer que seja o status HTTP.

        Levanta ``ComunicacaoErro`` apenas em falha de rede (TLS, DNS, timeout).
        """


class ClienteRest(TransporteRest):
    """Requisições HTTPS com certificado A1 (mTLS), usando apenas a biblioteca padrão.

    Args:
        certificado: certificado A1 do interessado.
        ca_bundle: bundle PEM com a cadeia do servidor, se não validar com as ACs do sistema.
            Nunca desabilite a verificação.
        timeout: tempo máximo padrão, em segundos, de cada requisição.
        proxy: ``"auto"`` (padrão, usa ``HTTPS_PROXY``/``NO_PROXY``), ``None`` (sem proxy) ou
            URL ``http://[usuario:senha@]host:porta``. Ver :func:`resolver_proxy`.
    """

    def __init__(self, certificado: CertificadoA1, ca_bundle: str | None = None,
                 timeout: int = 60, proxy: str | None = PROXY_AUTOMATICO):
        self._certificado = certificado
        self._ca_bundle = ca_bundle
        self._timeout = timeout
        self._proxy = proxy
        self._contexto: ssl.SSLContext | None = None

    def requisitar(self, metodo: str, url: str, corpo: bytes | None = None,
                   cabecalhos: dict[str, str] | None = None,
                   timeout: int | None = None) -> RespostaHttp:
        """Executa a requisição. Levanta ``ComunicacaoErro`` em falha de rede ou timeout."""
        alvo = urllib.parse.urlsplit(url)
        if alvo.scheme != "https" or not alvo.hostname:
            raise ValidacaoErro(f"A API exige HTTPS: {url}")
        caminho = alvo.path or "/"
        if alvo.query:
            caminho = f"{caminho}?{alvo.query}"
        metodo = metodo.upper()
        conexao = abrir_conexao(alvo.hostname, alvo.port or 443, self._obter_contexto(),
                                timeout or self._timeout, self._proxy)
        try:
            conexao.request(metodo, caminho, body=corpo,
                            headers=self._cabecalhos(metodo, corpo, cabecalhos))
            resposta = conexao.getresponse()
            conteudo = resposta.read()  # HEAD devolve b"": lê e descarta
            recebidos = {nome.lower(): valor for nome, valor in resposta.getheaders()}
        except (OSError, http.client.HTTPException) as erro:
            raise ComunicacaoErro(
                descrever_falha(alvo.hostname, metodo, erro, self._proxy)) from erro
        finally:
            conexao.close()
        return RespostaHttp(status=resposta.status, corpo=conteudo, cabecalhos=recebidos)

    @staticmethod
    def _cabecalhos(metodo: str, corpo: bytes | None,
                    extras: dict[str, str] | None) -> dict[str, str]:
        cabecalhos = {"Accept": "application/json", "User-Agent": f"freedfe/{__version__}"}
        if corpo is not None:
            cabecalhos["Content-Type"] = "application/json"
            cabecalhos["Content-Length"] = str(len(corpo))
        cabecalhos.update(extras or {})
        return cabecalhos

    def _obter_contexto(self) -> ssl.SSLContext:
        if self._contexto is None:
            self._contexto = criar_contexto_tls(self._certificado, self._ca_bundle)
        return self._contexto
