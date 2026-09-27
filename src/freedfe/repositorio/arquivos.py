"""Repositório em arquivos: XML bruto em pastas + índice JSON por modelo."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path

from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.documentos.leitor import LeitorDocumentoFiscal
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento
from freedfe.repositorio.base import RepositorioDocumentos
from freedfe.tipos import ModeloDocumento

NOME_INDICE = "indice.json"


class IndiceModelo:
    """Índice JSON de um modelo: documentos por chave, eventos por chave e NSU recebidos."""

    def __init__(self, arquivo: Path):
        self.arquivo = arquivo
        self.documentos: dict[str, dict] = {}
        self.eventos: dict[str, list[dict]] = {}
        self.nsus: set[str] = set()
        self.alterado = False
        if arquivo.exists():
            self._carregar()

    def _carregar(self) -> None:
        dados = json.loads(self.arquivo.read_text("utf-8"))
        self.documentos = dados.get("documentos", {})
        self.eventos = dados.get("eventos", {})
        self.nsus = set(dados.get("nsus", []))

    def salvar(self) -> None:
        """Escrita atômica (arquivo temporário + rename), somente se houve alteração."""
        if not self.alterado:
            return
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        conteudo = {"documentos": self.documentos, "eventos": self.eventos,
                    "nsus": sorted(self.nsus)}
        temporario = self.arquivo.with_suffix(".tmp")
        temporario.write_text(json.dumps(conteudo, ensure_ascii=False, indent=1), "utf-8")
        temporario.replace(self.arquivo)
        self.alterado = False


class RepositorioArquivos(RepositorioDocumentos):
    """Armazena os XMLs em ``<pasta>/<modelo>/<AAAA-MM>/<chave>-<tipo>.xml``.

    Estrutura:
        - ``<modelo>/indice.json``: resumos, eventos e NSU recebidos.
        - ``<modelo>/<AAAA-MM>/``: XMLs de documentos pelo mês de emissão.
        - ``<modelo>/eventos/<AAAA-MM>/``: XMLs de eventos.
        - ``nao_reconhecidos/``: XMLs de tipo desconhecido (preservados para análise).

    O XML é gravado exatamente como recebido (auditabilidade). Um resumo
    (``resNFe``) nunca substitui um XML completo já armazenado.
    """

    def __init__(self, pasta: str | Path, interessado: str | None = None,
                 leitor: LeitorDocumentoFiscal | None = None):
        super().__init__(interessado, leitor)
        self.pasta = Path(pasta)
        self._indices: dict[ModeloDocumento, IndiceModelo] = {}

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def obter(self, modelo: ModeloDocumento, chave: str) -> ResumoDocumento | None:
        dados = self._indice(modelo).documentos.get(chave)
        return ResumoDocumento.de_dict(dados) if dados else None

    def eventos(self, modelo: ModeloDocumento, chave: str) -> list[EventoDocumento]:
        return [EventoDocumento.de_dict(d) for d in self._indice(modelo).eventos.get(chave, [])]

    def ler_xml(self, resumo: ResumoDocumento) -> bytes:
        return (self.pasta / resumo.caminho_xml).read_bytes()

    def caminho_absoluto(self, resumo: ResumoDocumento) -> Path:
        """Caminho do XML no disco."""
        return self.pasta / resumo.caminho_xml

    def _resumos(self, modelo: ModeloDocumento) -> Iterable[ResumoDocumento]:
        return [ResumoDocumento.de_dict(d) for d in self._indice(modelo).documentos.values()]

    def _nsus(self, modelo: ModeloDocumento) -> Iterable[str]:
        return self._indice(modelo).nsus

    # ------------------------------------------------------------------
    # Gravação
    # ------------------------------------------------------------------

    def _gravar_documento(self, resumo: ResumoDocumento, xml: bytes) -> None:
        indice = self._indice(resumo.modelo)
        existente = indice.documentos.get(resumo.chave)
        if existente and existente["xml_completo"] and not resumo.xml_completo:
            return  # não rebaixa XML completo para resumo
        relativo = (Path(resumo.modelo.value) / _pasta_mes(resumo.data_emissao)
                    / f"{resumo.chave}-{resumo.tipo_xml}.xml")
        self._gravar_arquivo(relativo, xml)
        resumo.caminho_xml = relativo.as_posix()
        indice.documentos[resumo.chave] = resumo.para_dict()
        indice.alterado = True

    def _gravar_evento(self, evento: EventoDocumento, xml: bytes) -> None:
        indice = self._indice(evento.modelo)
        conhecidos = indice.eventos.setdefault(evento.chave, [])
        identificador = evento.protocolo or evento.nsu or _hash_curto(xml)
        relativo = (Path(evento.modelo.value) / "eventos" / _pasta_mes(evento.data_evento)
                    / f"{evento.chave}-{evento.tipo_evento}-{identificador}.xml")
        if any(e.get("caminho_xml") == relativo.as_posix() for e in conhecidos):
            return  # evento já armazenado
        self._gravar_arquivo(relativo, xml)
        evento.caminho_xml = relativo.as_posix()
        conhecidos.append(evento.para_dict())
        indice.alterado = True

    def _gravar_nao_reconhecido(self, recebido: DocumentoRecebido) -> None:
        schema = re.sub(r"[^0-9A-Za-z_.-]", "_", recebido.schema or "sem-schema")
        nome = f"{recebido.nsu or _hash_curto(recebido.xml)}_{schema}.xml"
        self._gravar_arquivo(Path("nao_reconhecidos") / nome, recebido.xml)

    def _registrar_nsu(self, modelo: ModeloDocumento, nsu: str) -> None:
        indice = self._indice(modelo)
        if nsu not in indice.nsus:
            indice.nsus.add(nsu)
            indice.alterado = True

    def _confirmar(self) -> None:
        for indice in self._indices.values():
            indice.salvar()

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------

    def _indice(self, modelo: ModeloDocumento) -> IndiceModelo:
        modelo = ModeloDocumento.normalizar(modelo)
        if modelo not in self._indices:
            self._indices[modelo] = IndiceModelo(self.pasta / modelo.value / NOME_INDICE)
        return self._indices[modelo]

    def _gravar_arquivo(self, relativo: Path, xml: bytes) -> None:
        destino = self.pasta / relativo
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(xml)


def _pasta_mes(data) -> str:
    """Subpasta ``AAAA-MM`` pela data do documento (``sem-data`` se ausente)."""
    return f"{data:%Y-%m}" if data else "sem-data"


def _hash_curto(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()[:16]
