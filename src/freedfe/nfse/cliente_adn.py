"""Sincronização das NFS-e e eventos do contribuinte pelo ADN (distribuição por NSU).

Serve para capturar notas tomadas e eventos feitos fora do ERP (cancelamentos pelo portal ou
por ofício, análise fiscal). Rota (Manual ADN v1.0 e relatos de desenvolvedores)::

    GET {adn}/contribuintes/DFe/{NSU}?tipoNSU=DISTRIBUICAO&lote=true[&cnpjConsulta=...]
    GET {adn}/contribuintes/NFSe/{chave}/Eventos

HIPÓTESE: nomes dos campos da resposta (``StatusProcessamento``, ``LoteDFe``, ``NSU``,
``ArquivoXml``, ``TipoDocumento``); a leitura é tolerante a caixa e a sinônimos. Também é
hipótese que o NSU do caminho seja "a partir de" (inclusivo): a biblioteca pede o último NSU
recebido, o que tolera as duas semânticas (um documento repetido é descartado sem duplicar).
Um 404 é tratado como "sem documentos novos".

Não há limite de taxa publicado: a sincronização respeita ``Retry-After`` em 429 e espaça as
execuções com um bloqueio local configurável (15 min por padrão).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from freedfe.distribuicao.cliente import ResultadoSincronizacao
from freedfe.distribuicao.estado import EstadoSequenciaNsu
from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.excecoes import ComunicacaoErro
from freedfe.nfse.cliente_base import ClienteNfseBase
from freedfe.nfse.envelope import descomprimir_b64
from freedfe.nfse.resposta import campo, extrair_erros, ler_json, levantar_rejeicao
from freedfe.repositorio.base import RepositorioDocumentos
from freedfe.tipos import ModeloDocumento
from freedfe.transporte import RespostaHttp
from freedfe.validadores import exigir_chave_nfse, formatar_nsu, validar_cnpj

logger = logging.getLogger(__name__)

BLOQUEIO_PADRAO_SEGUNDOS = 15 * 60
ESPERA_429_PADRAO = 60.0
TENTATIVAS_429 = 3


@dataclass
class LoteAdn:
    """Lote de distribuição já interpretado."""

    status: str
    documentos: list[DocumentoRecebido] = field(default_factory=list)
    maior_nsu: int | None = None
    alertas: list[str] = field(default_factory=list)

    @property
    def vazio(self) -> bool:
        """True quando o lote não traz documentos."""
        return not self.documentos


class ClienteAdn(ClienteNfseBase):
    """Consultas ao ADN Contribuintes (somente ``GET``)."""

    servico = "nfse-adn"

    def distribuicao(self, nsu: int | str, cnpj_consulta: str | None = None) -> RespostaHttp:
        """``GET /DFe/{nsu}`` em lote. ``cnpj_consulta`` consulta outro CNPJ da mesma raiz."""
        consulta = "tipoNSU=DISTRIBUICAO&lote=true"
        if cnpj_consulta:
            consulta += f"&cnpjConsulta={cnpj_consulta}"
        url = self._cfg.url_adn(f"/DFe/{int(nsu)}?{consulta}")
        return self._requisitar("distribuicao", "GET", url, nsu=str(int(nsu)))

    def eventos(self, chave: str) -> RespostaHttp:
        """``GET /NFSe/{chave}/Eventos``: eventos da NFS-e no ADN."""
        chave = exigir_chave_nfse(chave)
        return self._requisitar("eventos", "GET", self._cfg.url_adn(f"/NFSe/{chave}/Eventos"),
                                chave=chave)


def interpretar_lote(resposta: RespostaHttp) -> LoteAdn:
    """Interpreta a resposta de ``GET /DFe/{NSU}``.

    Raises:
        RejeicaoNfseErro: 4xx (exceto 404, que significa "sem documentos").
        ComunicacaoErro: 5xx que sobrou depois das repetições.
    """
    if resposta.status == 404:
        return LoteAdn("NENHUM_DOCUMENTO_LOCALIZADO", alertas=["HTTP 404"])
    if resposta.status >= 500:
        raise ComunicacaoErro(f"O ADN respondeu HTTP {resposta.status}.")
    if not 200 <= resposta.status < 300:
        levantar_rejeicao(resposta, None)
    dados = ler_json(resposta)
    status = str(campo(dados, "StatusProcessamento", "status", padrao="") or "")
    itens = campo(dados, "LoteDFe", "lote", "documentos", "dfe", padrao=[]) or []
    documentos: list[DocumentoRecebido] = []
    maior: int | None = None
    for item in itens:
        arquivo = campo(item, "ArquivoXml", "arquivo", "xml")
        nsu = _como_inteiro(campo(item, "NSU", "nsu"))
        if not arquivo or nsu is None:
            logger.warning("Item do lote ADN ignorado (sem NSU ou ArquivoXml).")
            continue
        documentos.append(DocumentoRecebido(
            xml=descomprimir_b64(arquivo), nsu=formatar_nsu(nsu),
            schema=str(campo(item, "TipoDocumento", "tipo", padrao="") or "")))
        maior = nsu if maior is None else max(maior, nsu)
    alertas = [str(e) for e in extrair_erros(dados, "Alertas", "alertas")]
    return LoteAdn(status or ("DOCUMENTOS_LOCALIZADOS" if documentos else "SEM_STATUS"),
                   documentos, maior, alertas)


def _como_inteiro(valor: Any) -> int | None:
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


class SincronizadorAdn:
    """Consome a sequência de NSU do contribuinte no ADN e grava no repositório.

    Args:
        cliente: cliente HTTP do ADN.
        estado: estado persistido da sequência (``EstadoSequenciaNsu``).
        repositorio: destino dos XML (NFS-e e eventos).
        pausa: função de espera (substituível em testes).
        pausa_entre_lotes: espera entre chamadas consecutivas.
        espera_429: segundos de espera em 429 quando não há ``Retry-After``.
        cnpj_consulta: CNPJ diferente do certificado (mesma raiz: matriz/filiais).
    """

    def __init__(self, cliente: ClienteAdn, estado: EstadoSequenciaNsu,
                 repositorio: RepositorioDocumentos,
                 pausa: Callable[[float], None] = time.sleep, pausa_entre_lotes: float = 1.0,
                 espera_429: float = ESPERA_429_PADRAO, cnpj_consulta: str | None = None):
        if cnpj_consulta is not None and not validar_cnpj(cnpj_consulta):
            raise ValueError(f"CNPJ de consulta inválido: {cnpj_consulta!r}")
        self._cliente = cliente
        self._estado = estado
        self._repositorio = repositorio
        self._pausa = pausa
        self._pausa_lotes = pausa_entre_lotes
        self._espera_429 = espera_429
        self._cnpj_consulta = cnpj_consulta

    def sincronizar(self, max_lotes: int = 200) -> ResultadoSincronizacao:
        """Baixa os lotes novos até esgotar, atingir ``max_lotes`` ou ser limitado (429)."""
        resultado = ResultadoSincronizacao(modelo=ModeloDocumento.NFSE, executado=False,
                                           ult_nsu=self._estado.ult_nsu)
        restante = self._estado.segundos_para_liberar()
        if restante:
            resultado.proxima_consulta_em_s = restante
            resultado.motivo = f"Sequência bloqueada localmente por mais {restante} s."
            return resultado
        resultado.executado = True
        esgotou = False
        for _ in range(max_lotes):
            lote = self._buscar_lote(resultado)
            if lote is None:  # limitado por 429
                break
            resultado.lotes += 1
            resultado.ultimo_cstat, resultado.ultimo_xmotivo = lote.status, "; ".join(lote.alertas)
            if lote.vazio:
                esgotou = True
                break
            novo = self._armazenar(lote)
            resultado.documentos += novo
            if lote.maior_nsu is None or lote.maior_nsu <= int(self._estado.ult_nsu):
                esgotou = True  # nada além do que já havia: evita laço infinito
                break
            self._estado.atualizar_sequencia(formatar_nsu(lote.maior_nsu), None)
            self._estado.registrar_execucao(lote.status)
            self._estado.salvar()
            resultado.ult_nsu = self._estado.ult_nsu
            self._pausa(self._pausa_lotes)
        self._estado.registrar_execucao(resultado.ultimo_cstat or "")
        if esgotou:
            self._estado.bloquear()
        self._estado.salvar()
        resultado.proxima_consulta_em_s = self._estado.segundos_para_liberar()
        return resultado

    def _buscar_lote(self, resultado: ResultadoSincronizacao) -> LoteAdn | None:
        for tentativa in range(1, TENTATIVAS_429 + 1):
            resposta = self._cliente.distribuicao(self._estado.ult_nsu, self._cnpj_consulta)
            if resposta.status != 429:
                return interpretar_lote(resposta)
            espera = self._espera_retry_after(resposta)
            logger.warning("ADN limitou as requisições (429); aguardando %.0f s.", espera)
            if tentativa < TENTATIVAS_429:
                self._pausa(espera)
        resultado.motivo = "O ADN limitou as requisições (HTTP 429); tente mais tarde."
        resultado.ultimo_cstat = "429"
        return None

    def _espera_retry_after(self, resposta: RespostaHttp) -> float:
        try:
            return max(float(resposta.cabecalho("Retry-After") or self._espera_429), 0.0)
        except ValueError:
            return self._espera_429

    def _armazenar(self, lote: LoteAdn) -> int:
        resultado = self._repositorio.armazenar(lote.documentos, sequencia=ModeloDocumento.NFSE)
        if resultado.nao_reconhecidos:
            logger.warning("%s documento(s) do ADN em leiaute não reconhecido foram preservados "
                           "sem interpretação.", resultado.nao_reconhecidos)
        return resultado.total
