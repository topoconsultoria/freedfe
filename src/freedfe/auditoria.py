"""Trilha de auditoria das chamadas aos Web Services: contrato e implementação em JSON Lines."""

from __future__ import annotations

import datetime as dt
import json
from abc import ABC, abstractmethod
from pathlib import Path


class Auditoria(ABC):
    """Contrato da trilha de auditoria: um registro por chamada ou por conclusão de emissão.

    Os registros guardam códigos, status, Ids e durações, **nunca XML** (contém CPF e valores).
    Uma falha de gravação deve ser propagada, não engolida: a trilha é evidência.
    """

    @abstractmethod
    def registrar(self, **dados) -> None:
        """Registra uma entrada com os campos informados (a implementação acrescenta o instante)."""


class RegistroAuditoria(Auditoria):
    """Acrescenta uma linha JSON por chamada em ``chamadas.jsonl``.

    O arquivo é somente-acréscimo: serve para rastrear cStat, NSU enviado/recebido
    e quantidade de documentos de cada consulta.
    """

    def __init__(self, arquivo: str | Path):
        self.arquivo = Path(arquivo)

    def registrar(self, **dados) -> None:
        """Grava o registro com carimbo de tempo UTC."""
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        registro = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), **dados}
        with self.arquivo.open("a", encoding="utf-8") as saida:
            saida.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
