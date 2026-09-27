import os
import stat

import pytest

from freedfe.certificado import CertificadoA1
from freedfe.excecoes import CertificadoErro

from conftest import SENHA_TESTE, gerar_pfx
from fabrica_xml import CNPJ_INTERESSADO


def test_info_extrai_cnpj_do_oid_icp_brasil(certificado):
    info = certificado.info()
    assert info.cnpj == CNPJ_INTERESSADO and info.cpf is None
    assert not info.vencido and info.dias_para_vencer > 300


def test_senha_errada(pfx_teste):
    with pytest.raises(CertificadoErro):
        CertificadoA1(pfx_teste, "errada")


def test_certificado_vencido():
    certificado = CertificadoA1(gerar_pfx(dias_validade=-1), SENHA_TESTE)
    with pytest.raises(CertificadoErro, match="vencido"):
        certificado.verificar_validade()


def test_pem_temporario_com_permissao_restrita_e_removido(certificado):
    with certificado.arquivos_pem() as (cert_pem, chave_pem):
        for caminho in (cert_pem, chave_pem):
            assert stat.S_IMODE(os.stat(caminho).st_mode) == 0o600
    assert not os.path.exists(cert_pem) and not os.path.exists(chave_pem)
