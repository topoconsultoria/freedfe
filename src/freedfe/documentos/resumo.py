"""Estruturas de dados dos documentos e eventos extraídos dos XMLs."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal

from freedfe.tipos import ModeloDocumento, SituacaoDocumento


@dataclass
class ResumoDocumento:
    """Dados principais de um DF-e (NF-e, CT-e ou MDF-e) para listagem e filtro.

    Attributes:
        chave: chave de acesso (44 posições).
        modelo: modelo do documento.
        tipo_xml: elemento raiz do XML armazenado (``nfeProc``, ``resNFe``, ``cteProc``...).
        xml_completo: False quando só existe o resumo (``resNFe``), antes da manifestação.
        demais_participantes: CNPJ/CPF de tomador, remetente, expedidor, recebedor,
            contratantes, transportador e ``autXML``, usados no filtro de documentos recebidos.
        caminho_xml: caminho do XML dentro do repositório.
    """

    chave: str
    modelo: ModeloDocumento
    tipo_xml: str
    xml_completo: bool
    numero: str | None = None
    serie: str | None = None
    data_emissao: dt.datetime | None = None
    emitente_documento: str | None = None
    emitente_nome: str | None = None
    destinatario_documento: str | None = None
    destinatario_nome: str | None = None
    valor_total: Decimal | None = None
    protocolo: str | None = None
    situacao: SituacaoDocumento = SituacaoDocumento.AUTORIZADO
    nsu: str | None = None
    demais_participantes: list[str] = field(default_factory=list)
    caminho_xml: str | None = None

    def participantes(self) -> set[str]:
        """Todos os CNPJ/CPF envolvidos, exceto o emitente."""
        documentos = set(self.demais_participantes)
        if self.destinatario_documento:
            documentos.add(self.destinatario_documento)
        return documentos

    def para_dict(self) -> dict:
        """Representação serializável em JSON (datas ISO 8601, valor como texto)."""
        dados = asdict(self)
        dados["modelo"] = self.modelo.value
        dados["situacao"] = self.situacao.value
        dados["data_emissao"] = self.data_emissao.isoformat() if self.data_emissao else None
        dados["valor_total"] = str(self.valor_total) if self.valor_total is not None else None
        return dados

    @classmethod
    def de_dict(cls, dados: dict) -> ResumoDocumento:
        """Reconstrói a partir de ``para_dict`` (ignora chaves desconhecidas)."""
        nomes = {f.name for f in fields(cls)}
        valores = {k: v for k, v in dados.items() if k in nomes}
        valores["modelo"] = ModeloDocumento(valores["modelo"])
        valores["situacao"] = SituacaoDocumento(valores.get("situacao", "autorizado"))
        if valores.get("data_emissao"):
            valores["data_emissao"] = dt.datetime.fromisoformat(valores["data_emissao"])
        if valores.get("valor_total") is not None:
            valores["valor_total"] = Decimal(valores["valor_total"])
        return cls(**valores)


@dataclass
class EventoDocumento:
    """Evento vinculado a um documento (cancelamento, CC-e, manifestação, encerramento...)."""

    chave: str
    modelo: ModeloDocumento
    tipo_evento: str
    descricao: str | None = None
    data_evento: dt.datetime | None = None
    protocolo: str | None = None
    nsu: str | None = None
    caminho_xml: str | None = None

    def para_dict(self) -> dict:
        """Representação serializável em JSON."""
        dados = asdict(self)
        dados["modelo"] = self.modelo.value
        dados["data_evento"] = self.data_evento.isoformat() if self.data_evento else None
        return dados

    @classmethod
    def de_dict(cls, dados: dict) -> EventoDocumento:
        """Reconstrói a partir de ``para_dict``."""
        nomes = {f.name for f in fields(cls)}
        valores = {k: v for k, v in dados.items() if k in nomes}
        valores["modelo"] = ModeloDocumento(valores["modelo"])
        if valores.get("data_evento"):
            valores["data_evento"] = dt.datetime.fromisoformat(valores["data_evento"])
        return cls(**valores)


# Eventos que cancelam o documento, por modelo. Atenção: no MDF-e, 110112 é Encerramento;
# na NF-e, 110112 é Cancelamento por Substituição.
EVENTOS_CANCELAMENTO = {
    ModeloDocumento.NFE: frozenset({"110111", "110112"}),
    ModeloDocumento.CTE: frozenset({"110111"}),
    ModeloDocumento.MDFE: frozenset({"110111"}),
}


def evento_cancela(evento: EventoDocumento) -> bool:
    """True se o evento cancela o documento a que se refere."""
    return evento.tipo_evento in EVENTOS_CANCELAMENTO[evento.modelo]
