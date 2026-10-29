"""Servidores locais para testes de integração HTTP sem depender da rede da Fazenda.

``ServidorSefinFalso`` é um servidor **HTTPS real com mTLS** (exige certificado cliente) que imita
as rotas da Sefin Nacional, do ADN e da parametrização. Ele existe para exercitar, em sockets
reais, o que o transporte falso não cobre: handshake TLS, certificado cliente, cabeçalhos,
timeouts, túnel de proxy e o fluxo completo de emissão, reconciliação, cancelamento e sincronização.

Limites (o servidor NÃO é a Sefin): os nomes dos campos JSON de resposta seguem as hipóteses
documentadas na biblioteca (as mesmas de ``fabrica_nfse``) e a numeração/regras de negócio
são mínimas. O que ele valida de verdade é o que o cliente envia: JSON com o campo exato,
GZip+Base64, declaração UTF-8 (E1229, como a Sefin real), XML bem formado, ``Signature`` irmã
de ``infDPS`` com a assinatura criptográfica conferida (Exclusive C14N + RSA-SHA256), ``Id`` da
DPS com 45 posições e ``tpAmb``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import select
import socket
import socketserver
import ssl
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree

from freedfe.assinatura import PERFIL_NFSE, verificar_assinatura
from freedfe.nfse.envelope import descomprimir_b64
from freedfe.nfse.leiaute import NAMESPACE_NFSE
from freedfe.servicos import ConfiguracaoNfse
from freedfe.tipos import Ambiente
from freedfe.transporte import RespostaHttp
from freedfe.validadores import validar_id_dps

from fabrica_nfse import (
    evento_nfse_xml,
    nfse_de_dps_xml,
    resposta_adn,
    resposta_adn_vazia,
    resposta_emissao,
    resposta_erro,
    resposta_evento,
    resposta_lista_eventos,
)

CAMINHO_SEFIN = "/SefinNacional"
CAMINHO_ADN = "/adn/contribuintes"
CAMINHO_PARAMETRIZACAO = "/adn/parametrizacao"
_NS = f"{{{NAMESPACE_NFSE}}}"
# A Sefin real recusou com E1229 um XML ASCII sem declaração (produção restrita, 29/10/2026).
_DECLARACAO_UTF8 = re.compile(rb"""<\?xml\s[^>]*encoding\s*=\s*["']utf-?8["']""", re.IGNORECASE)
# Forma do convênio observada na mesma data: indicadores 0/1 dentro de parametrosConvenio.
CONVENIO_ADERENTE = {"parametrosConvenio": {"aderenteAmbienteNacional": 1,
                                            "aderenteEmissorNacional": 1}}


@dataclass
class RequisicaoRecebida:
    """O que o servidor viu em uma chamada."""

    metodo: str
    caminho: str
    cabecalhos: dict[str, str]
    corpo: bytes
    cn_cliente: str | None  # subject do certificado cliente apresentado no handshake
    json: dict = field(default_factory=dict)


def gerar_certificado_servidor(nome: str = "localhost") -> tuple[bytes, bytes]:
    """Certificado autoassinado para ``localhost``/``127.0.0.1``: ``(cert_pem, chave_pem)``."""
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assunto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nome)])
    agora = dt.datetime.now(dt.timezone.utc)
    certificado = (x509.CertificateBuilder()
                   .subject_name(assunto).issuer_name(assunto).public_key(chave.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(agora - dt.timedelta(days=1))
                   .not_valid_after(agora + dt.timedelta(days=30))
                   .add_extension(x509.SubjectAlternativeName([
                       x509.DNSName("localhost"),
                       x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
                       critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .sign(chave, hashes.SHA256()))
    return (certificado.public_bytes(serialization.Encoding.PEM),
            chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))


class _Servidor(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):  # handshakes recusados são esperados
        pass


class ServidorSefinFalso:
    """Servidor HTTPS com mTLS na porta efêmera de ``127.0.0.1``.

    Args:
        certificados_clientes_pem: PEM dos certificados cliente aceitos (âncoras de confiança).
        ambiente: valor esperado em ``tpAmb`` (padrão: produção restrita).
        validador_xsd: ``ValidadorXsd`` opcional para conferir a DPS recebida contra o XSD oficial.

    Atributos de comportamento (alterados pelo teste):
        atraso_emissao: segundos de espera **depois** de registrar a NFS-e e **antes** de responder,
            para simular timeout com nota já emitida.
        atraso_geral: segundos de espera antes de responder a QUALQUER requisição (timeout puro).
        proxima_rejeicao: ``(codigo, status)``; o próximo ``POST /nfse`` é rejeitado.
        status_forcado: ``{(metodo, prefixo_do_caminho): (status, corpo)}`` para respostas fixas.
    """

    def __init__(self, certificados_clientes_pem: list[bytes],
                 ambiente: Ambiente = Ambiente.HOMOLOGACAO, validador_xsd=None):
        self.ambiente = Ambiente(ambiente)
        self.validador_xsd = validador_xsd
        self.requisicoes: list[RequisicaoRecebida] = []
        self.nfses: dict[str, bytes] = {}
        self.dps_para_chave: dict[str, str] = {}
        self.eventos: dict[str, list[bytes]] = {}
        self.documentos_adn: list[tuple[int, bytes]] = []
        self.convenios: dict[str, dict] = {}
        self.atraso_emissao = 0.0
        self.atraso_geral = 0.0
        self.proxima_rejeicao: tuple[str, int] | None = None
        self.status_forcado: dict[tuple[str, str], tuple[int, bytes]] = {}
        self._trava = threading.Lock()
        self._numero = 0
        self._tmp = tempfile.TemporaryDirectory(prefix="freedfe-servidor-")
        base = Path(self._tmp.name)
        cert_pem, chave_pem = gerar_certificado_servidor()
        self.ca_bundle = str(base / "ca.pem")
        (base / "ca.pem").write_bytes(cert_pem)
        (base / "servidor.pem").write_bytes(cert_pem)
        (base / "servidor.key").write_bytes(chave_pem)
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.minimum_version = ssl.TLSVersion.TLSv1_2
        contexto.load_cert_chain(base / "servidor.pem", base / "servidor.key")
        contexto.verify_mode = ssl.CERT_REQUIRED
        contexto.load_verify_locations(cadata=b"".join(certificados_clientes_pem).decode())
        self._contexto = contexto
        servidor = self

        class Manipulador(_Manipulador):
            dono = servidor

        self._http = _Servidor(("127.0.0.1", 0), Manipulador)
        self._http.socket = contexto.wrap_socket(self._http.socket, server_side=True)
        self._thread = threading.Thread(
            target=self._http.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    # -- ciclo de vida ------------------------------------------------------

    @property
    def porta(self) -> int:
        return self._http.server_address[1]

    @property
    def configuracao(self) -> ConfiguracaoNfse:
        """``ConfiguracaoNfse`` apontando para este servidor (host ``localhost`` valida o SAN)."""
        base = f"https://localhost:{self.porta}"
        return ConfiguracaoNfse(
            ambiente=self.ambiente, host_sefin=base,
            base_adn_contribuintes=f"{base}{CAMINHO_ADN}",
            base_parametrizacao=f"{base}{CAMINHO_PARAMETRIZACAO}",
            modelo_url_consulta_publica=f"{base}/ConsultaPublica/?tpc=1&chave={{chave}}",
            caminho_sefin=CAMINHO_SEFIN, timeout_emissao=5, timeout_consulta=5)

    def __enter__(self) -> ServidorSefinFalso:
        self._thread.start()
        return self

    def __exit__(self, *args) -> None:
        self._http.shutdown()
        self._http.server_close()
        self._tmp.cleanup()

    # -- consultas para os testes ------------------------------------------

    def chamadas(self, metodo: str, prefixo: str = "") -> list[RequisicaoRecebida]:
        """Requisições recebidas com o método e o prefixo de caminho informados."""
        with self._trava:
            return [r for r in self.requisicoes
                    if r.metodo == metodo and r.caminho.startswith(prefixo)]

    def publicar_no_adn(self, xml: bytes) -> int:
        """Acrescenta um documento à fila do ADN e devolve o NSU atribuído."""
        with self._trava:
            nsu = len(self.documentos_adn) + 1
            self.documentos_adn.append((nsu, xml))
            return nsu

    # -- regras de negócio mínimas -----------------------------------------

    def emitir(self, dps_bytes: bytes) -> RespostaHttp:
        if not _DECLARACAO_UTF8.match(dps_bytes):
            return resposta_erro("E1229", "XML não está utilizando codificação UTF8.")
        try:
            raiz = etree.fromstring(dps_bytes, etree.XMLParser(resolve_entities=False))
        except etree.XMLSyntaxError as erro:
            return resposta_erro("E0001", f"XML malformado: {erro}")
        inf = raiz.find(f"{_NS}infDPS")
        if raiz.tag != f"{_NS}DPS" or inf is None:
            return resposta_erro("E0002", "Raiz DPS/infDPS ausente")
        id_dps = inf.get("Id", "")
        if not validar_id_dps(id_dps):
            return resposta_erro("E0004", "Id da DPS inválido")
        if inf.findtext(f"{_NS}tpAmb") != str(int(self.ambiente)):
            return resposta_erro("E0006", "tpAmb diferente do ambiente")
        if raiz.find("{http://www.w3.org/2000/09/xmldsig#}Signature") is None:
            return resposta_erro("E1235", "Assinatura deve ser irmã de infDPS")
        if not verificar_assinatura(raiz, PERFIL_NFSE):
            return resposta_erro("E0714", "Assinatura digital inválida")
        if self.validador_xsd is not None:
            try:
                self.validador_xsd.validar(raiz)
            except Exception as erro:  # ValidacaoErro com as linhas do schema
                return resposta_erro("E0001", str(erro)[:300])
        with self._trava:
            if id_dps in self.dps_para_chave:
                return resposta_erro("E0014", "DPS já utilizada em NFS-e emitida", status=400)
            self._numero += 1
            chave, xml = nfse_de_dps_xml(raiz, numero_nfse=self._numero)
            self.nfses[chave] = xml
            self.dps_para_chave[id_dps] = chave
        self.publicar_no_adn(xml)
        return resposta_emissao(xml, chave)

    def registrar_evento(self, chave: str, pedido_bytes: bytes) -> RespostaHttp:
        if not _DECLARACAO_UTF8.match(pedido_bytes):
            return resposta_erro("E1229", "XML não está utilizando codificação UTF8.")
        raiz = etree.fromstring(pedido_bytes, etree.XMLParser(resolve_entities=False))
        if raiz.tag != f"{_NS}pedRegEvento":
            return resposta_erro("E0002", "Raiz pedRegEvento ausente")
        if not verificar_assinatura(raiz, PERFIL_NFSE):
            return resposta_erro("E0714", "Assinatura digital inválida")
        info = raiz.find(f"{_NS}infPedReg")
        if info.findtext(f"{_NS}chNFSe") != chave or chave not in self.nfses:
            return resposta_erro("E0801", "NFS-e não encontrada", status=404)
        if len(info.get("Id", "")) != 59:
            return resposta_erro("E0004", "Id do pedido de evento inválido")
        tipo = next((el.tag.removeprefix(_NS)[1:] for el in info
                     if el.tag.removeprefix(_NS).startswith("e1")), "101101")
        with self._trava:
            if self.eventos.get(chave):
                return resposta_erro("E0840", "NFS-e já cancelada")
            xml = evento_nfse_xml(chave, tipo)
            self.eventos.setdefault(chave, []).append(xml)
        self.publicar_no_adn(xml)
        return resposta_evento(chave, tipo)


class _Manipulador(BaseHTTPRequestHandler):
    """Roteia as chamadas; a lógica fica em ``ServidorSefinFalso``."""

    dono: ServidorSefinFalso
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silencia o stderr do http.server
        pass

    def _cn_cliente(self) -> str | None:
        certificado = self.connection.getpeercert()
        for grupo in (certificado or {}).get("subject", ()):
            for nome, valor in grupo:
                if nome == "commonName":
                    return valor
        return None

    def _tratar(self) -> None:
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        recebida = RequisicaoRecebida(
            self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, corpo,
            self._cn_cliente())
        try:
            recebida.json = json.loads(corpo) if corpo else {}
        except ValueError:
            recebida.json = {}
        with self.dono._trava:
            self.dono.requisicoes.append(recebida)
        if self.dono.atraso_geral:
            time.sleep(self.dono.atraso_geral)
        resposta = self._responder(recebida)
        self.send_response(resposta.status)
        for nome, valor in resposta.cabecalhos.items():
            self.send_header(nome, valor)
        self.send_header("Content-Length", str(len(resposta.corpo)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(resposta.corpo)

    do_GET = do_POST = do_HEAD = _tratar  # noqa: N815 (nomes exigidos pelo http.server)

    def _responder(self, req: RequisicaoRecebida) -> RespostaHttp:
        caminho = req.caminho.split("?", 1)[0]
        for (metodo, prefixo), (status, corpo) in self.dono.status_forcado.items():
            if req.metodo == metodo and caminho.startswith(prefixo):
                return RespostaHttp(status, corpo, {"content-type": "application/json"})
        if caminho.startswith(CAMINHO_SEFIN):
            return self._sefin(req, caminho.removeprefix(CAMINHO_SEFIN))
        if caminho.startswith(CAMINHO_ADN):
            return self._adn(req, caminho.removeprefix(CAMINHO_ADN))
        if caminho.startswith(CAMINHO_PARAMETRIZACAO):
            return self._parametrizacao(caminho.removeprefix(CAMINHO_PARAMETRIZACAO))
        return RespostaHttp(404, b"{}", {"content-type": "application/json"})

    def _sefin(self, req: RequisicaoRecebida, rota: str) -> RespostaHttp:
        partes = rota.strip("/").split("/")
        dono = self.dono
        if partes == ["nfse"] and req.metodo == "POST":
            if "dpsXmlGZipB64" not in req.json:
                return RespostaHttp(500, b"", {})  # campo errado = 500 mudo, como na Sefin
            if dono.proxima_rejeicao:
                codigo, status = dono.proxima_rejeicao
                dono.proxima_rejeicao = None
                return resposta_erro(codigo, status=status)
            resposta = dono.emitir(descomprimir_b64(req.json["dpsXmlGZipB64"]))
            if dono.atraso_emissao:
                time.sleep(dono.atraso_emissao)
            return resposta
        if len(partes) == 2 and partes[0] == "dps":
            chave = dono.dps_para_chave.get(partes[1])
            if req.metodo == "HEAD":
                return RespostaHttp(200 if chave else 404)
            if chave:
                corpo = json.dumps({"chaveAcesso": chave}).encode()
                return RespostaHttp(200, corpo, {"content-type": "application/json"})
            return resposta_erro("E0801", "DPS não encontrada", status=404)
        if len(partes) == 2 and partes[0] == "nfse" and req.metodo == "GET":
            xml = dono.nfses.get(partes[1])
            if xml is None:
                return resposta_erro("E0801", "NFS-e não encontrada", status=404)
            return resposta_emissao(xml, partes[1], status=200)
        if len(partes) == 3 and partes[0] == "nfse" and partes[2] == "eventos":
            if req.metodo == "POST":
                if "pedidoRegistroEventoXmlGZipB64" not in req.json:
                    return RespostaHttp(500, b"", {})
                return dono.registrar_evento(
                    partes[1], descomprimir_b64(req.json["pedidoRegistroEventoXmlGZipB64"]))
            return resposta_lista_eventos(dono.eventos.get(partes[1], []))
        return RespostaHttp(404, b"{}", {"content-type": "application/json"})

    def _adn(self, req: RequisicaoRecebida, rota: str) -> RespostaHttp:
        partes = rota.strip("/").split("/")
        if len(partes) == 2 and partes[0] == "DFe" and partes[1].isdigit():
            nsu = int(partes[1])
            lote = [(n, x) for n, x in self.dono.documentos_adn if n > nsu][:50]
            return resposta_adn(lote) if lote else resposta_adn_vazia()
        if len(partes) == 3 and partes[0] == "NFSe" and partes[2] == "Eventos":
            return resposta_lista_eventos(self.dono.eventos.get(partes[1], []))
        return RespostaHttp(404, b"{}", {"content-type": "application/json"})

    def _parametrizacao(self, rota: str) -> RespostaHttp:
        partes = rota.strip("/").split("/")
        if len(partes) == 2 and partes[1] == "convenio":
            dados = self.dono.convenios.get(partes[0])
            if dados is None:
                return resposta_erro("E0037", "Município não conveniado", status=404)
            return RespostaHttp(200, json.dumps(dados).encode(),
                                {"content-type": "application/json"})
        return RespostaHttp(404, b"{}", {"content-type": "application/json"})


# ---------------------------------------------------------------------------
# Proxy HTTP com CONNECT (para provar que o mTLS passa pelo túnel)
# ---------------------------------------------------------------------------

class ProxyConnectFalso:
    """Proxy ``http://`` mínimo que só aceita ``CONNECT`` e repassa bytes cegamente.

    Registra os pedidos em ``conexoes`` (``(destino, proxy_authorization)``). Com ``recusar``
    responde 403 ao CONNECT, como o proxy deste ambiente faz com hosts não liberados.
    """

    def __init__(self, recusar: bool = False, credencial: str | None = None):
        self.recusar = recusar
        self.credencial = credencial  # valor esperado de Proxy-Authorization
        self.conexoes: list[tuple[str, str | None]] = []
        proxy = self

        class Manipulador(socketserver.BaseRequestHandler):
            def handle(self):
                proxy._atender(self.request)

        self._servidor = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Manipulador)
        self._servidor.daemon_threads = True
        self._thread = threading.Thread(
            target=self._servidor.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._servidor.server_address[1]}"

    def __enter__(self) -> ProxyConnectFalso:
        self._thread.start()
        return self

    def __exit__(self, *args) -> None:
        self._servidor.shutdown()
        self._servidor.server_close()

    def _atender(self, cliente: socket.socket) -> None:
        cabecalho = b""
        while b"\r\n\r\n" not in cabecalho:
            parte = cliente.recv(4096)
            if not parte:
                return
            cabecalho += parte
        linhas = cabecalho.decode("latin-1").split("\r\n")
        metodo, destino, _ = linhas[0].split(" ", 2)
        autorizacao = next((linha.split(":", 1)[1].strip() for linha in linhas[1:]
                            if linha.lower().startswith("proxy-authorization:")), None)
        self.conexoes.append((destino, autorizacao))
        if metodo != "CONNECT":
            cliente.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            return
        if self.recusar or (self.credencial and autorizacao != self.credencial):
            status = b"407 Proxy Authentication Required" if self.credencial else b"403 Forbidden"
            cliente.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\n\r\n")
            return
        host, porta = destino.rsplit(":", 1)
        with socket.create_connection(("127.0.0.1" if host == "localhost" else host,
                                       int(porta)), timeout=10) as remoto:
            cliente.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self._repassar(cliente, remoto)

    @staticmethod
    def _repassar(a: socket.socket, b: socket.socket) -> None:
        sockets = [a, b]
        while True:
            prontos, _, _ = select.select(sockets, [], [], 10)
            if not prontos:
                return
            for origem in prontos:
                dados = origem.recv(65536)
                if not dados:
                    return
                (b if origem is a else a).sendall(dados)
