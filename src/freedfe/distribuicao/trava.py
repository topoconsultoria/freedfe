"""Travas de exclusão mútua: contrato (``Trava``) e implementação em arquivo (``TravaArquivo``)."""

from __future__ import annotations

import contextlib
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from freedfe.excecoes import SincronizacaoEmAndamentoErro

VALIDADE_TRAVA_SEGUNDOS = 2 * 3600  # trava mais antiga que isso é considerada abandonada


class Trava(ABC):
    """Contrato de uma trava exclusiva por recurso, usada como gerenciador de contexto.

    Garantias que toda implementação deve oferecer:

    - **exclusividade por recurso** entre todos os processos (e contêineres) que usam o mesmo
      backend: enquanto uma trava está adquirida, nenhuma outra aquisição do mesmo recurso
      tem sucesso;
    - quem não consegue a trava levanta ``SincronizacaoEmAndamentoErro``, de imediato
      (*fail-fast*, como ``TravaArquivo``) ou após um timeout configurado na implementação;
    - a trava é **liberada mesmo quando ocorre exceção** dentro do bloco ``with``.

    A trava de emissão de NFS-e dura todo o ``POST`` à Sefin. Um backend em banco de dados deve
    usá-la numa conexão dedicada (por exemplo, ``pg_try_advisory_lock`` de sessão), nunca numa
    transação mantida aberta durante a chamada HTTP.
    """

    @abstractmethod
    def __enter__(self) -> Trava:
        """Adquire a trava ou levanta ``SincronizacaoEmAndamentoErro``."""

    @abstractmethod
    def __exit__(self, *_excecao) -> None:
        """Libera a trava, inclusive quando o bloco terminou em exceção."""


#: Fábrica de travas: recebe o nome do recurso (``emissao_nfse``, ``nsu_nfse``...).
FabricaTrava = Callable[[str], Trava]


class TravaArquivo(Trava):
    """Trava exclusiva baseada em criação atômica de arquivo (``O_EXCL``).

    Dois processos consumindo o mesmo CNPJ quebram a sequência de NSU e geram
    rejeição 656. Uma trava abandonada (processo morto) expira após 2 horas.

    Falha de imediato (*fail-fast*) se o arquivo já existe. Só vale entre processos que
    enxergam o mesmo sistema de arquivos: entre contêineres sem volume compartilhado, injete
    outra implementação de ``Trava``.
    """

    def __init__(self, arquivo: str | Path, validade_segundos: int = VALIDADE_TRAVA_SEGUNDOS):
        self.arquivo = Path(arquivo)
        self._validade = validade_segundos

    def __enter__(self) -> TravaArquivo:
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        self._remover_se_abandonada()
        try:
            descritor = os.open(self.arquivo, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as erro:
            raise SincronizacaoEmAndamentoErro(
                f"Sequência em uso por outro processo (trava: {self.arquivo}).") from erro
        with os.fdopen(descritor, "w") as saida:
            saida.write(f"pid={os.getpid()} ts={time.time():.0f}\n")
        return self

    def __exit__(self, *_excecao) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.arquivo.unlink()

    def _remover_se_abandonada(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            if time.time() - self.arquivo.stat().st_mtime > self._validade:
                self.arquivo.unlink()
