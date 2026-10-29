import base64
import datetime as dt
import gzip

import pytest
from lxml import etree

from freedfe.assinatura import PERFIL_NFSE, AssinadorXml, verificar_assinatura
from freedfe.excecoes import RespostaInvalidaErro, ValidacaoErro
from freedfe.nfse.envelope import (
    DECLARACAO_UTF8,
    com_declaracao_utf8,
    comprimir_b64,
    descomprimir_b64,
)
from freedfe.nfse.montador_dps import MontadorDpsNt009
from freedfe.tipos import Ambiente

from conftest import RelogioFalso
from fabrica_nfse import DATA_EMISSAO, dps_minima

XML = "<NFSe><nome>Serviço de teste</nome></NFSe>".encode()


# ---------------------------------------------------------------------------
# Declaração UTF-8 (E1229)
# ---------------------------------------------------------------------------

def test_declaracao_padrao_tem_aspas_duplas_e_nao_tem_quebra_de_linha():
    assert DECLARACAO_UTF8 == b'<?xml version="1.0" encoding="UTF-8"?>'
    assert com_declaracao_utf8(XML) == DECLARACAO_UTF8 + XML


@pytest.mark.parametrize("declaracao", [
    b'<?xml version="1.0" encoding="UTF-8"?>', b"<?xml version='1.0' encoding='utf-8'?>",
    b'<?xml version="1.0" encoding="utf8" standalone="yes"?>'])
def test_declaracao_utf8_existente_e_mantida(declaracao):
    xml = declaracao + XML
    assert com_declaracao_utf8(xml) == xml
    assert com_declaracao_utf8(com_declaracao_utf8(XML)) == com_declaracao_utf8(XML)


def test_declaracao_sem_encoding_e_trocada_e_bom_e_removido():
    assert com_declaracao_utf8(b'<?xml version="1.0"?>' + XML) == DECLARACAO_UTF8 + XML
    assert com_declaracao_utf8(b"\xef\xbb\xbf" + XML) == DECLARACAO_UTF8 + XML
    assert com_declaracao_utf8(b"\xef\xbb\xbf" + DECLARACAO_UTF8 + XML) == DECLARACAO_UTF8 + XML


def test_outra_codificacao_e_recusada():
    with pytest.raises(ValidacaoErro, match="E1229"):
        com_declaracao_utf8(b'<?xml version="1.0" encoding="ISO-8859-1"?><a/>')


def test_declaracao_nao_invalida_a_assinatura(certificado):
    relogio = RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))
    raiz = MontadorDpsNt009(Ambiente.HOMOLOGACAO, relogio).montar(dps_minima())
    AssinadorXml(certificado, PERFIL_NFSE).assinar(raiz)
    sem = etree.tostring(raiz, encoding="utf-8")
    com = com_declaracao_utf8(sem)
    assert com == DECLARACAO_UTF8 + sem  # só o prefixo; o resto é byte a byte igual
    assert verificar_assinatura(etree.fromstring(com), PERFIL_NFSE)


def test_ida_e_volta_e_determinismo():
    assert descomprimir_b64(comprimir_b64(XML)) == XML
    assert comprimir_b64(XML) == comprimir_b64(XML)  # mtime zerado
    assert gzip.decompress(base64.b64decode(comprimir_b64(XML))) == XML


def test_base64_duplo_do_gzip():
    duplo = base64.b64encode(comprimir_b64(XML).encode()).decode()
    assert descomprimir_b64(duplo) == XML


def test_xml_em_base64_sem_gzip_e_com_quebras_de_linha():
    assert descomprimir_b64(base64.b64encode(XML)) == XML
    com_quebras = comprimir_b64(XML)
    com_quebras = com_quebras[:20] + "\n" + com_quebras[20:] + "\n"
    assert descomprimir_b64(com_quebras) == XML


@pytest.mark.parametrize("conteudo", ["", "   ", "!!!não é base64!!!",
                                      base64.b64encode(b"texto qualquer").decode(),
                                      base64.b64encode(b"\x1f\x8bcorrompido").decode()])
def test_conteudo_invalido(conteudo):
    with pytest.raises(RespostaInvalidaErro):
        descomprimir_b64(conteudo)
