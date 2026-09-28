"""Envio da Manifestação do Destinatário ao Ambiente Nacional (NFeRecepcaoEvento4).

Atenção jurídica/operacional: o evento é assinado com o certificado do
destinatário, ou seja, é ato do contribuinte. Automatizar a Ciência exige
autorização formal do cliente; as manifestações conclusivas (Confirmação,
Desconhecimento, Operação não Realizada) devem vir do fluxo de recebimento.
Prazos (Ajuste SINIEF 14/26): Ciência em 10 dias; conclusiva em 90 dias.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

from lxml import etree

from freedfe.auditoria import RegistroAuditoria
from freedfe.excecoes import RespostaInvalidaErro
from freedfe.interessado import Interessado
from freedfe.manifestacao.assinatura import AssinadorXml
from freedfe.manifestacao.evento import (
    MAXIMO_EVENTOS_LOTE,
    NS_NFE,
    DadosManifestacao,
    MontadorEvento,
    TipoManifestacao,
)
from freedfe.servicos import ENDPOINTS_EVENTO_AN, NS_WSDL_EVENTO
from freedfe.tipos import Ambiente
from freedfe.transporte import SOAP12_NS, Transporte

CSTAT_EVENTO_VINCULADO = {"135", "136"}  # 135 = registrado e vinculado; 136 = registrado


@dataclass(frozen=True)
class RetornoEvento:
    """Resultado de um evento dentro do lote."""

    chave: str | None
    tipo_evento: str | None
    cstat: str | None
    xmotivo: str | None
    protocolo: str | None
    data_registro: str | None

    @property
    def registrado(self) -> bool:
        """True se a SEFAZ registrou o evento."""
        return self.cstat in CSTAT_EVENTO_VINCULADO


@dataclass
class RetornoManifestacao:
    """Resultado de um lote de manifestação."""

    cstat_lote: str | None
    xmotivo_lote: str | None
    eventos: list[RetornoEvento] = field(default_factory=list)


class ClienteManifestacao:
    """Envia eventos de manifestação em lotes de até 20."""

    def __init__(self, ambiente: Ambiente, autor: Interessado, transporte: Transporte,
                 assinador: AssinadorXml, auditoria: RegistroAuditoria):
        self._ambiente = Ambiente(ambiente)
        self._autor = autor
        self._transporte = transporte
        self._assinador = assinador
        self._auditoria = auditoria
        self._montador = MontadorEvento(ambiente, autor)

    def ciencia(self, chaves: Iterable[str]) -> list[RetornoManifestacao]:
        """Ciência da Operação (210210): libera o XML completo na próxima sincronização."""
        return self.manifestar(chaves, TipoManifestacao.CIENCIA)

    def confirmacao(self, chaves: Iterable[str]) -> list[RetornoManifestacao]:
        """Confirmação da Operação (210200). Impede o emitente de cancelar a NF-e."""
        return self.manifestar(chaves, TipoManifestacao.CONFIRMACAO)

    def desconhecimento(self, chaves: Iterable[str]) -> list[RetornoManifestacao]:
        """Desconhecimento da Operação (210220). Não libera o XML."""
        return self.manifestar(chaves, TipoManifestacao.DESCONHECIMENTO)

    def nao_realizada(self, chaves: Iterable[str], justificativa: str) -> list[RetornoManifestacao]:
        """Operação não Realizada (210240). Justificativa de 15 a 255 caracteres."""
        return self.manifestar(chaves, TipoManifestacao.NAO_REALIZADA, justificativa)

    def manifestar(self, chaves: Iterable[str], tipo: TipoManifestacao,
                   justificativa: str | None = None) -> list[RetornoManifestacao]:
        """Valida todas as chaves e envia em lotes de até 20 eventos."""
        dados = [DadosManifestacao(chave, tipo, justificativa) for chave in chaves]
        for item in dados:
            item.validar()  # falha antes de enviar qualquer lote
        return [self.enviar_lote(dados[i:i + MAXIMO_EVENTOS_LOTE])
                for i in range(0, len(dados), MAXIMO_EVENTOS_LOTE)]

    def gerar_lote_assinado(self, dados: list[DadosManifestacao]) -> bytes:
        """``envEvento`` assinado, sem enviar (útil para conferência)."""
        eventos = [self._assinador.assinar(self._montador.evento(item)) for item in dados]
        return self._montador.lote(eventos, _id_lote())

    def enviar_lote(self, dados: list[DadosManifestacao]) -> RetornoManifestacao:
        """Assina, envia e interpreta um lote."""
        lote = self.gerar_lote_assinado(dados)
        resposta = self._transporte.enviar(ENDPOINTS_EVENTO_AN[self._ambiente], _envelope(lote),
                                           f"{NS_WSDL_EVENTO}/nfeRecepcaoEvento")
        retorno = _interpretar(resposta)
        for evento in retorno.eventos:
            self._auditoria.registrar(
                modo="manifestacao", ambiente=int(self._ambiente),
                interessado=self._autor.documento,
                chave=evento.chave, tipo_evento=evento.tipo_evento, cstat=evento.cstat,
                xmotivo=evento.xmotivo, protocolo=evento.protocolo)
        return retorno


def _id_lote() -> int:
    return int(dt.datetime.now().strftime("%y%m%d%H%M%S%f")[:15])


def _envelope(env_evento: bytes) -> bytes:
    return (b'<?xml version="1.0" encoding="utf-8"?>'
            b'<soap12:Envelope xmlns:soap12="' + SOAP12_NS.encode() + b'">'
            b'<soap12:Body><nfeDadosMsg xmlns="' + NS_WSDL_EVENTO.encode() + b'">' + env_evento
            + b"</nfeDadosMsg></soap12:Body></soap12:Envelope>")


def _interpretar(resposta: bytes) -> RetornoManifestacao:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        raiz = etree.fromstring(resposta, parser)
    except etree.XMLSyntaxError as erro:
        raise RespostaInvalidaErro(f"Resposta não é XML válido: {resposta[:300]!r}") from erro
    ret = raiz.find(f".//{{{NS_NFE}}}retEnvEvento")
    if ret is None:
        raise RespostaInvalidaErro(f"Resposta sem retEnvEvento: {resposta[:500]!r}")

    def campo(no, nome):
        return no.findtext(f"{{{NS_NFE}}}{nome}") if no is not None else None

    eventos = [RetornoEvento(chave=campo(inf, "chNFe"), tipo_evento=campo(inf, "tpEvento"),
                             cstat=campo(inf, "cStat"), xmotivo=campo(inf, "xMotivo"),
                             protocolo=campo(inf, "nProt"), data_registro=campo(inf, "dhRegEvento"))
               for inf in ret.iterfind(f"{{{NS_NFE}}}retEvento/{{{NS_NFE}}}infEvento")]
    return RetornoManifestacao(campo(ret, "cStat"), campo(ret, "xMotivo"), eventos)
