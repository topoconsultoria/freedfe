"""Base dos clientes REST da NFS-e Nacional: repetição de consultas e auditoria sem XML."""

from __future__ import annotations

import time
from collections.abc import Callable

from freedfe.auditoria import Auditoria
from freedfe.excecoes import ComunicacaoErro, RespostaInvalidaErro
from freedfe.nfse.resposta import extrair_erros, ler_json
from freedfe.servicos import ConfiguracaoNfse
from freedfe.transporte import RespostaHttp, TransporteRest

ESPERAS_PADRAO = (2.0, 4.0, 8.0)
STATUS_REPETIVEIS = frozenset({408, 425, 429, 500, 502, 503, 504})


class ClienteNfseBase:
    """Chamadas HTTP com a política comum dos serviços da NFS-e Nacional.

    ``GET``/``HEAD`` são repetidos em falha de rede ou status transitório, com as esperas
    informadas; ``POST`` nunca é repetido. Cada chamada gera uma linha de auditoria com status,
    códigos de erro, tentativas e duração, **sem XML** (contém CPF e valores).
    """

    #: Rótulo do serviço na trilha de auditoria.
    servico = "nfse"

    def __init__(self, configuracao: ConfiguracaoNfse, transporte: TransporteRest,
                 auditoria: Auditoria,
                 pausa: Callable[[float], None] = time.sleep,
                 esperas: tuple[float, ...] = ESPERAS_PADRAO):
        self._cfg = configuracao
        self._transporte = transporte
        self._auditoria = auditoria
        self._pausa = pausa
        self._esperas = esperas

    @property
    def configuracao(self) -> ConfiguracaoNfse:
        """Configuração (URLs) em uso."""
        return self._cfg

    def _requisitar(self, operacao: str, metodo: str, url: str, *, corpo: bytes | None = None,
                    timeout: int | None = None, repetir: bool | None = None,
                    **referencias: str) -> RespostaHttp:
        repetir = metodo in ("GET", "HEAD") if repetir is None else repetir
        esperas = list(self._esperas) if repetir else []
        timeout = timeout or self._cfg.timeout_consulta
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
            servico=self.servico, operacao=operacao, metodo=metodo,
            ambiente=int(self._cfg.ambiente), status_http=status, codigos=codigos,
            tentativas=tentativas, duracao_ms=int((time.monotonic() - inicio) * 1000),
            erro=erro, **referencias)
