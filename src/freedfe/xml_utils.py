"""Funções auxiliares para leitura de XML ignorando namespaces."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def nome_local(tag: str) -> str:
    """Remove o namespace de uma tag (``{ns}infNFe`` -> ``infNFe``)."""
    return tag.rsplit("}", 1)[-1]


def achar(no: ET.Element | None, *caminho: str) -> ET.Element | None:
    """Localiza o primeiro elemento (o próprio nó ou descendente) pela sequência de nomes locais.

    Exemplo: ``achar(raiz, "emit", "CNPJ")`` encontra o primeiro ``emit`` e,
    dentro dele, o primeiro ``CNPJ`` (em qualquer profundidade).
    """
    atual = no
    for nome in caminho:
        if atual is None:
            return None
        atual = next((el for el in atual.iter() if nome_local(el.tag) == nome), None)
    return atual


def texto(no: ET.Element | None, *caminho: str) -> str | None:
    """Texto (sem espaços nas pontas) do elemento localizado por ``achar``."""
    elemento = achar(no, *caminho)
    if elemento is None or elemento.text is None:
        return None
    return elemento.text.strip() or None


def todos(no: ET.Element | None, nome: str) -> list[ET.Element]:
    """Todos os elementos (o próprio nó e descendentes) com o nome local informado."""
    if no is None:
        return []
    return [el for el in no.iter() if nome_local(el.tag) == nome]
