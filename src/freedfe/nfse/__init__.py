"""NFS-e Nacional (Sefin Nacional / ADN): emissão, consulta, eventos e DANFSe.

O ponto de entrada para o usuário é ``FreeDFe.nfse`` (ver :class:`ServicoNfse`).
Os módulos deste pacote são desacoplados e testáveis sem rede.

Os contratos de persistência (``EstadoEmissao``, ``Trava``, ``EstadoSequenciaNsu``) são
reexportados aqui para quem implementa um backend próprio (por exemplo, PostgreSQL).
"""

from freedfe.distribuicao.estado import EstadoNSU, EstadoSequenciaNsu, FabricaEstadoNsu
from freedfe.distribuicao.trava import FabricaTrava, Trava, TravaArquivo
from freedfe.nfse.estado_emissao import (
    EstadoEmissao,
    EstadoEmissaoArquivo,
    RegistroEmissao,
    SituacaoEmissao,
)

__all__ = [
    "EstadoEmissao",
    "EstadoEmissaoArquivo",
    "EstadoNSU",
    "EstadoSequenciaNsu",
    "FabricaEstadoNsu",
    "FabricaTrava",
    "RegistroEmissao",
    "SituacaoEmissao",
    "Trava",
    "TravaArquivo",
]
