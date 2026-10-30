"""Implementações em memória dos contratos de persistência da NFS-e (somente para testes).

Servem de segundo backend para provar que os contratos (``EstadoEmissao``, ``Trava`` e
``EstadoSequenciaNsu``) não dependem de arquivos e que ``ServicoNfse`` aceita qualquer
implementação injetada. Não são exportadas pela biblioteca.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable

from freedfe.distribuicao.estado import (
    BLOQUEIO_SEGUNDOS,
    EstadoSequenciaNsu,
    Relogio,
    relogio_utc,
)
from freedfe.distribuicao.trava import Trava
from freedfe.excecoes import SincronizacaoEmAndamentoErro
from freedfe.nfse.estado_emissao import EstadoEmissao, RegistroEmissao, SituacaoEmissao


class EstadoEmissaoMemoria(EstadoEmissao):
    """Estado das emissões em um ``dict``, protegido por um ``threading.Lock``.

    ``reservar_proximo`` é atômico sem trava externa (a leitura do próximo número e a reserva
    acontecem sob o mesmo ``Lock``), como exige o contrato. Devolve cópias dos registros, como
    um banco de dados faria: alterar o retorno não altera o estado armazenado.

    ``outra_visao`` cria outra instância sobre o mesmo armazenamento, o que simula outro
    processo lendo o mesmo banco.
    """

    def __init__(self, relogio: Relogio = relogio_utc, *,
                 registros: dict[str, RegistroEmissao] | None = None,
                 trava: threading.Lock | None = None):
        super().__init__(relogio)
        self._registros = registros if registros is not None else {}
        self._trava = trava or threading.Lock()

    def outra_visao(self) -> EstadoEmissaoMemoria:
        """Outra instância sobre os mesmos registros."""
        return EstadoEmissaoMemoria(self._relogio, registros=self._registros, trava=self._trava)

    def obter(self, id_dps: str) -> RegistroEmissao | None:
        with self._trava:
            return copy.deepcopy(self._registros.get(id_dps))

    def listar(self, situacao: SituacaoEmissao | str | None = None) -> list[RegistroEmissao]:
        alvo = SituacaoEmissao(situacao) if situacao is not None else None
        with self._trava:
            itens = [r for r in self._registros.values() if alvo is None or r.situacao is alvo]
            return copy.deepcopy(sorted(itens, key=lambda r: (r.serie, r.numero, r.id_dps)))

    def reservar(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        with self._trava:
            return self._reservar(id_dps, serie, numero)

    def garantir(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        with self._trava:
            existente = self._registros.get(id_dps)
            if existente is not None:
                return copy.deepcopy(existente)
            return self._reservar(id_dps, serie, numero)

    def marcar(self, id_dps: str, situacao: SituacaoEmissao, **dados) -> RegistroEmissao:
        with self._trava:
            registro = self._registros[id_dps]
            self._aplicar_marcacao(registro, situacao, dados)
            return copy.deepcopy(registro)

    def proximo_numero(self, serie: int) -> int:
        with self._trava:
            return self._proximo_numero(serie)

    def reservar_proximo(self, serie: int,
                         montar_id: Callable[[int], str]) -> RegistroEmissao:
        with self._trava:
            numero = self._proximo_numero(serie)
            return self._reservar(montar_id(numero), int(serie), numero)

    def descartar(self, id_dps: str) -> None:
        with self._trava:
            self._validar_descarte(id_dps, self._registros.get(id_dps))
            del self._registros[id_dps]

    # Os métodos abaixo exigem ``self._trava`` já adquirida (``Lock`` não é reentrante).

    def _proximo_numero(self, serie: int) -> int:
        usados = [r.numero for r in self._registros.values() if r.serie == int(serie)]
        return max(usados, default=0) + 1

    def _reservar(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        existente = self._registros.get(id_dps)
        self._validar_reserva(existente)
        registro = self._registro_reservado(existente, id_dps, serie, numero)
        self._registros[id_dps] = registro
        return copy.deepcopy(registro)


class TravaMemoria(Trava):
    """Trava de um recurso baseada em ``threading.Lock``, *fail-fast*.

    Quem não consegue adquirir levanta ``SincronizacaoEmAndamentoErro`` de imediato. Registra
    em ``eventos`` (lista compartilhada) cada aquisição e liberação, para os testes conferirem.
    """

    def __init__(self, recurso: str, trava: threading.Lock,
                 eventos: list[tuple[str, str]] | None = None):
        self.recurso = recurso
        self._trava = trava
        self._eventos = eventos if eventos is not None else []

    def __enter__(self) -> TravaMemoria:
        if not self._trava.acquire(blocking=False):
            raise SincronizacaoEmAndamentoErro(
                f"Recurso em uso por outro processo (trava em memória: {self.recurso}).")
        self._eventos.append(("adquirida", self.recurso))
        return self

    def __exit__(self, *_excecao) -> None:
        self._eventos.append(("liberada", self.recurso))
        self._trava.release()


class TravasMemoria:
    """Fábrica de ``TravaMemoria``: um ``threading.Lock`` por nome de recurso.

    É chamável como ``FabricaTrava``. ``eventos`` guarda ``("adquirida" | "liberada", recurso)``
    na ordem em que ocorreram.
    """

    def __init__(self) -> None:
        self.eventos: list[tuple[str, str]] = []
        self._travas: dict[str, threading.Lock] = {}
        self._tabela = threading.Lock()

    def __call__(self, recurso: str) -> TravaMemoria:
        with self._tabela:
            trava = self._travas.setdefault(recurso, threading.Lock())
        return TravaMemoria(recurso, trava, self.eventos)

    def recursos(self, tipo: str) -> list[str]:
        """Recursos com o evento ``tipo`` (``"adquirida"`` ou ``"liberada"``), em ordem."""
        return [recurso for evento, recurso in self.eventos if evento == tipo]


class EstadoNsuMemoria(EstadoSequenciaNsu):
    """Estado de uma sequência de NSU guardado em um ``dict`` compartilhado (recurso -> dados)."""

    def __init__(self, armazenamento: dict[str, dict], recurso: str,
                 relogio: Relogio = relogio_utc, bloqueio_segundos: int = BLOQUEIO_SEGUNDOS):
        self.armazenamento = armazenamento
        self.recurso = recurso
        super().__init__(relogio, bloqueio_segundos)

    @classmethod
    def fabrica(cls, armazenamento: dict[str, dict], relogio: Relogio = relogio_utc,
                bloqueio_segundos: int = BLOQUEIO_SEGUNDOS) -> Callable[[str], EstadoNsuMemoria]:
        """``FabricaEstadoNsu`` sobre o ``armazenamento`` informado."""
        return lambda recurso: cls(armazenamento, recurso, relogio, bloqueio_segundos)

    def _ler_dados(self) -> dict | None:
        return copy.deepcopy(self.armazenamento.get(self.recurso))

    def _gravar_dados(self, dados: dict) -> None:
        self.armazenamento[self.recurso] = copy.deepcopy(dados)
