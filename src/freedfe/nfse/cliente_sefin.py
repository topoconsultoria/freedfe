"""Cliente REST da Sefin Nacional (emissão, consulta por chave/DPS e eventos).

Regras operacionais (relatório técnico, out/2026):
    - o ``POST`` de emissão **nunca** é repetido automaticamente: timeout ou 5xx deixam a
      emissão incerta e a decisão passa pela consulta ``HEAD /dps/{id}``;
    - ``GET``/``HEAD`` são repetidos com espera crescente (2, 4 e 8 s) em falha de rede ou 5xx;
    - o corpo de eventos usa o campo ``pedidoRegistroEventoXmlGZipB64`` (nome errado gera HTTP
      500 sem mensagem);
    - nenhum XML é gravado na auditoria (contém CPF e valores).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from freedfe.auditoria import RegistroAuditoria
from freedfe.excecoes import ComunicacaoErro, RespostaInvalidaErro
from freedfe.nfse.envelope import comprimir_b64
from freedfe.nfse.resposta import campo, extrair_erros, ler_json
from freedfe.servicos import ConfiguracaoNfse
from freedfe.transporte import RespostaHttp, TransporteRest
from freedfe.validadores import exigir_chave_nfse, exigir_id_dps

ESPERAS_PADRAO = (2.0, 4.0, 8.0)
STATUS_REPETIVEIS = frozenset({408, 425, 429, 500, 502, 503, 504})


class ClienteSefin:
    """Chamadas à Sefin Nacional. Devolve ``RespostaHttp``; quem chama interpreta o resultado.

    Args:
        configuracao: URLs e prazos do ambiente.
        transporte: transporte REST com mTLS.
        auditoria: trilha JSONL (sem XML).
        pausa: função de espera entre tentativas (substituível em testes).
        esperas: segundos de espera antes de cada nova tentativa de ``GET``/``HEAD``.
        status_dps_existente / status_dps_inexistente: HIPÓTESE sobre ``HEAD /dps/{id}``
            (200 quando a DPS gerou NFS-e, 404 quando não); ajustáveis após o smoke test.
    """

    def __init__(self, configuracao: ConfiguracaoNfse, transporte: TransporteRest,
                 auditoria: RegistroAuditoria,
                 pausa: Callable[[float], None] = time.sleep,
                 esperas: tuple[float, ...] = ESPERAS_PADRAO,
                 status_dps_existente: frozenset[int] = frozenset({200}),
                 status_dps_inexistente: frozenset[int] = frozenset({404})):
        self._cfg = configuracao
        self._transporte = transporte
        self._auditoria = auditoria
        self._pausa = pausa
        self._esperas = esperas
        self._existente = status_dps_existente
        self._inexistente = status_dps_inexistente

    @property
    def configuracao(self) -> ConfiguracaoNfse:
        """Configuração (URLs) em uso."""
        return self._cfg

    # ------------------------------------------------------------------
    # Emissão e consulta
    # ------------------------------------------------------------------

    def emitir(self, dps_assinada: bytes, id_dps: str) -> RespostaHttp:
        """``POST /nfse``, **sem repetição automática**; ``ComunicacaoErro`` em falha de rede."""
        corpo = json.dumps({"dpsXmlGZipB64": comprimir_b64(dps_assinada)}).encode("ascii")
        return self._chamar("emitir", "POST", "/nfse", corpo=corpo,
                            timeout=self._cfg.timeout_emissao, repetir=False,
                            id_dps=exigir_id_dps(id_dps))

    def consultar_nfse(self, chave: str) -> RespostaHttp:
        """``GET /nfse/{chave}``: NFS-e em ``nfseXmlGZipB64``."""
        chave = exigir_chave_nfse(chave)
        return self._chamar("consultar_nfse", "GET", f"/nfse/{chave}", chave=chave)

    def dps_processada(self, id_dps: str) -> bool:
        """``HEAD /dps/{id}``: ``True`` se a DPS já gerou NFS-e, ``False`` se a Sefin não a conhece.

        Qualquer outro status (autenticação, 5xx persistente) é ambíguo e levanta
        ``RespostaInvalidaErro``: nesse caso a emissão continua incerta.
        """
        id_dps = exigir_id_dps(id_dps)
        resposta = self._chamar("dps_processada", "HEAD", f"/dps/{id_dps}", id_dps=id_dps)
        if resposta.status in self._existente:
            return True
        if resposta.status in self._inexistente:
            return False
        raise RespostaInvalidaErro(
            f"HEAD /dps respondeu HTTP {resposta.status}: não é possível confirmar a DPS {id_dps}.")

    def chave_da_dps(self, id_dps: str) -> str | None:
        """``GET /dps/{id}``: chave de acesso da NFS-e gerada pela DPS (``None`` se não existe).

        Só funciona se o certificado for de um ator da nota (prestador, tomador ou intermediário).
        """
        id_dps = exigir_id_dps(id_dps)
        resposta = self._chamar("chave_da_dps", "GET", f"/dps/{id_dps}", id_dps=id_dps)
        if resposta.status in self._inexistente:
            return None
        if not 200 <= resposta.status < 300:
            raise RespostaInvalidaErro(
                f"GET /dps respondeu HTTP {resposta.status} para a DPS {id_dps}.")
        chave = campo(ler_json(resposta), "chaveAcesso", "chave", "chNFSe")
        if not chave:
            raise RespostaInvalidaErro("Resposta de GET /dps sem a chave de acesso.")
        return exigir_chave_nfse(str(chave))

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def registrar_evento(self, chave: str, pedido_assinado: bytes) -> RespostaHttp:
        """``POST /nfse/{chave}/eventos`` com ``pedidoRegistroEventoXmlGZipB64``. Sem repetição."""
        chave = exigir_chave_nfse(chave)
        corpo = json.dumps(
            {"pedidoRegistroEventoXmlGZipB64": comprimir_b64(pedido_assinado)}).encode("ascii")
        return self._chamar("registrar_evento", "POST", f"/nfse/{chave}/eventos", corpo=corpo,
                            timeout=self._cfg.timeout_emissao, repetir=False, chave=chave)

    def eventos(self, chave: str, tipo: str | None = None,
                sequencia: int | None = None) -> RespostaHttp:
        """``GET /nfse/{chave}/eventos[/{tipo}[/{sequencia}]]``."""
        chave = exigir_chave_nfse(chave)
        caminho = f"/nfse/{chave}/eventos"
        if tipo is not None:
            caminho += f"/{int(tipo)}"
            if sequencia is not None:
                caminho += f"/{int(sequencia)}"
        return self._chamar("eventos", "GET", caminho, chave=chave)

    # ------------------------------------------------------------------
    # Infra
    # ------------------------------------------------------------------

    def _chamar(self, operacao: str, metodo: str, caminho: str, *, corpo: bytes | None = None,
                timeout: int | None = None, repetir: bool | None = None,
                **referencias: str) -> RespostaHttp:
        repetir = metodo in ("GET", "HEAD") if repetir is None else repetir
        esperas = list(self._esperas) if repetir else []
        timeout = timeout or self._cfg.timeout_consulta
        url = self._cfg.url_sefin(caminho)
        inicio = time.monotonic()
        tentativas = 0
        while True:
            tentativas += 1
            try:
                resposta = self._transporte.requisitar(metodo, url, corpo, timeout=timeout)
            except ComunicacaoErro as erro:
                if esperas:
                    self._pausa(esperas.pop(0))
                    continue
                self._auditar(operacao, metodo, referencias, None, [], tentativas, inicio,
                              erro=str(erro))
                raise
            if esperas and resposta.status in STATUS_REPETIVEIS:
                self._pausa(esperas.pop(0))
                continue
            self._auditar(operacao, metodo, referencias, resposta.status,
                          self._codigos(resposta), tentativas, inicio)
            return resposta

    @staticmethod
    def _codigos(resposta: RespostaHttp) -> list[str]:
        if resposta.status < 400:
            return []
        try:
            return [erro.codigo for erro in extrair_erros(ler_json(resposta))]
        except RespostaInvalidaErro:
            return []

    def _auditar(self, operacao: str, metodo: str, referencias: dict[str, str],
                 status: int | None, codigos: list[str], tentativas: int, inicio: float,
                 erro: str | None = None) -> None:
        self._auditoria.registrar(
            servico="nfse-sefin", operacao=operacao, metodo=metodo,
            ambiente=int(self._cfg.ambiente), status_http=status, codigos=codigos,
            tentativas=tentativas, duracao_ms=int((time.monotonic() - inicio) * 1000),
            erro=erro, **referencias)
