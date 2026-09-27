import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from freedfe.auditoria import RegistroAuditoria
from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.manifestacao.assinatura import NS_DS, AssinadorXml
from freedfe.manifestacao.cliente import ClienteManifestacao
from freedfe.manifestacao.evento import (
    NS_NFE,
    DadosManifestacao,
    MontadorEvento,
    TipoManifestacao,
)
from freedfe.tipos import Ambiente

from conftest import TransporteFalso
from fabrica_xml import CNPJ_FORNECEDOR, CNPJ_INTERESSADO, gerar_chave, resposta_evento

AUTOR = Interessado(cnpj=CNPJ_INTERESSADO)
CHAVE = gerar_chave("55", CNPJ_FORNECEDOR, 1)


def test_evento_ciencia():
    evento = MontadorEvento(Ambiente.HOMOLOGACAO, AUTOR).evento(
        DadosManifestacao(CHAVE, TipoManifestacao.CIENCIA))
    inf = evento.find(f"{{{NS_NFE}}}infEvento")
    assert inf.get("Id") == f"ID210210{CHAVE}01" and len(inf.get("Id")) == 54
    assert inf.findtext(f"{{{NS_NFE}}}cOrgao") == "91"
    assert inf.findtext(f"{{{NS_NFE}}}CNPJ") == CNPJ_INTERESSADO
    assert inf.findtext(f".//{{{NS_NFE}}}descEvento") == "Ciencia da Operacao"
    assert inf.findtext(f"{{{NS_NFE}}}dhEvento").endswith("-03:00")


@pytest.mark.parametrize("dados", [
    DadosManifestacao(CHAVE, TipoManifestacao.NAO_REALIZADA, "curta"),
    DadosManifestacao(CHAVE, TipoManifestacao.CIENCIA, "nao permitida aqui"),
    DadosManifestacao(CHAVE, TipoManifestacao.CONFIRMACAO, sequencia=3),
    DadosManifestacao("123", TipoManifestacao.CIENCIA),
])
def test_validacoes(dados):
    with pytest.raises(ValidacaoErro):
        dados.validar()


def test_assinatura_verificavel(certificado):
    evento = MontadorEvento(Ambiente.HOMOLOGACAO, AUTOR).evento(
        DadosManifestacao(CHAVE, TipoManifestacao.CIENCIA))
    AssinadorXml(certificado).assinar(evento)
    xml = etree.fromstring(etree.tostring(evento))  # simula o documento serializado
    inf = xml.find(f"{{{NS_NFE}}}infEvento")
    assinatura = xml.find(f"{{{NS_DS}}}Signature")
    info = assinatura.find(f"{{{NS_DS}}}SignedInfo")

    digest = base64.b64encode(hashlib.sha1(etree.tostring(inf, method="c14n")).digest()).decode()
    assert info.findtext(f".//{{{NS_DS}}}DigestValue") == digest
    assert info.find(f".//{{{NS_DS}}}Reference").get("URI") == f"#{inf.get('Id')}"
    valor = base64.b64decode(assinatura.findtext(f"{{{NS_DS}}}SignatureValue"))
    certificado.certificado_x509.public_key().verify(
        valor, etree.tostring(info, method="c14n"), padding.PKCS1v15(), hashes.SHA1())


def test_envio_em_lotes_de_20(certificado, tmp_path):
    chaves = [gerar_chave("55", CNPJ_FORNECEDOR, n) for n in range(1, 23)]
    transporte = TransporteFalso([resposta_evento(chaves[:20]), resposta_evento(chaves[20:])])
    cliente = ClienteManifestacao(Ambiente.HOMOLOGACAO, AUTOR, transporte,
                                  AssinadorXml(certificado),
                                  RegistroAuditoria(tmp_path / "a.jsonl"))
    retornos = cliente.ciencia(chaves)
    assert [len(r.eventos) for r in retornos] == [20, 2]
    assert all(ev.registrado for r in retornos for ev in r.eventos)
    assert "hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4" in transporte.enviados[0][0]


def test_chave_invalida_impede_envio_de_qualquer_lote(certificado, tmp_path):
    transporte = TransporteFalso()
    cliente = ClienteManifestacao(Ambiente.HOMOLOGACAO, AUTOR, transporte,
                                  AssinadorXml(certificado),
                                  RegistroAuditoria(tmp_path / "a.jsonl"))
    with pytest.raises(ValidacaoErro):
        cliente.ciencia([CHAVE, "invalida"])
    assert transporte.enviados == []
