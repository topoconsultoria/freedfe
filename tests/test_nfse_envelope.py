import base64
import gzip

import pytest

from freedfe.excecoes import RespostaInvalidaErro
from freedfe.nfse.envelope import comprimir_b64, descomprimir_b64

XML = "<NFSe><nome>Serviço de teste</nome></NFSe>".encode()


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
