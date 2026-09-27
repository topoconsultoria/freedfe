"""Assinatura XMLDSig enveloped no padrão da NF-e (C14N 1.0, RSA-SHA1, SHA1)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from freedfe.certificado import CertificadoA1

NS_DS = "http://www.w3.org/2000/09/xmldsig#"
ALG_C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ALG_RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
ALG_SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"
ALG_ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"


class AssinadorXml:
    """Assina o primeiro filho do elemento (``infEvento``) referenciado pelo atributo ``Id``."""

    def __init__(self, certificado: CertificadoA1):
        self._certificado = certificado

    def assinar(self, elemento: etree._Element) -> etree._Element:
        """Acrescenta ``<Signature>`` ao elemento e o devolve."""
        alvo = elemento[0]
        info_assinada = self._signed_info(alvo.get("Id"), self._digest(alvo))
        assinatura = etree.SubElement(elemento, _ds("Signature"), nsmap={None: NS_DS})
        assinatura.append(info_assinada)
        _filho(assinatura, "SignatureValue", self._valor_assinatura(info_assinada))
        dados_x509 = etree.SubElement(etree.SubElement(assinatura, _ds("KeyInfo")), _ds("X509Data"))
        _filho(dados_x509, "X509Certificate",
               base64.b64encode(self._certificado.certificado_der()).decode())
        return elemento

    @staticmethod
    def _digest(alvo: etree._Element) -> str:
        return base64.b64encode(hashlib.sha1(etree.tostring(alvo, method="c14n")).digest()).decode()

    @staticmethod
    def _signed_info(referencia: str, digest: str) -> etree._Element:
        info = etree.Element(_ds("SignedInfo"), nsmap={None: NS_DS})
        etree.SubElement(info, _ds("CanonicalizationMethod"), Algorithm=ALG_C14N)
        etree.SubElement(info, _ds("SignatureMethod"), Algorithm=ALG_RSA_SHA1)
        ref = etree.SubElement(info, _ds("Reference"), URI=f"#{referencia}")
        transformacoes = etree.SubElement(ref, _ds("Transforms"))
        etree.SubElement(transformacoes, _ds("Transform"), Algorithm=ALG_ENVELOPED)
        etree.SubElement(transformacoes, _ds("Transform"), Algorithm=ALG_C14N)
        etree.SubElement(ref, _ds("DigestMethod"), Algorithm=ALG_SHA1)
        _filho(ref, "DigestValue", digest)
        return info

    def _valor_assinatura(self, info_assinada: etree._Element) -> str:
        canonico = etree.tostring(info_assinada, method="c14n")
        assinatura = self._certificado.chave_privada.sign(
            canonico, padding.PKCS1v15(), hashes.SHA1())
        return base64.b64encode(assinatura).decode()


def _ds(nome: str) -> str:
    return f"{{{NS_DS}}}{nome}"


def _filho(pai: etree._Element, nome: str, valor: str) -> None:
    etree.SubElement(pai, _ds(nome)).text = valor
