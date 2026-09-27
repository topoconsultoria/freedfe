"""Trava de arquivo: garante um único consumidor por sequência de NSU."""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

from freedfe.excecoes import SincronizacaoEmAndamentoErro

VALIDADE_TRAVA_SEGUNDOS = 2 * 3600  # trava mais antiga que isso é considerada abandonada


class TravaArquivo:
    """Trava exclusiva baseada em criação atômica de arquivo (``O_EXCL``).

    Dois processos consumindo o mesmo CNPJ quebram a sequência de NSU e geram
    rejeição 656. Uma trava abandonada (processo morto) expira após 2 horas.
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
