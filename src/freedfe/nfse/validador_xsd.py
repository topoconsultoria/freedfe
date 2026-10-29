"""Validação opcional da DPS (e de outros XML da NFS-e) contra os XSD oficiais.

Os XSD **não** são distribuídos com a biblioteca. Baixe o pacote de esquemas do leiaute
desejado no portal da NFS-e Nacional (documentação técnica) e aponte a pasta: a subpasta
``Schemas/1.01`` de ``nfse-esquemas_xsd-v1-01-20260209.zip`` para a NT 004 v2.0, ou o conteúdo
de ``esquemas_prod_restrita_20261020.zip`` para a NT 009 v1.01. O validador usa ``DPS_v*.xsd``
(ou o arquivo informado) e resolve os ``include``/``import`` na mesma pasta, sem acessar a rede.

Defeito conhecido do pacote ``20260209``: o padrão de ``TSSerieDPS`` vem como
``^0{0,4}\\d{1,5}$``. Em XSD, ``^`` e ``$`` são caracteres literais, então nenhuma série passa
no libxml2. O validador remove essas âncoras (o casamento de XSD já é do valor inteiro); a
Sefin aceita as séries normalmente.
"""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from freedfe.excecoes import ValidacaoErro

MAXIMO_MENSAGENS = 10
_PADRAO = re.compile(r'(<(?:\w+:)?pattern\s+value=")([^"]*)(")')


def _sem_ancoras(texto: str) -> str:
    """Remove ``^`` e ``$`` das pontas dos ``xs:pattern`` (literais em XSD; defeito do pacote)."""
    def limpar(casado: re.Match) -> str:
        valor = casado.group(2)
        if valor.startswith("^"):
            valor = valor[1:]
        if valor.endswith("$") and not valor.endswith("\\$"):
            valor = valor[:-1]
        return f"{casado.group(1)}{valor}{casado.group(3)}"
    return _PADRAO.sub(limpar, texto)


class _ResolvedorLocal(etree.Resolver):
    """Entrega os XSD da pasta (``include``/``import``) já sem as âncoras defeituosas."""

    def __init__(self, pasta: Path):
        super().__init__()
        self._arquivos = {p.name: p for p in pasta.glob("*.xsd")}

    def resolve(self, url, id_publico, contexto):
        arquivo = self._arquivos.get(Path(url.split("file://")[-1]).name)
        if arquivo is None:
            return None  # nada de rede: lxml falha com erro claro
        return self.resolve_string(_sem_ancoras(arquivo.read_text("utf-8-sig")), contexto)


class ValidadorXsd:
    """Valida elementos ou bytes contra um XSD local.

    Args:
        pasta_xsd: pasta com os XSD do pacote.
        arquivo: nome do XSD raiz. Padrão: o ``DPS_v*.xsd`` de maior versão na pasta.
    """

    def __init__(self, pasta_xsd: str | Path, arquivo: str | None = None):
        pasta = Path(pasta_xsd)
        if not pasta.is_dir():
            raise ValidacaoErro(f"Pasta de XSD inexistente: {pasta}")
        caminho = pasta / arquivo if arquivo else self._localizar_dps(pasta)
        if not caminho.is_file():
            raise ValidacaoErro(f"XSD não encontrado: {caminho}")
        parser = etree.XMLParser(no_network=True, resolve_entities=False)
        parser.resolvers.add(_ResolvedorLocal(pasta))
        try:
            raiz = etree.fromstring(_sem_ancoras(caminho.read_text("utf-8-sig")).encode(), parser,
                                    base_url=str(caminho))
            self._esquema = etree.XMLSchema(etree.ElementTree(raiz))
        except (etree.XMLSyntaxError, etree.XMLSchemaParseError) as erro:
            raise ValidacaoErro(f"XSD inválido ({caminho.name}): {erro}") from erro
        self.arquivo = caminho

    @staticmethod
    def _localizar_dps(pasta: Path) -> Path:
        candidatos = sorted(pasta.glob("DPS_v*.xsd"))
        if not candidatos:
            raise ValidacaoErro(f"Nenhum DPS_v*.xsd em {pasta}")
        return candidatos[-1]

    def mensagens(self, xml: etree._Element | bytes) -> list[str]:
        """Violações do schema (vazia se válido)."""
        elemento = self._como_elemento(xml)
        if self._esquema.validate(elemento):
            return []
        return [f"linha {erro.line}: {erro.message}" for erro in self._esquema.error_log]

    def validar(self, xml: etree._Element | bytes) -> None:
        """Levanta ``ValidacaoErro`` com as primeiras violações do schema."""
        mensagens = self.mensagens(xml)
        if mensagens:
            resumo = "; ".join(mensagens[:MAXIMO_MENSAGENS])
            excedente = len(mensagens) - MAXIMO_MENSAGENS
            extra = f" (+{excedente})" if excedente > 0 else ""
            raise ValidacaoErro(f"XML fora do schema {self.arquivo.name}: {resumo}{extra}")

    @staticmethod
    def _como_elemento(xml: etree._Element | bytes) -> etree._Element:
        if isinstance(xml, bytes):
            parser = etree.XMLParser(no_network=True, resolve_entities=False)
            return etree.fromstring(xml, parser)
        return xml
