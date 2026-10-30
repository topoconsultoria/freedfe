"""Estado local da sequência de NSU e do controle de consumo indevido."""

from __future__ import annotations

import datetime as dt
import json
from abc import ABC, abstractmethod
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


class EstadoSequenciaNsu(ABC):
    """Estado de uma sequência de NSU e do controle de consumo indevido (*template method*).

    Toda a regra fica aqui; as subclasses só dizem onde o estado é lido e gravado
    (``_ler_dados`` e ``_gravar_dados``). Trocar o arquivo JSON por um banco de dados exige
    apenas uma nova subclasse.

    O bloqueio padrão (1 h + 5 min) é o da NF-e; a NFS-e Nacional não tem essa regra e usa um
    intervalo configurável (``bloqueio_segundos``) para espaçar as consultas ao ADN.

    Regras (NT 2014.002 item 3.11.4):
        - A continuação usa somente o ``ultNSU`` devolvido pela SEFAZ.
        - Após 137, ``ultNSU == maxNSU`` ou consumo indevido: bloqueio local de 1 h + 5 min.
        - Consultas pontuais (``consNSU``/``consChNFe``): no máximo 20 por hora.

    O estado é lido uma vez, na construção, e gravado a cada ``salvar``. Quem consome a
    sequência deve segurar uma ``Trava`` do recurso durante todo o ciclo ler-alterar-salvar.
    As subclasses devem preparar os atributos de que ``_ler_dados`` depende **antes** de chamar
    ``super().__init__``.

    Args:
        relogio: relógio usado nos bloqueios e na última execução.
        bloqueio_segundos: duração do bloqueio local de ``bloquear``.
    """

    def __init__(self, relogio: Relogio = relogio_utc,
                 bloqueio_segundos: int = BLOQUEIO_SEGUNDOS):
        self._relogio = relogio
        self._bloqueio = bloqueio_segundos
        self._dados = {"ult_nsu": NSU_INICIAL, "max_nsu": None, "bloqueado_ate": None,
                       "consultas_pontuais": [], "ultimo_cstat": None,
                       "ultima_execucao": None}
        persistidos = self._ler_dados()
        if persistidos:
            self._dados.update(persistidos)

    @abstractmethod
    def _ler_dados(self) -> dict | None:
        """Estado persistido (as mesmas chaves de ``como_dict``), ou ``None`` se não existir."""

    @abstractmethod
    def _gravar_dados(self, dados: dict) -> None:
        """Persiste o estado completo, de forma atômica.

        ``dados`` é o dicionário de trabalho da instância: a implementação deve gravar uma cópia
        e não reter a referência.
        """

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
        """Bloqueia novas consultas por ``bloqueio_segundos`` (padrão 1 h + margem da NF-e)."""
        ate = self._relogio() + dt.timedelta(seconds=self._bloqueio)
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
        """Persiste o estado pelo backend da subclasse (``_gravar_dados``)."""
        self._gravar_dados(self._dados)

    def como_dict(self) -> dict:
        """Cópia do estado atual, com os segundos restantes de bloqueio."""
        return {**self._dados, "liberado_em_s": self.segundos_para_liberar()}


#: Fábrica de estados de NSU: recebe o recurso (``nsu_nfse`` ou ``nsu_nfse_<cnpj>``).
FabricaEstadoNsu = Callable[[str], EstadoSequenciaNsu]


class EstadoNSU(EstadoSequenciaNsu):
    """Estado persistido em JSON por (modelo, ambiente, interessado).

    Implementação em arquivo de ``EstadoSequenciaNsu``: um JSON por sequência, com escrita
    atômica (arquivo temporário + ``rename``). As regras estão na classe base.
    """

    def __init__(self, arquivo: str | Path, relogio: Relogio = relogio_utc,
                 bloqueio_segundos: int = BLOQUEIO_SEGUNDOS):
        self.arquivo = Path(arquivo)
        super().__init__(relogio, bloqueio_segundos)

    def _ler_dados(self) -> dict | None:
        if not self.arquivo.exists():
            return None
        return json.loads(self.arquivo.read_text("utf-8"))

    def _gravar_dados(self, dados: dict) -> None:
        """Escrita atômica (arquivo temporário + rename)."""
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.arquivo.with_suffix(".tmp")
        temporario.write_text(json.dumps(dados, indent=2, ensure_ascii=False), "utf-8")
        temporario.replace(self.arquivo)
