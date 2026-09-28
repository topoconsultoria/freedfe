"""Estado local da sequência de NSU e do controle de consumo indevido."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

from freedfe.excecoes import LimiteConsultaErro

BLOQUEIO_SEGUNDOS = 3600 + 5 * 60  # 1 h exigida pela NT + 5 min de margem de relógio
LIMITE_CONSULTAS_PONTUAIS_HORA = 20
NSU_INICIAL = "0" * 15

Relogio = Callable[[], dt.datetime]


def relogio_utc() -> dt.datetime:
    """Relógio padrão (UTC). Pode ser substituído em testes."""
    return dt.datetime.now(dt.timezone.utc)


class EstadoNSU:
    """Estado persistido em JSON por (modelo, ambiente, interessado).

    Regras (NT 2014.002 item 3.11.4):
        - A continuação usa somente o ``ultNSU`` devolvido pela SEFAZ.
        - Após 137, ``ultNSU == maxNSU`` ou consumo indevido: bloqueio local de 1 h + 5 min.
        - Consultas pontuais (``consNSU``/``consChNFe``): no máximo 20 por hora.
    """

    def __init__(self, arquivo: str | Path, relogio: Relogio = relogio_utc):
        self.arquivo = Path(arquivo)
        self._relogio = relogio
        self._dados = {"ult_nsu": NSU_INICIAL, "max_nsu": None, "bloqueado_ate": None,
                       "consultas_pontuais": [], "ultimo_cstat": None,
                       "ultima_execucao": None}
        if self.arquivo.exists():
            self._dados.update(json.loads(self.arquivo.read_text("utf-8")))

    @property
    def ult_nsu(self) -> str:
        """Último NSU recebido (ponto de continuação da ``distNSU``)."""
        return self._dados["ult_nsu"]

    @property
    def max_nsu(self) -> str | None:
        """Maior NSU existente na SEFAZ no momento da última consulta."""
        return self._dados["max_nsu"]

    @property
    def ultimo_cstat(self) -> str | None:
        """cStat da última consulta de distribuição."""
        return self._dados["ultimo_cstat"]

    @property
    def ultima_execucao(self) -> str | None:
        """Momento (ISO 8601, UTC) da última consulta de distribuição."""
        return self._dados["ultima_execucao"]

    def atualizar_sequencia(self, ult_nsu: str | None, max_nsu: str | None) -> None:
        """Adota o ``ultNSU``/``maxNSU`` devolvidos (inclusive no 656, NT v1.14)."""
        if ult_nsu:
            self._dados["ult_nsu"] = ult_nsu
        if max_nsu:
            self._dados["max_nsu"] = max_nsu

    def registrar_execucao(self, cstat: str) -> None:
        """Registra cStat e horário da última consulta de distribuição."""
        self._dados["ultimo_cstat"] = cstat
        self._dados["ultima_execucao"] = self._relogio().isoformat()

    def bloquear(self) -> None:
        """Bloqueia novas ``distNSU`` por 1 h + margem."""
        ate = self._relogio() + dt.timedelta(seconds=BLOQUEIO_SEGUNDOS)
        self._dados["bloqueado_ate"] = ate.isoformat()

    def segundos_para_liberar(self) -> int:
        """Segundos restantes de bloqueio (0 se liberado)."""
        ate = self._dados.get("bloqueado_ate")
        if not ate:
            return 0
        falta = dt.datetime.fromisoformat(ate) - self._relogio()
        return max(0, int(falta.total_seconds()))

    def registrar_consulta_pontual(self) -> None:
        """Conta uma consulta pontual; levanta ``LimiteConsultaErro`` acima de 20/hora."""
        agora = self._relogio().timestamp()
        janela = [t for t in self._dados.get("consultas_pontuais", []) if agora - t < 3600]
        if len(janela) >= LIMITE_CONSULTAS_PONTUAIS_HORA:
            raise LimiteConsultaErro(
                f"Limite local de {LIMITE_CONSULTAS_PONTUAIS_HORA} consultas pontuais/hora "
                "atingido (evita rejeição 656). Use a sincronização para carga em massa.")
        janela.append(agora)
        self._dados["consultas_pontuais"] = janela

    def salvar(self) -> None:
        """Persiste o estado com escrita atômica (arquivo temporário + rename)."""
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.arquivo.with_suffix(".tmp")
        temporario.write_text(json.dumps(self._dados, indent=2, ensure_ascii=False), "utf-8")
        temporario.replace(self.arquivo)

    def como_dict(self) -> dict:
        """Cópia do estado atual, com os segundos restantes de bloqueio."""
        return {**self._dados, "liberado_em_s": self.segundos_para_liberar()}
