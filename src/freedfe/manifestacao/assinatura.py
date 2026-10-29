"""Compatibilidade: a assinatura XMLDSig agora vive em :mod:`freedfe.assinatura`.

``AssinadorXml`` mantém o perfil da NF-e (C14N 1.0, RSA-SHA1) como padrão.
"""

from freedfe.assinatura import (
    ALG_C14N,
    ALG_ENVELOPED,
    ALG_RSA_SHA1,
    ALG_SHA1,
    NS_DS,
    AssinadorXml,
)

__all__ = ["ALG_C14N", "ALG_ENVELOPED", "ALG_RSA_SHA1", "ALG_SHA1", "NS_DS", "AssinadorXml"]
