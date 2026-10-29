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
from freedfe.excecoes import RespostaInvalidaErro
from freedfe.nfse.cliente_base import ESPERAS_PADRAO, STATUS_REPETIVEIS, ClienteNfseBase
from freedfe.nfse.envelope import comprimir_b64
from freedfe.nfse.resposta import campo, ler_json
from freedfe.servicos import ConfiguracaoNfse
from freedfe.transporte import RespostaHttp, TransporteRest
from freedfe.validadores import exigir_chave_nfse, exigir_id_dps

__all__ = ["ESPERAS_PADRAO", "STATUS_REPETIVEIS", "ClienteSefin"]


class ClienteSefin(ClienteNfseBase):
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

    servico = "nfse-sefin"

    def __init__(self, configuracao: ConfiguracaoNfse, transporte: TransporteRest,
                 auditoria: RegistroAuditoria,
                 pausa: Callable[[float], None] = time.sleep,
                 esperas: tuple[float, ...] = ESPERAS_PADRAO,
                 status_dps_existente: frozenset[int] = frozenset({200}),
                 status_dps_inexistente: frozenset[int] = frozenset({404})):
        super().__init__(configuracao, transporte, auditoria, pausa, esperas)
        self._existente = status_dps_existente
        self._inexistente = status_dps_inexistente

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

    def _chamar(self, operacao: str, metodo: str, caminho: str, **opcoes) -> RespostaHttp:
        return self._requisitar(operacao, metodo, self._cfg.url_sefin(caminho), **opcoes)
