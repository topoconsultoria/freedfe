"""Manifestação do Destinatário da NF-e (requer lxml)."""

from freedfe.manifestacao.cliente import ClienteManifestacao, RetornoEvento, RetornoManifestacao
from freedfe.manifestacao.evento import DadosManifestacao, TipoManifestacao

__all__ = [
    "ClienteManifestacao",
    "DadosManifestacao",
    "RetornoEvento",
    "RetornoManifestacao",
    "TipoManifestacao",
]
