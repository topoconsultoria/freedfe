"""Eventos da NFS-e Nacional: tipos conhecidos e montagem do pedido de cancelamento (101101).

Leiaute do pedido conferido contra o XSD oficial ``pedRegEvento_v1.01``::

    pedRegEvento@versao
      infPedReg@Id   PRE + chave(50) + tipo(6) = 59 posições
        tpAmb, verAplic, dhEvento, CNPJAutor|CPFAutor, chNFSe
        e101101: xDesc("Cancelamento de NFS-e"), cMotivo(1|2|9), xMotivo(15 a 255)
      Signature      (irmã de infPedReg, perfil NFS-e)

O cancelamento depende de regras municipais (prazo, análise fiscal, nota sem tomador
identificado: E0824) que só a Sefin aplica. A biblioteca expõe o código de rejeição.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum, IntEnum

from lxml import etree

from freedfe._versao import __version__
from freedfe.distribuicao.estado import Relogio, relogio_utc
from freedfe.documentos.resumo import EVENTOS_CANCELAMENTO, EVENTOS_SUBSTITUICAO
from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.nfse.identificadores import id_pedido_evento
from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.nfse.montador_dps import FOLGA_EMISSAO_SEGUNDOS
from freedfe.nfse.texto import sanitizar
from freedfe.tipos import FUSO_BRASILIA, Ambiente, ModeloDocumento
from freedfe.validadores import exigir_chave_nfse

DESCRICAO_CANCELAMENTO = "Cancelamento de NFS-e"
MINIMO_JUSTIFICATIVA = 15
MAXIMO_JUSTIFICATIVA = 255


class TipoEventoNfse(str, Enum):
    """Eventos da NFS-e (Anexo II). Só ``CANCELAMENTO`` é emitido pela biblioteca."""

    CANCELAMENTO = "101101"
    SOLICITACAO_ANALISE_FISCAL = "101103"
    CANCELAMENTO_POR_SUBSTITUICAO = "105102"
    CANCELAMENTO_DEFERIDO = "105104"
    CANCELAMENTO_INDEFERIDO = "105105"
    CANCELAMENTO_POR_OFICIO = "305101"
    CONFIRMACAO_PRESTADOR = "202201"
    CONFIRMACAO_TOMADOR = "203202"
    CONFIRMACAO_INTERMEDIARIO = "204203"
    REJEICAO_PRESTADOR = "202205"
    REJEICAO_TOMADOR = "203206"
    REJEICAO_INTERMEDIARIO = "204207"

    @property
    def cancela(self) -> bool:
        """True se o evento deixa a NFS-e cancelada (101101, 105104 e 305101)."""
        return self.value in EVENTOS_CANCELAMENTO[ModeloDocumento.NFSE]

    @property
    def substitui(self) -> bool:
        """True se o evento indica NFS-e substituída (105102, gerado pela Sefin)."""
        return self.value in EVENTOS_SUBSTITUICAO[ModeloDocumento.NFSE]


class MotivoCancelamento(IntEnum):
    """``cMotivo`` do evento 101101."""

    ERRO_NA_EMISSAO = 1
    SERVICO_NAO_PRESTADO = 2
    OUTROS = 9


def _q(nome: str) -> str:
    return f"{{{NAMESPACE_NFSE}}}{nome}"


def _filho(pai: etree._Element, nome: str, valor: object) -> etree._Element:
    elemento = etree.SubElement(pai, _q(nome))
    elemento.text = str(valor)
    return elemento


class MontadorPedidoEvento:
    """Monta ``<pedRegEvento>`` (sem assinatura).

    Args:
        ambiente: ambiente da fachada (``tpAmb`` nunca é informado pelo chamador).
        autor: titular do certificado (CNPJ ou CPF do autor do evento).
        leiaute: define o ``versao`` do pedido e o tamanho do Id (59 posições).
        relogio: relógio UTC injetável.
        folga_segundos: subtraída do relógio em ``dhEvento`` (não pode ser futura).
    """

    def __init__(self, ambiente: Ambiente, autor: Interessado,
                 leiaute: VersaoLeiaute = VersaoLeiaute.NT009_V101,
                 relogio: Relogio = relogio_utc, folga_segundos: int = FOLGA_EMISSAO_SEGUNDOS):
        self._ambiente = Ambiente(ambiente)
        self._autor = autor
        self._leiaute = leiaute
        self._relogio = relogio
        self._folga = dt.timedelta(seconds=folga_segundos)

    def cancelamento(self, chave: str, codigo_motivo: MotivoCancelamento | int,
                     justificativa: str) -> etree._Element:
        """Pedido de cancelamento (101101) com justificativa de 15 a 255 caracteres."""
        chave = exigir_chave_nfse(chave)
        try:
            motivo = MotivoCancelamento(int(codigo_motivo))
        except ValueError:
            raise ValidacaoErro(
                f"Motivo de cancelamento inválido: {codigo_motivo!r} (1, 2 ou 9).") from None
        texto = sanitizar(justificativa)
        if not MINIMO_JUSTIFICATIVA <= len(texto) <= MAXIMO_JUSTIFICATIVA:
            raise ValidacaoErro(
                f"A justificativa do cancelamento deve ter de {MINIMO_JUSTIFICATIVA} a "
                f"{MAXIMO_JUSTIFICATIVA} caracteres (tem {len(texto)}).")
        tipo = TipoEventoNfse.CANCELAMENTO.value
        momento = (self._relogio().astimezone(FUSO_BRASILIA) - self._folga).replace(microsecond=0)

        raiz = etree.Element(_q("pedRegEvento"), nsmap={None: NAMESPACE_NFSE},
                             versao=self._leiaute.versao_evento)
        inf = etree.SubElement(raiz, _q("infPedReg"),
                               Id=id_pedido_evento(chave, tipo, self._leiaute))
        _filho(inf, "tpAmb", int(self._ambiente))
        _filho(inf, "verAplic", f"freedfe/{__version__}"[:20])
        _filho(inf, "dhEvento", momento.isoformat())
        _filho(inf, f"{self._autor.tag}Autor", self._autor.documento)
        _filho(inf, "chNFSe", chave)
        evento = etree.SubElement(inf, _q(f"e{tipo}"))
        _filho(evento, "xDesc", DESCRICAO_CANCELAMENTO)
        _filho(evento, "cMotivo", int(motivo))
        _filho(evento, "xMotivo", texto)
        return raiz
