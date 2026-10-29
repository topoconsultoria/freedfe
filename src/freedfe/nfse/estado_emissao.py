"""Estado persistido das emissões de NFS-e: máquina de estados e numeração local.

Fluxo (relatório técnico, seção 5)::

    PENDENTE --(assinada e gravada)--> ENVIADA --(201)-------------> AUTORIZADA
                                          |--(4xx com erros)-------> REJEITADA
                                          '--(timeout ou 5xx)-----> INCERTA
    INCERTA --(HEAD e GET /dps na Sefin)--> AUTORIZADA

``PENDENTE`` significa que a DPS foi reservada e **nunca** enviada. ``ENVIADA`` e ``INCERTA``
significam que a Sefin pode ter processado a DPS: só se reenvia depois de consultar a Sefin.
O ERP continua sendo a fonte oficial da numeração; a numeração local só evita colisões.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from freedfe.distribuicao.estado import Relogio, relogio_utc
from freedfe.excecoes import ValidacaoErro


class SituacaoEmissao(str, Enum):
    """Situação local de uma DPS emitida pela biblioteca."""

    PENDENTE = "pendente"
    ENVIADA = "enviada"
    AUTORIZADA = "autorizada"
    REJEITADA = "rejeitada"
    INCERTA = "incerta"


#: Situações em que a Sefin pode ter processado a DPS sem que a biblioteca saiba.
SITUACOES_A_RECONCILIAR = (SituacaoEmissao.ENVIADA, SituacaoEmissao.INCERTA)


@dataclass
class RegistroEmissao:
    """Linha do estado: uma DPS identificada pelo seu Id (45 posições)."""

    id_dps: str
    serie: int
    numero: int
    situacao: SituacaoEmissao = SituacaoEmissao.PENDENTE
    chave: str | None = None
    erros: list[dict] = field(default_factory=list)
    motivo: str | None = None
    tentativas: int = 0
    caminho_dps: str | None = None
    caminho_resposta: str | None = None
    ts_reserva: str | None = None
    ts_envio: str | None = None
    ts_resposta: str | None = None

    def para_dict(self) -> dict:
        """Representação serializável em JSON."""
        dados = asdict(self)
        dados["situacao"] = self.situacao.value
        return dados

    @classmethod
    def de_dict(cls, dados: dict) -> RegistroEmissao:
        """Reconstrói a partir de ``para_dict`` (ignora chaves desconhecidas)."""
        nomes = set(cls.__dataclass_fields__)
        valores = {k: v for k, v in dados.items() if k in nomes}
        valores["situacao"] = SituacaoEmissao(valores["situacao"])
        return cls(**valores)


class EstadoEmissao:
    """Estado em JSON (escrita atômica a cada alteração), uma entrada por Id de DPS.

    Não faz controle de concorrência: quem emite deve segurar a ``TravaArquivo`` do emitente.
    """

    def __init__(self, arquivo: str | Path, relogio: Relogio = relogio_utc):
        self.arquivo = Path(arquivo)
        self._relogio = relogio
        self._registros: dict[str, RegistroEmissao] = {}
        if self.arquivo.exists():
            dados = json.loads(self.arquivo.read_text("utf-8"))
            self._registros = {
                id_dps: RegistroEmissao.de_dict(item)
                for id_dps, item in dados.get("emissoes", {}).items()}

    def obter(self, id_dps: str) -> RegistroEmissao | None:
        """Registro da DPS, se existir."""
        return self._registros.get(id_dps)

    def listar(self, situacao: SituacaoEmissao | str | None = None) -> list[RegistroEmissao]:
        """Registros (opcionalmente de uma situação), ordenados por série e número."""
        alvo = SituacaoEmissao(situacao) if situacao is not None else None
        itens = [r for r in self._registros.values() if alvo is None or r.situacao is alvo]
        return sorted(itens, key=lambda r: (r.serie, r.numero, r.id_dps))

    def reservar(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        """Cria (ou reabre, após rejeição) o registro como ``PENDENTE``.

        Levanta ``ValidacaoErro`` se a DPS já foi enviada e ainda não foi rejeitada: o
        chamador deve reconciliar em vez de reservar de novo.
        """
        existente = self._registros.get(id_dps)
        if existente is not None and existente.situacao not in (
                SituacaoEmissao.PENDENTE, SituacaoEmissao.REJEITADA):
            raise ValidacaoErro(f"A DPS {id_dps} já está {existente.situacao.value}.")
        registro = existente or RegistroEmissao(id_dps, serie, numero)
        registro.situacao = SituacaoEmissao.PENDENTE
        registro.erros, registro.motivo = [], None
        registro.ts_reserva = registro.ts_reserva or self._agora()
        self._registros[id_dps] = registro
        self._salvar()
        return registro

    def garantir(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        """Registro existente ou um novo ``PENDENTE`` (usado ao reconciliar DPS desconhecidas)."""
        return self._registros.get(id_dps) or self.reservar(id_dps, serie, numero)

    def marcar(self, id_dps: str, situacao: SituacaoEmissao, **dados) -> RegistroEmissao:
        """Muda a situação e grava os campos informados (``chave``, ``erros``, ``motivo``...)."""
        registro = self._registros[id_dps]
        registro.situacao = SituacaoEmissao(situacao)
        if situacao is SituacaoEmissao.ENVIADA:
            registro.tentativas += 1
            registro.ts_envio = self._agora()
        elif situacao in (SituacaoEmissao.AUTORIZADA, SituacaoEmissao.REJEITADA,
                          SituacaoEmissao.INCERTA):
            registro.ts_resposta = self._agora()
        for nome, valor in dados.items():
            if nome not in RegistroEmissao.__dataclass_fields__:
                raise ValidacaoErro(f"Campo desconhecido no estado de emissão: {nome}")
            setattr(registro, nome, valor)
        self._salvar()
        return registro

    def proximo_numero(self, serie: int) -> int:
        """Maior número já usado na série + 1 (lacunas não são vedadas; duplicidade gera E0014)."""
        usados = [r.numero for r in self._registros.values() if r.serie == int(serie)]
        return max(usados, default=0) + 1

    def _agora(self) -> str:
        return self._relogio().isoformat()

    def _salvar(self) -> None:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        conteudo = {"emissoes": {i: r.para_dict() for i, r in sorted(self._registros.items())}}
        temporario = self.arquivo.with_suffix(".tmp")
        temporario.write_text(json.dumps(conteudo, indent=1, ensure_ascii=False), "utf-8")
        temporario.replace(self.arquivo)
