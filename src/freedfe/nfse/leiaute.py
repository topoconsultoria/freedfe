"""Versões de leiaute da DPS/NFS-e e a regra de seleção por ambiente e data.

Cronograma (relatório técnico de out/2026, notícia oficial do CGNFS-e de 20/10/2026):
    - NT 004 v2.0 (XSD 1.01): leiaute em produção até 30/11/2026.
    - NT 009 v1.01 (reforma tributária): produção restrita desde 20/10/2026;
      produção a partir de 01/12/2026 (fontes secundárias citam 03/11/2026, não adotado).

O leiaute que vale é o da data em que a Sefin **recebe** a DPS, não o da competência.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from freedfe.tipos import FUSO_BRASILIA, Ambiente

NAMESPACE_NFSE = "http://www.sped.fazenda.gov.br/nfse"

INICIO_NT009_PRODUCAO = dt.date(2026, 12, 1)
INICIO_NT009_PRODUCAO_RESTRITA = dt.date(2026, 10, 20)


class VersaoLeiaute(Enum):
    """Leiaute da DPS e do pedido de registro de evento.

    HIPÓTESE: o atributo ``versao`` da DPS é ``"1.01"`` nos dois leiautes. O pacote
    ``PRODREST...20261020`` pode trazer outro valor; confirmar no XSD e ajustar aqui.
    """

    NT004_V2 = "nt004_v2"
    NT009_V101 = "nt009_v101"

    @property
    def namespace(self) -> str:
        """Namespace do XML da DPS, NFS-e e eventos."""
        return NAMESPACE_NFSE

    @property
    def versao_dps(self) -> str:
        """Valor do atributo ``versao`` do elemento ``DPS``."""
        return "1.01"

    @property
    def versao_evento(self) -> str:
        """Valor do atributo ``versao`` do elemento ``pedRegEvento``."""
        return "1.01"

    @property
    def tamanho_id_evento(self) -> int:
        """Posições do Id do pedido de evento: ``PRE`` + chave(50) + tipo(6) = 59.

        O leiaute antigo acrescentava ``nPedRegEvento`` (62 posições) e é recusado com o
        XSD 1.01, que é o vigente também na NT 004 v2.0.
        """
        return 59

    @property
    def descricao(self) -> str:
        """Nome legível para logs e mensagens."""
        return "NT 004 v2.0" if self is VersaoLeiaute.NT004_V2 else "NT 009 v1.01"

    @classmethod
    def selecionar(cls, ambiente: Ambiente | int,
                   data_referencia: dt.date | None = None) -> VersaoLeiaute:
        """Leiaute em vigor no ``ambiente`` na ``data_referencia`` (padrão: hoje em Brasília).

        Produção restrita roda sempre a NT 009. Produção usa a NT 004 até 30/11/2026.
        """
        if Ambiente(ambiente) is Ambiente.HOMOLOGACAO:
            return cls.NT009_V101
        data = data_referencia or dt.datetime.now(FUSO_BRASILIA).date()
        return cls.NT004_V2 if data < INICIO_NT009_PRODUCAO else cls.NT009_V101
