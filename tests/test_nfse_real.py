"""Verificações contra uma NFS-e REAL emitida em produção (opt-in, arquivo fora do repositório).

O XML real contém dados do tomador e não pode ser versionado. Aponte o arquivo em
``FREEDFE_NFSE_REAL=/caminho/nota.xml`` para conferir, contra o que a Sefin de fato gerou:
assinatura da Sefin (``xml-exc-c14n#WithComments``), DV da chave, leitura no repositório e DANFSe.
"""

import os
from pathlib import Path

import pytest
from lxml import etree

from freedfe.assinatura import verificar_assinatura
from freedfe.documentos.leitor import LeitorDocumentoFiscal
from freedfe.pdf.danfse import GeradorDanfse, extrair_dados
from freedfe.tipos import ModeloDocumento
from freedfe.validadores import _digito_modulo_11, validar_chave_nfse

CAMINHO = os.environ.get("FREEDFE_NFSE_REAL")
pytestmark = pytest.mark.skipif(not CAMINHO, reason="defina FREEDFE_NFSE_REAL=<nota.xml>")


@pytest.fixture(scope="module")
def xml() -> bytes:
    return Path(CAMINHO).read_bytes()


@pytest.fixture(scope="module")
def chave(xml) -> str:
    return etree.fromstring(xml)[0].get("Id")[3:]


def test_assinatura_da_sefin_confere_e_adulteracao_e_detectada(xml):
    assert verificar_assinatura(etree.fromstring(xml))
    raiz = etree.fromstring(xml)
    numero = raiz.find(".//{http://www.sped.fazenda.gov.br/nfse}nNFSe")
    numero.text = str(int(numero.text) + 1)
    assert not verificar_assinatura(raiz)


def test_chave_e_dv_modulo_11(chave):
    assert validar_chave_nfse(chave)
    assert int(chave[-1]) == _digito_modulo_11(chave[:-1], 9)


def test_leitura_no_repositorio(xml, chave):
    resumo = LeitorDocumentoFiscal().ler(xml)
    assert resumo.chave == chave and resumo.modelo is ModeloDocumento.NFSE
    assert resumo.valor_total is not None


def test_danfse(xml, tmp_path):
    dados = extrair_dados(xml)
    assert dados.numero and dados.chave
    destino = GeradorDanfse().gerar(xml, ModeloDocumento.NFSE, tmp_path / "real.pdf")
    assert destino.read_bytes().startswith(b"%PDF")
