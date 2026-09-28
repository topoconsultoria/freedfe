"""Montagem dos eventos de Manifestação do Destinatário (NT 2020.001 v1.60)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from lxml import etree

from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.tipos import Ambiente
from freedfe.validadores import exigir_chave

NS_NFE = "http://www.portalfiscal.inf.br/nfe"
C_ORGAO_AMBIENTE_NACIONAL = "91"
FUSO_BRASILIA = dt.timezone(dt.timedelta(hours=-3))
MAXIMO_EVENTOS_LOTE = 20


class TipoManifestacao(Enum):
    """Eventos de manifestação: (tpEvento, descEvento literal do schema)."""

    CONFIRMACAO = ("210200", "Confirmacao da Operacao")
    CIENCIA = ("210210", "Ciencia da Operacao")
    DESCONHECIMENTO = ("210220", "Desconhecimento da Operacao")
    NAO_REALIZADA = ("210240", "Operacao nao Realizada")

    @property
    def codigo(self) -> str:
        """Código ``tpEvento``."""
        return self.value[0]

    @property
    def descricao(self) -> str:
        """Texto exato de ``descEvento``."""
        return self.value[1]


@dataclass(frozen=True)
class DadosManifestacao:
    """Parâmetros de um evento de manifestação.

    ``justificativa`` é obrigatória (15 a 255 caracteres) apenas em Operação não Realizada.
    ``sequencia`` (``nSeqEvento``) pode ser 1 ou 2 (Ajuste SINIEF 43/23).
    """

    chave: str
    tipo: TipoManifestacao
    justificativa: str | None = None
    sequencia: int = 1
    data_evento: dt.datetime | None = None

    def validar(self) -> None:
        """Levanta ``ValidacaoErro`` nas regras que a SEFAZ rejeitaria."""
        exigir_chave(self.chave)
        if self.sequencia not in (1, 2):
            raise ValidacaoErro("nSeqEvento deve ser 1 ou 2 (Ajuste SINIEF 43/23).")
        if self.tipo is TipoManifestacao.NAO_REALIZADA:
            texto = (self.justificativa or "").strip()
            if not 15 <= len(texto) <= 255:
                raise ValidacaoErro(
                    "Operação não Realizada exige justificativa de 15 a 255 caracteres (rej. 595).")
        elif self.justificativa:
            raise ValidacaoErro("Justificativa só é aceita no evento 210240.")


class MontadorEvento:
    """Monta o elemento ``<evento>`` (sem assinatura) e o lote ``envEvento``."""

    def __init__(self, ambiente: Ambiente, autor: Interessado):
        self._ambiente = Ambiente(ambiente)
        self._autor = autor

    def evento(self, dados: DadosManifestacao) -> etree._Element:
        """Elemento ``<evento>`` conforme ``leiauteConfRecebto_v1.00``."""
        dados.validar()
        chave = dados.chave.strip().upper()
        # Horário de Brasília com offset explícito; a SEFAZ tolera até 5 min (rej. 578).
        data = (dados.data_evento or dt.datetime.now(FUSO_BRASILIA)).replace(microsecond=0)
        identificador = f"ID{dados.tipo.codigo}{chave}{dados.sequencia:02d}"  # 54 posições

        evento = etree.Element(_tag("evento"), nsmap={None: NS_NFE}, versao="1.00")
        inf = etree.SubElement(evento, _tag("infEvento"), Id=identificador)
        _filho(inf, "cOrgao", C_ORGAO_AMBIENTE_NACIONAL)
        _filho(inf, "tpAmb", str(int(self._ambiente)))
        _filho(inf, self._autor.tag, self._autor.documento)
        _filho(inf, "chNFe", chave)
        _filho(inf, "dhEvento", data.isoformat())
        _filho(inf, "tpEvento", dados.tipo.codigo)
        _filho(inf, "nSeqEvento", str(dados.sequencia))
        _filho(inf, "verEvento", "1.00")
        detalhe = etree.SubElement(inf, _tag("detEvento"), versao="1.00")
        _filho(detalhe, "descEvento", dados.tipo.descricao)
        if dados.justificativa:
            _filho(detalhe, "xJust", dados.justificativa.strip())
        return evento

    @staticmethod
    def lote(eventos_assinados: list[etree._Element], id_lote: int) -> bytes:
        """``envEvento`` com 1 a 20 eventos já assinados."""
        if not 1 <= len(eventos_assinados) <= MAXIMO_EVENTOS_LOTE:
            raise ValidacaoErro(f"Lote deve conter de 1 a {MAXIMO_EVENTOS_LOTE} eventos.")
        env = etree.Element(_tag("envEvento"), nsmap={None: NS_NFE}, versao="1.00")
        _filho(env, "idLote", str(id_lote)[:15])
        for evento in eventos_assinados:
            env.append(evento)
        return etree.tostring(env, encoding="utf-8")


def _tag(nome: str) -> str:
    return f"{{{NS_NFE}}}{nome}"


def _filho(pai: etree._Element, nome: str, valor: str) -> etree._Element:
    elemento = etree.SubElement(pai, _tag(nome))
    elemento.text = valor
    return elemento
