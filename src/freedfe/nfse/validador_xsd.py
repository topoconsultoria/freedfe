"""Validação opcional da DPS (e de outros XML da NFS-e) contra os XSD oficiais.

Os XSD **não** são distribuídos com a biblioteca. Baixe o pacote de esquemas do leiaute
desejado no portal da NFS-e Nacional (documentação técnica) e aponte a pasta, por exemplo
``NFSe-ESQUEMAS_XSD-v1.01-20260209`` para a NT 004 v2.0 ou o pacote ``PRODREST`` para a
NT 009 v1.01. O validador usa ``DPS_v*.xsd`` (ou o arquivo informado) e resolve os
``include``/``import`` na mesma pasta, sem acessar a rede.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from freedfe.excecoes import ValidacaoErro

MAXIMO_MENSAGENS = 10


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
        try:
            self._esquema = etree.XMLSchema(etree.parse(str(caminho), parser))
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
