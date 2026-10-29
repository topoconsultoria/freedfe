"""Assinatura XMLDSig enveloped com perfis de algoritmos (NF-e e NFS-e Nacional).

Dois perfis prontos:

``PERFIL_NFE`` (padrão, usado na manifestação do destinatário)
    C14N 1.0 inclusiva, RSA-SHA1, SHA-1.

``PERFIL_NFSE`` (DPS e pedidos de evento da NFS-e Nacional)
    **Exclusive** C14N, RSA-SHA256, SHA-256. Perfil aceito na produção restrita
    (validador ``SefinNacional_1.6.0``, 14/10/2026): C14N inclusiva é rejeitada com E0714 e
    C14N 1.1 com E0715; ``Signature`` dentro de ``infDPS`` gera E1235; prefixo ``ds:`` gera
    E1228; ``KeyInfo`` só com o certificado final (cadeia gera E0714).

O pacote de XSD 1.00 fixava RSA-SHA1/SHA-1 no esquema; por isso o perfil é configurável
(``PERFIL_NFSE_SHA1``) caso algum ambiente o exija.
"""

from __future__ import annotations

import base64
import copy
from collections.abc import Callable
from dataclasses import dataclass

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from freedfe.certificado import CertificadoA1
from freedfe.excecoes import ValidacaoErro

NS_DS = "http://www.w3.org/2000/09/xmldsig#"
ALG_C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ALG_EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ALG_RSA_SHA1 = "http://www.w3.org/2000/09/xmldsig#rsa-sha1"
ALG_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ALG_SHA1 = "http://www.w3.org/2000/09/xmldsig#sha1"
ALG_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
ALG_ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"


@dataclass(frozen=True)
class PerfilAssinatura:
    """Conjunto de algoritmos usado para assinar e verificar."""

    nome: str
    c14n_uri: str
    c14n_exclusivo: bool
    assinatura_uri: str
    digest_uri: str
    funcao_hash: Callable[[], hashes.HashAlgorithm]


PERFIL_NFE = PerfilAssinatura("nfe", ALG_C14N, False, ALG_RSA_SHA1, ALG_SHA1, hashes.SHA1)
PERFIL_NFSE = PerfilAssinatura(
    "nfse", ALG_EXC_C14N, True, ALG_RSA_SHA256, ALG_SHA256, hashes.SHA256)
PERFIL_NFSE_SHA1 = PerfilAssinatura(
    "nfse-sha1", ALG_EXC_C14N, True, ALG_RSA_SHA1, ALG_SHA1, hashes.SHA1)

_PERFIS_CONHECIDOS = (PERFIL_NFE, PERFIL_NFSE, PERFIL_NFSE_SHA1)


class AssinadorXml:
    """Assina um elemento referenciado pelo atributo ``Id``, no padrão enveloped.

    Args:
        certificado: certificado A1 com a chave privada.
        perfil: algoritmos da assinatura (padrão ``PERFIL_NFE``, compatível com a v0.1).
    """

    def __init__(self, certificado: CertificadoA1, perfil: PerfilAssinatura = PERFIL_NFE):
        self._certificado = certificado
        self._perfil = perfil

    @property
    def perfil(self) -> PerfilAssinatura:
        """Perfil de algoritmos em uso."""
        return self._perfil

    def assinar(self, elemento: etree._Element,
                alvo: etree._Element | None = None) -> etree._Element:
        """Acrescenta ``<Signature>`` como último filho de ``elemento`` e o devolve.

        Args:
            elemento: raiz que receberá a assinatura (``DPS``, ``pedRegEvento``, ``evento``).
            alvo: elemento assinado, com atributo ``Id``. Padrão: o primeiro filho de
                ``elemento`` (``infDPS``/``infPedReg``/``infEvento``). A ``Signature`` fica
                como irmã do alvo, nunca dentro dele.
        """
        alvo = elemento[0] if alvo is None else alvo
        identificador = alvo.get("Id")
        if not identificador:
            raise ValidacaoErro("O elemento a assinar precisa do atributo Id.")
        info_assinada = self._signed_info(identificador, self._digest(alvo))
        assinatura = etree.SubElement(elemento, _ds("Signature"), nsmap={None: NS_DS})
        assinatura.append(info_assinada)
        _filho(assinatura, "SignatureValue", self._valor_assinatura(info_assinada))
        dados_x509 = etree.SubElement(etree.SubElement(assinatura, _ds("KeyInfo")), _ds("X509Data"))
        _filho(dados_x509, "X509Certificate",
               base64.b64encode(self._certificado.certificado_der()).decode())
        return elemento

    def _digest(self, alvo: etree._Element) -> str:
        return base64.b64encode(_digest_bytes(canonicalizar(alvo, self._perfil), self._perfil)
                                ).decode()

    def _signed_info(self, referencia: str, digest: str) -> etree._Element:
        perfil = self._perfil
        info = etree.Element(_ds("SignedInfo"), nsmap={None: NS_DS})
        etree.SubElement(info, _ds("CanonicalizationMethod"), Algorithm=perfil.c14n_uri)
        etree.SubElement(info, _ds("SignatureMethod"), Algorithm=perfil.assinatura_uri)
        ref = etree.SubElement(info, _ds("Reference"), URI=f"#{referencia}")
        transformacoes = etree.SubElement(ref, _ds("Transforms"))
        etree.SubElement(transformacoes, _ds("Transform"), Algorithm=ALG_ENVELOPED)
        etree.SubElement(transformacoes, _ds("Transform"), Algorithm=perfil.c14n_uri)
        etree.SubElement(ref, _ds("DigestMethod"), Algorithm=perfil.digest_uri)
        _filho(ref, "DigestValue", digest)
        return info

    def _valor_assinatura(self, info_assinada: etree._Element) -> str:
        canonico = canonicalizar(info_assinada, self._perfil)
        assinatura = self._certificado.chave_privada.sign(
            canonico, padding.PKCS1v15(), self._perfil.funcao_hash())
        return base64.b64encode(assinatura).decode()


def canonicalizar(elemento: etree._Element, perfil: PerfilAssinatura) -> bytes:
    """Forma canônica do elemento segundo o perfil.

    No perfil exclusivo a canonicalização usa uma cópia isolada do elemento. Isso garante o
    ``xmlns`` herdado no elemento raiz da cópia, evitando a perda do namespace em subárvores
    (armadilha do libxml2 2.11 que gera E0714 mesmo com a assinatura conferindo localmente).
    O perfil inclusivo mantém o comportamento da v0.1 (elemento no documento original).
    """
    if perfil.c14n_exclusivo:
        return etree.tostring(copy.deepcopy(elemento), method="c14n", exclusive=True)
    return etree.tostring(elemento, method="c14n")


def verificar_assinatura(elemento: etree._Element,
                         perfil: PerfilAssinatura | None = None) -> bool:
    """Confere a ``Signature`` filha de ``elemento`` (digest e valor da assinatura).

    Usa a chave pública do ``X509Certificate`` embutido; **não** valida a cadeia nem a
    validade do certificado. Sem ``perfil``, ele é deduzido dos algoritmos declarados.
    Devolve ``False`` (nunca levanta) quando a assinatura está ausente, malformada ou não confere.
    """
    try:
        return _verificar(elemento, perfil)
    except (ValueError, TypeError, AttributeError, InvalidSignature, etree.LxmlError):
        return False


def _verificar(elemento: etree._Element, perfil: PerfilAssinatura | None) -> bool:
    assinatura = elemento.find(_ds("Signature"))
    if assinatura is None:
        return False
    info = assinatura.find(_ds("SignedInfo"))
    referencia = info.find(_ds("Reference"))
    perfil = perfil or _deduzir_perfil(info, referencia)
    if perfil is None:
        return False
    uri = referencia.get("URI", "")
    if not uri.startswith("#"):
        return False
    alvo = next((e for e in elemento.iter() if e.get("Id") == uri[1:]), None)
    if alvo is None:
        return False

    digest_calculado = _digest_bytes(canonicalizar(_sem_assinaturas(alvo), perfil), perfil)
    digest_declarado = base64.b64decode(referencia.findtext(_ds("DigestValue")) or "")
    if digest_calculado != digest_declarado:
        return False

    certificado = x509.load_der_x509_certificate(base64.b64decode(
        assinatura.findtext(f".//{_ds('X509Certificate')}") or ""))
    valor = base64.b64decode(assinatura.findtext(_ds("SignatureValue")) or "")
    certificado.public_key().verify(
        valor, canonicalizar(info, perfil), padding.PKCS1v15(), perfil.funcao_hash())
    return True


def _deduzir_perfil(info: etree._Element, referencia: etree._Element) -> PerfilAssinatura | None:
    declarado = (info.find(_ds("CanonicalizationMethod")).get("Algorithm"),
                 info.find(_ds("SignatureMethod")).get("Algorithm"),
                 referencia.find(_ds("DigestMethod")).get("Algorithm"))
    return next((p for p in _PERFIS_CONHECIDOS
                 if (p.c14n_uri, p.assinatura_uri, p.digest_uri) == declarado), None)


def _sem_assinaturas(alvo: etree._Element) -> etree._Element:
    """Transform enveloped-signature: devolve o alvo sem Signature descendente."""
    assinaturas = alvo.findall(f".//{_ds('Signature')}")
    if not assinaturas:
        return alvo
    copia = copy.deepcopy(alvo)
    for assinatura in copia.findall(f".//{_ds('Signature')}"):
        assinatura.getparent().remove(assinatura)
    return copia


def _digest_bytes(conteudo: bytes, perfil: PerfilAssinatura) -> bytes:
    calculo = hashes.Hash(perfil.funcao_hash())
    calculo.update(conteudo)
    return calculo.finalize()


def _ds(nome: str) -> str:
    return f"{{{NS_DS}}}{nome}"


def _filho(pai: etree._Element, nome: str, valor: str) -> None:
    etree.SubElement(pai, _ds(nome)).text = valor
