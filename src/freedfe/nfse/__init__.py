"""NFS-e Nacional (Sefin Nacional / ADN): emissão, consulta, eventos e DANFSe.

O ponto de entrada para o usuário é ``FreeDFe.nfse`` (ver :class:`ServicoNfse`), ou
``criar_servico_nfse`` para uma aplicação sem pasta local. Os módulos deste pacote são
desacoplados e testáveis sem rede.

Os contratos de persistência (``EstadoEmissao``, ``Trava``, ``EstadoSequenciaNsu``,
``CacheParametros``, ``ArmazenamentoEvidencias`` e ``Auditoria``) são reexportados aqui, com as
implementações em arquivo, para quem implementa um backend próprio (por exemplo, PostgreSQL).

``criar_servico_nfse`` é reexportado sob demanda: o módulo da fábrica importa ``ServicoNfse``, que
depende de pacotes que por sua vez importam este (``freedfe.nfse.leiaute``), e um import
imediato aqui fecharia um ciclo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from freedfe.auditoria import Auditoria, RegistroAuditoria
from freedfe.distribuicao.estado import EstadoNSU, EstadoSequenciaNsu, FabricaEstadoNsu
from freedfe.distribuicao.trava import FabricaTrava, Trava, TravaArquivo
from freedfe.nfse.estado_emissao import (
    EstadoEmissao,
    EstadoEmissaoArquivo,
    RegistroEmissao,
    SituacaoEmissao,
)
from freedfe.nfse.evidencias import ArmazenamentoEvidencias, ArmazenamentoEvidenciasArquivo
from freedfe.nfse.parametros import CacheParametros, CacheParametrosArquivo

if TYPE_CHECKING:
    from freedfe.nfse.fabrica import criar_servico_nfse

__all__ = [
    "ArmazenamentoEvidencias",
    "ArmazenamentoEvidenciasArquivo",
    "Auditoria",
    "CacheParametros",
    "CacheParametrosArquivo",
    "EstadoEmissao",
    "EstadoEmissaoArquivo",
    "EstadoNSU",
    "EstadoSequenciaNsu",
    "FabricaEstadoNsu",
    "FabricaTrava",
    "RegistroAuditoria",
    "RegistroEmissao",
    "SituacaoEmissao",
    "Trava",
    "TravaArquivo",
    "criar_servico_nfse",
]


def __getattr__(nome: str) -> Any:
    """Reexporta ``criar_servico_nfse`` na primeira consulta (ver a docstring do módulo)."""
    if nome == "criar_servico_nfse":
        from freedfe.nfse.fabrica import criar_servico_nfse
        return criar_servico_nfse
    raise AttributeError(f"module {__name__!r} has no attribute {nome!r}")
