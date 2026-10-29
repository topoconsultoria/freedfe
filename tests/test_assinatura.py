import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from freedfe.assinatura import (
    ALG_EXC_C14N,
    ALG_RSA_SHA256,
    ALG_SHA256,
    NS_DS,
    PERFIL_NFE,
    PERFIL_NFSE,
    PERFIL_NFSE_SEFIN,
    PERFIL_NFSE_SHA1,
    AssinadorXml,
    canonicalizar,
    verificar_assinatura,
)
from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.manifestacao.evento import DadosManifestacao, MontadorEvento, TipoManifestacao
from freedfe.tipos import Ambiente

from fabrica_xml import CNPJ_FORNECEDOR, CNPJ_INTERESSADO, gerar_chave

NS = "http://www.sped.fazenda.gov.br/nfse"
ID_DPS = "DPS" + "2927408" + "2" + CNPJ_INTERESSADO + "00001" + "000000000000001"


def dps_minima(extra_nsmap: dict | None = None) -> etree._Element:
    """DPS reduzida: o que importa aqui é a posição e a canonicalização da assinatura."""
    raiz = etree.Element(f"{{{NS}}}DPS", nsmap={None: NS, **(extra_nsmap or {})}, versao="1.01")
    inf = etree.SubElement(raiz, f"{{{NS}}}infDPS", Id=ID_DPS)
    etree.SubElement(inf, f"{{{NS}}}tpAmb").text = "2"
    etree.SubElement(inf, f"{{{NS}}}xDescServ").text = "Serviço de teste & <ok>"
    return raiz


def assinada(certificado, perfil=PERFIL_NFSE, extra_nsmap=None) -> etree._Element:
    raiz = AssinadorXml(certificado, perfil).assinar(dps_minima(extra_nsmap))
    return etree.fromstring(etree.tostring(raiz))  # simula o documento serializado e relido


def _ds(nome: str) -> str:
    return f"{{{NS_DS}}}{nome}"


def test_perfil_padrao_continua_sendo_o_da_nfe(certificado):
    assert AssinadorXml(certificado).perfil is PERFIL_NFE
    evento = MontadorEvento(Ambiente.HOMOLOGACAO, Interessado(cnpj=CNPJ_INTERESSADO)).evento(
        DadosManifestacao(gerar_chave("55", CNPJ_FORNECEDOR, 1), TipoManifestacao.CIENCIA))
    AssinadorXml(certificado).assinar(evento)
    info = evento.find(f"{_ds('Signature')}/{_ds('SignedInfo')}")
    assert info.find(_ds("SignatureMethod")).get("Algorithm").endswith("#rsa-sha1")
    assert verificar_assinatura(etree.fromstring(etree.tostring(evento)))


def test_perfil_nfse_declara_algoritmos_exclusivos_sha256(certificado):
    info = assinada(certificado).find(f"{_ds('Signature')}/{_ds('SignedInfo')}")
    assert info.find(_ds("CanonicalizationMethod")).get("Algorithm") == ALG_EXC_C14N
    assert info.find(_ds("SignatureMethod")).get("Algorithm") == ALG_RSA_SHA256
    referencia = info.find(_ds("Reference"))
    assert referencia.get("URI") == f"#{ID_DPS}"
    assert referencia.find(_ds("DigestMethod")).get("Algorithm") == ALG_SHA256
    transforms = [t.get("Algorithm") for t in referencia.iterfind(f".//{_ds('Transform')}")]
    assert transforms == [
        "http://www.w3.org/2000/09/xmldsig#enveloped-signature", ALG_EXC_C14N]


def test_signature_e_irma_de_infdps_sem_prefixo(certificado):
    raiz = AssinadorXml(certificado, PERFIL_NFSE).assinar(dps_minima())
    assert [etree.QName(f).localname for f in raiz] == ["infDPS", "Signature"]  # E1235
    assert raiz[0].find(f".//{_ds('Signature')}") is None
    xml = etree.tostring(raiz)
    assert b"<ds:" not in xml  # E1228
    assert b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">' in xml


def test_keyinfo_tem_somente_o_certificado_final(certificado):
    assinatura = assinada(certificado).find(_ds("Signature"))
    certificados = assinatura.findall(
        f"{_ds('KeyInfo')}/{_ds('X509Data')}/{_ds('X509Certificate')}")
    assert len(certificados) == 1
    assert base64.b64decode(certificados[0].text) == certificado.certificado_der()


def test_digest_sobre_a_forma_canonica_exclusiva_com_xmlns(certificado):
    """Confere o digest por um caminho independente do assinador (armadilha E0714)."""
    raiz = assinada(certificado)
    canonico = etree.tostring(raiz[0], method="c14n", exclusive=True)
    assert canonico.startswith(b'<infDPS xmlns="http://www.sped.fazenda.gov.br/nfse" Id="DPS')
    assert canonicalizar(raiz[0], PERFIL_NFSE) == canonico
    declarado = raiz.findtext(f"{_ds('Signature')}/{_ds('SignedInfo')}/{_ds('Reference')}/"
                              f"{_ds('DigestValue')}")
    assert base64.b64decode(declarado) == hashlib.sha256(canonico).digest()


def test_valor_da_assinatura_confere_por_caminho_independente(certificado):
    raiz = assinada(certificado)
    assinatura = raiz.find(_ds("Signature"))
    canonico = etree.tostring(assinatura.find(_ds("SignedInfo")), method="c14n", exclusive=True)
    valor = base64.b64decode(assinatura.findtext(_ds("SignatureValue")))
    certificado.certificado_x509.public_key().verify(
        valor, canonico, padding.PKCS1v15(), hashes.SHA256())


def test_namespace_nao_utilizado_fica_fora_da_forma_canonica(certificado):
    raiz = assinada(certificado, extra_nsmap={"x": "urn:nao-usado"})
    assert b"urn:nao-usado" not in canonicalizar(raiz[0], PERFIL_NFSE)
    assert verificar_assinatura(raiz)


@pytest.mark.parametrize("perfil", [PERFIL_NFSE, PERFIL_NFSE_SHA1, PERFIL_NFE, PERFIL_NFSE_SEFIN])
def test_verificar_assinatura_ida_e_volta(certificado, perfil):
    raiz = assinada(certificado, perfil)
    assert verificar_assinatura(raiz, perfil)
    assert verificar_assinatura(raiz)  # perfil deduzido dos algoritmos declarados


def test_assinatura_da_sefin_with_comments_ignora_comentarios_na_referencia_por_id(certificado):
    """A NFS-e real vem com ``xml-exc-c14n#WithComments``; com ``URI="#Id"`` comentários saem."""
    raiz = assinada(certificado, PERFIL_NFSE_SEFIN)
    assert raiz.find(f"{_ds('Signature')}/{_ds('SignedInfo')}/{_ds('CanonicalizationMethod')}"
                     ).get("Algorithm").endswith("#WithComments")
    raiz[0].append(etree.Comment(" comentário acrescentado depois da assinatura "))
    assert verificar_assinatura(raiz)


def test_perfil_errado_nao_verifica(certificado):
    assert not verificar_assinatura(assinada(certificado, PERFIL_NFSE), PERFIL_NFE)


def test_alteracao_do_conteudo_quebra_a_verificacao(certificado):
    raiz = assinada(certificado)
    raiz[0].find(f"{{{NS}}}tpAmb").text = "1"
    assert not verificar_assinatura(raiz)


def test_alteracao_do_valor_da_assinatura_quebra_a_verificacao(certificado):
    raiz = assinada(certificado)
    valor = raiz.find(f"{_ds('Signature')}/{_ds('SignatureValue')}")
    bruto = bytearray(base64.b64decode(valor.text))
    bruto[0] ^= 0xFF
    valor.text = base64.b64encode(bytes(bruto)).decode()
    assert not verificar_assinatura(raiz)


@pytest.mark.parametrize("xml", [
    b"<a/>", b"<a><b Id='x'/><Signature xmlns='%s'/></a>" % NS_DS.encode()])
def test_verificar_assinatura_ausente_ou_malformada_devolve_falso(xml):
    assert not verificar_assinatura(etree.fromstring(xml))


def test_assinar_exige_id_no_alvo(certificado):
    raiz = etree.Element("raiz")
    etree.SubElement(raiz, "filho")
    with pytest.raises(ValidacaoErro, match="Id"):
        AssinadorXml(certificado, PERFIL_NFSE).assinar(raiz)


def test_alvo_explicito_diferente_do_primeiro_filho(certificado):
    raiz = dps_minima()
    cabecalho = etree.Element(f"{{{NS}}}antes")
    raiz.insert(0, cabecalho)
    AssinadorXml(certificado, PERFIL_NFSE).assinar(raiz, alvo=raiz[1])
    referencia = raiz.find(f"{_ds('Signature')}/{_ds('SignedInfo')}/{_ds('Reference')}")
    assert referencia.get("URI") == f"#{ID_DPS}"
