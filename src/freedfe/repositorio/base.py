"""Contrato do repositório de documentos fiscais."""

from __future__ import annotations

import datetime as dt
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, replace

from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.documentos.leitor import LeitorDocumentoFiscal
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento, evento_cancela
from freedfe.repositorio.filtro import FiltroConsulta
from freedfe.tipos import ModeloDocumento, SituacaoDocumento
from freedfe.validadores import formatar_nsu

logger = logging.getLogger(__name__)


@dataclass
class ResultadoArmazenamento:
    """Quantidades gravadas em uma operação de armazenamento."""

    documentos: int = 0
    eventos: int = 0
    nao_reconhecidos: int = 0

    @property
    def total(self) -> int:
        """Total de XMLs gravados."""
        return self.documentos + self.eventos + self.nao_reconhecidos


class RepositorioDocumentos(ABC):
    """Armazena XMLs brutos e mantém um índice consultável.

    ``armazenar`` é um *template method*: interpreta cada XML com o
    ``LeitorDocumentoFiscal`` e delega a gravação às subclasses. Assim, trocar
    arquivos por SQLite/PostgreSQL exige apenas uma nova subclasse.

    Args:
        interessado: CNPJ/CPF dono do repositório (completa o destinatário de ``resNFe``).
        leitor: leitor de XML; o padrão reconhece NF-e, CT-e, MDF-e, resumos e eventos.
    """

    def __init__(self, interessado: str | None = None, leitor: LeitorDocumentoFiscal | None = None):
        self.interessado = interessado
        self._leitor = leitor or LeitorDocumentoFiscal()

    def armazenar(self, documentos: Iterable[DocumentoRecebido],
                  sequencia: ModeloDocumento | None = None) -> ResultadoArmazenamento:
        """Grava cada XML sem alteração e atualiza o índice.

        Args:
            documentos: XMLs recebidos da SEFAZ ou importados.
            sequencia: serviço de distribuição de origem; os NSU são registrados
                nessa sequência (necessário para detectar lacunas).
        """
        resultado = ResultadoArmazenamento()
        for recebido in documentos:
            item = self._leitor.ler(recebido.xml, self.interessado)
            if isinstance(item, ResumoDocumento):
                self._gravar_documento(replace(item, nsu=recebido.nsu), recebido.xml)
                resultado.documentos += 1
            elif isinstance(item, EventoDocumento):
                self._gravar_evento(replace(item, nsu=recebido.nsu), recebido.xml)
                resultado.eventos += 1
            else:
                self._gravar_nao_reconhecido(recebido)
                resultado.nao_reconhecidos += 1
            if recebido.nsu and sequencia is not None:
                self._registrar_nsu(sequencia, recebido.nsu)
        self._confirmar()
        return resultado

    def listar(self, filtro: FiltroConsulta | None = None) -> list[ResumoDocumento]:
        """Documentos que atendem ao filtro, com a situação atualizada pelos eventos."""
        filtro = filtro or FiltroConsulta()
        modelos = filtro.modelos or list(ModeloDocumento)
        encontrados = []
        for modelo in modelos:
            for resumo in self._resumos(modelo):
                resumo = self._aplicar_eventos(resumo)
                if filtro.aceita(resumo):
                    encontrados.append(resumo)
        return sorted(encontrados, key=_ordem_listagem)

    def lacunas_nsu(self, modelo: ModeloDocumento) -> list[str]:
        """NSU faltantes entre os já recebidos (candidatos a consulta pontual)."""
        nsus = sorted(int(n) for n in self._nsus(modelo))
        faltantes = []
        for anterior, atual in zip(nsus, nsus[1:]):
            faltantes.extend(formatar_nsu(n) for n in range(anterior + 1, atual))
        return faltantes

    def _aplicar_eventos(self, resumo: ResumoDocumento) -> ResumoDocumento:
        if any(evento_cancela(ev) for ev in self.eventos(resumo.modelo, resumo.chave)):
            return replace(resumo, situacao=SituacaoDocumento.CANCELADO)
        return resumo

    # ------------------------------------------------------------------
    # Operações a implementar pelas subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def obter(self, modelo: ModeloDocumento, chave: str) -> ResumoDocumento | None:
        """Resumo de uma chave específica, se existir."""

    @abstractmethod
    def eventos(self, modelo: ModeloDocumento, chave: str) -> list[EventoDocumento]:
        """Eventos conhecidos de uma chave."""

    @abstractmethod
    def ler_xml(self, resumo: ResumoDocumento) -> bytes:
        """Conteúdo bruto do XML armazenado."""

    @abstractmethod
    def _resumos(self, modelo: ModeloDocumento) -> Iterable[ResumoDocumento]:
        """Todos os resumos armazenados de um modelo."""

    @abstractmethod
    def _nsus(self, modelo: ModeloDocumento) -> Iterable[str]:
        """NSU já recebidos de um modelo."""

    @abstractmethod
    def _gravar_documento(self, resumo: ResumoDocumento, xml: bytes) -> None:
        """Grava o XML do documento e atualiza o índice."""

    @abstractmethod
    def _gravar_evento(self, evento: EventoDocumento, xml: bytes) -> None:
        """Grava o XML do evento e atualiza o índice."""

    @abstractmethod
    def _gravar_nao_reconhecido(self, recebido: DocumentoRecebido) -> None:
        """Preserva XML de tipo desconhecido (nada recebido pode ser perdido)."""

    @abstractmethod
    def _registrar_nsu(self, modelo: ModeloDocumento, nsu: str) -> None:
        """Registra o NSU recebido (controle de lacunas)."""

    @abstractmethod
    def _confirmar(self) -> None:
        """Persiste as alterações pendentes do índice."""


def _ordem_listagem(resumo: ResumoDocumento) -> tuple:
    data = resumo.data_emissao or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    if data.tzinfo is None:
        data = data.replace(tzinfo=dt.timezone.utc)
    return data, resumo.chave
