"""Estado persistido das emissões de NFS-e: máquina de estados e numeração local.

Fluxo (relatório técnico, seção 5)::

    PENDENTE --(assinada e gravada)--> ENVIADA --(201)-------------> AUTORIZADA
                                          |--(4xx com erros)-------> REJEITADA
                                          '--(timeout ou 5xx)-----> INCERTA
    INCERTA --(HEAD e GET /dps na Sefin)--> AUTORIZADA

``PENDENTE`` significa que a DPS foi reservada e **nunca** enviada. ``ENVIADA`` e ``INCERTA``
significam que a Sefin pode ter processado a DPS: só se reenvia depois de consultar a Sefin.
O ERP continua sendo a fonte oficial da numeração; a numeração local só evita colisões.

``EstadoEmissao`` é o contrato; ``EstadoEmissaoArquivo`` é a implementação padrão (um JSON, para
um único processo ou processos que compartilham o disco). Para vários processos ou contêineres
sem volume compartilhado, injete uma implementação em banco de dados (ver README).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
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


class EstadoEmissao(ABC):
    """Contrato do estado das emissões: uma entrada por Id de DPS e a numeração por série.

    Garantias que toda implementação deve oferecer (verificadas por ``ContratoEstadoEmissao``,
    em ``tests/contrato_estado_emissao.py``):

    - **Sem cache entre operações:** cada chamada lê o estado atual da fonte, porque outro
      processo pode tê-lo alterado.
    - **``reservar_proximo`` é atômico:** duas chamadas concorrentes na mesma série nunca
      recebem o mesmo número, mesmo sem trava. Num banco, isso significa ``UNIQUE (emitente,
      serie, numero)`` mais ``FOR UPDATE`` ou *advisory lock*. O Id da DPS depende do número,
      por isso a chamada recebe ``montar_id``, que devolve o Id a partir do número escolhido.
    - **``reservar``** levanta ``ValidacaoErro`` se a DPS está ``ENVIADA``, ``INCERTA`` ou
      ``AUTORIZADA``. Se está ``PENDENTE`` ou ``REJEITADA``, reabre: limpa ``erros`` e
      ``motivo`` e preserva ``tentativas`` e ``ts_reserva``.
    - **``marcar``** rejeita campo desconhecido com ``ValidacaoErro`` **antes** de alterar o
      registro; incrementa ``tentativas`` ao marcar ``ENVIADA``; preenche ``ts_envio`` (em
      ``ENVIADA``) e ``ts_resposta`` (em ``AUTORIZADA``, ``REJEITADA`` e ``INCERTA``). Marcar
      um Id inexistente levanta ``KeyError``.
    - **``listar``** ordena por ``(serie, numero, id_dps)``.
    - **``descartar``** só remove registro ``PENDENTE`` com ``tentativas == 0``; nos demais
      casos (inclusive Id inexistente) levanta ``ValidacaoErro``. Serve para devolver o número
      quando a DPS falhou localmente antes de qualquer envio.

    As regras de transição ficam nos auxiliares concretos desta classe
    (``_validar_reserva``, ``_registro_reservado``, ``_validar_descarte``,
    ``_aplicar_marcacao`` e ``_agora``): reutilize-os nas implementações para que cada regra
    exista num lugar só. Eles não persistem nada.

    Args:
        relogio: relógio dos carimbos de data e hora (``ts_*``).
    """

    def __init__(self, relogio: Relogio = relogio_utc):
        self._relogio = relogio

    @abstractmethod
    def obter(self, id_dps: str) -> RegistroEmissao | None:
        """Registro da DPS, se existir."""

    @abstractmethod
    def listar(self, situacao: SituacaoEmissao | str | None = None) -> list[RegistroEmissao]:
        """Registros (opcionalmente de uma situação), ordenados por série, número e Id."""

    @abstractmethod
    def reservar(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        """Cria (ou reabre, após rejeição) o registro como ``PENDENTE``.

        Levanta ``ValidacaoErro`` se a DPS já foi enviada e ainda não foi rejeitada: o
        chamador deve reconciliar em vez de reservar de novo.
        """

    @abstractmethod
    def garantir(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        """Registro existente ou um novo ``PENDENTE`` (usado ao reconciliar DPS desconhecidas)."""

    @abstractmethod
    def marcar(self, id_dps: str, situacao: SituacaoEmissao, **dados) -> RegistroEmissao:
        """Muda a situação e grava os campos informados (``chave``, ``erros``, ``motivo``...)."""

    @abstractmethod
    def proximo_numero(self, serie: int) -> int:
        """Maior número já usado na série + 1 (lacunas não são vedadas; duplicidade gera E0014).

        Apenas consulta: não reserva nada. Para numerar e reservar numa só operação, use
        ``reservar_proximo``.
        """

    @abstractmethod
    def reservar_proximo(self, serie: int,
                         montar_id: Callable[[int], str]) -> RegistroEmissao:
        """Reserva, como ``PENDENTE``, o próximo número da série.

        Args:
            serie: série da DPS.
            montar_id: devolve o Id da DPS (45 posições) para o número escolhido.

        Returns:
            O registro reservado, com ``numero`` e ``id_dps``.
        """

    @abstractmethod
    def descartar(self, id_dps: str) -> None:
        """Remove o registro ``PENDENTE`` nunca enviado (``tentativas == 0``).

        Levanta ``ValidacaoErro`` nos demais casos.
        """

    # ------------------------------------------------------------------
    # Regras compartilhadas pelas implementações (não persistem nada)
    # ------------------------------------------------------------------

    def _validar_reserva(self, existente: RegistroEmissao | None) -> None:
        """Levanta ``ValidacaoErro`` se ``existente`` já foi enviada e não foi rejeitada."""
        if existente is not None and existente.situacao not in (
                SituacaoEmissao.PENDENTE, SituacaoEmissao.REJEITADA):
            raise ValidacaoErro(f"A DPS {existente.id_dps} já está {existente.situacao.value}.")

    def _registro_reservado(self, existente: RegistroEmissao | None, id_dps: str, serie: int,
                            numero: int) -> RegistroEmissao:
        """Registro ``PENDENTE`` novo ou reaberto (altera ``existente`` quando informado).

        Reabrir limpa ``erros`` e ``motivo`` e preserva ``tentativas`` e ``ts_reserva``. Quem
        chama deve ter validado a reserva (``_validar_reserva``) e persistir o resultado.
        """
        registro = existente or RegistroEmissao(id_dps, serie, numero)
        registro.situacao = SituacaoEmissao.PENDENTE
        registro.erros, registro.motivo = [], None
        registro.ts_reserva = registro.ts_reserva or self._agora()
        return registro

    def _validar_descarte(self, id_dps: str, registro: RegistroEmissao | None) -> None:
        """Levanta ``ValidacaoErro`` se ``registro`` não é um ``PENDENTE`` nunca enviado."""
        if registro is None:
            raise ValidacaoErro(f"A DPS {id_dps} não consta no estado de emissão.")
        if registro.situacao is not SituacaoEmissao.PENDENTE or registro.tentativas != 0:
            raise ValidacaoErro(
                f"A DPS {id_dps} não pode ser descartada: está {registro.situacao.value} com "
                f"{registro.tentativas} tentativa(s) de envio.")

    def _aplicar_marcacao(self, registro: RegistroEmissao, situacao: SituacaoEmissao,
                          dados: dict) -> None:
        """Aplica a transição de ``marcar`` a ``registro`` (altera o objeto, sem persistir).

        Valida a situação e os nomes dos campos **antes** de alterar qualquer coisa: se levantar
        ``ValidacaoErro`` (campo desconhecido) ou ``ValueError`` (situação inválida), o registro
        fica intacto.
        """
        situacao = SituacaoEmissao(situacao)
        desconhecidos = [nome for nome in dados if nome not in RegistroEmissao.__dataclass_fields__]
        if desconhecidos:
            raise ValidacaoErro(
                f"Campo desconhecido no estado de emissão: {', '.join(desconhecidos)}")
        registro.situacao = situacao
        if situacao is SituacaoEmissao.ENVIADA:
            registro.tentativas += 1
            registro.ts_envio = self._agora()
        elif situacao in (SituacaoEmissao.AUTORIZADA, SituacaoEmissao.REJEITADA,
                          SituacaoEmissao.INCERTA):
            registro.ts_resposta = self._agora()
        for nome, valor in dados.items():
            setattr(registro, nome, valor)

    def _agora(self) -> str:
        """Instante atual do relógio injetado, em ISO 8601."""
        return self._relogio().isoformat()


class EstadoEmissaoArquivo(EstadoEmissao):
    """Estado em JSON (escrita atômica a cada alteração), uma entrada por Id de DPS.

    Implementação padrão de ``EstadoEmissao``. Relê o arquivo no início de **todo** método
    público, de modo que enxerga as alterações de outros processos que compartilham o disco.

    Não faz controle de concorrência: a atomicidade de ``reservar_proximo`` (leitura do próximo
    número e gravação da reserva) vem da trava que o chamador segura, a ``TravaArquivo`` do
    emitente (``ServicoNfse`` já a segura em toda operação de emissão). Sem ela, dois
    processos podem receber o mesmo número e sobrescrever as gravações um do outro.
    """

    def __init__(self, arquivo: str | Path, relogio: Relogio = relogio_utc):
        super().__init__(relogio)
        self.arquivo = Path(arquivo)
        self._registros: dict[str, RegistroEmissao] = {}
        self._carregar()

    def obter(self, id_dps: str) -> RegistroEmissao | None:
        """Registro da DPS, se existir."""
        self._carregar()
        return self._registros.get(id_dps)

    def listar(self, situacao: SituacaoEmissao | str | None = None) -> list[RegistroEmissao]:
        """Registros (opcionalmente de uma situação), ordenados por série, número e Id."""
        self._carregar()
        alvo = SituacaoEmissao(situacao) if situacao is not None else None
        itens = [r for r in self._registros.values() if alvo is None or r.situacao is alvo]
        return sorted(itens, key=lambda r: (r.serie, r.numero, r.id_dps))

    def reservar(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        """Cria (ou reabre, após rejeição) o registro como ``PENDENTE``.

        Levanta ``ValidacaoErro`` se a DPS já foi enviada e ainda não foi rejeitada: o
        chamador deve reconciliar em vez de reservar de novo.
        """
        self._carregar()
        existente = self._registros.get(id_dps)
        self._validar_reserva(existente)
        registro = self._registro_reservado(existente, id_dps, serie, numero)
        self._registros[id_dps] = registro
        self._salvar()
        return registro

    def garantir(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        """Registro existente ou um novo ``PENDENTE`` (usado ao reconciliar DPS desconhecidas)."""
        existente = self.obter(id_dps)
        if existente is not None:
            return existente
        return self.reservar(id_dps, serie, numero)

    def marcar(self, id_dps: str, situacao: SituacaoEmissao, **dados) -> RegistroEmissao:
        """Muda a situação e grava os campos informados (``chave``, ``erros``, ``motivo``...)."""
        self._carregar()
        registro = self._registros[id_dps]
        self._aplicar_marcacao(registro, situacao, dados)
        self._salvar()
        return registro

    def proximo_numero(self, serie: int) -> int:
        """Maior número já usado na série + 1 (lacunas não são vedadas; duplicidade gera E0014)."""
        self._carregar()
        usados = [r.numero for r in self._registros.values() if r.serie == int(serie)]
        return max(usados, default=0) + 1

    def reservar_proximo(self, serie: int,
                         montar_id: Callable[[int], str]) -> RegistroEmissao:
        """Reserva o próximo número da série: ``proximo_numero`` seguido de ``reservar``.

        A atomicidade vem da trava do chamador (ver a docstring da classe), não deste método.
        """
        numero = self.proximo_numero(serie)
        return self.reservar(montar_id(numero), int(serie), numero)

    def descartar(self, id_dps: str) -> None:
        """Remove o registro ``PENDENTE`` nunca enviado (``tentativas == 0``) e salva."""
        self._carregar()
        self._validar_descarte(id_dps, self._registros.get(id_dps))
        del self._registros[id_dps]
        self._salvar()

    def _carregar(self) -> None:
        """Relê o JSON; arquivo ausente equivale a estado vazio."""
        self._registros = {}
        if self.arquivo.exists():
            dados = json.loads(self.arquivo.read_text("utf-8"))
            self._registros = {
                id_dps: RegistroEmissao.de_dict(item)
                for id_dps, item in dados.get("emissoes", {}).items()}

    def _salvar(self) -> None:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        conteudo = {"emissoes": {i: r.para_dict() for i, r in sorted(self._registros.items())}}
        temporario = self.arquivo.with_suffix(".tmp")
        temporario.write_text(json.dumps(conteudo, indent=1, ensure_ascii=False), "utf-8")
        temporario.replace(self.arquivo)
