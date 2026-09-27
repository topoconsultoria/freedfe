import pytest

from freedfe.distribuicao.retorno import InterpretadorRetorno
from freedfe.excecoes import RespostaInvalidaErro

from fabrica_xml import (
    CNPJ_INTERESSADO,
    doc_zip,
    gerar_chave,
    res_nfe,
    resposta_distribuicao,
)


def test_interpreta_lote_com_doczip():
    xml = res_nfe(gerar_chave("55", CNPJ_INTERESSADO, 1))
    resposta = resposta_distribuicao("138", 3, 10, [doc_zip(xml, 3, "resNFe_v1.01.xsd")])
    ret = InterpretadorRetorno().interpretar(resposta)
    assert ret.cstat == "138" and ret.documentos_localizados
    assert ret.ult_nsu == "000000000000003" and ret.max_nsu == "000000000000010"
    assert not ret.sem_novos
    assert ret.documentos[0].xml == xml
    assert ret.documentos[0].nsu == "000000000000003"
    assert ret.documentos[0].schema == "resNFe_v1.01.xsd"


def test_sem_novos_137_ou_ult_igual_max():
    interpretador = InterpretadorRetorno()
    assert interpretador.interpretar(resposta_distribuicao("137", 5, 5)).sem_novos
    assert interpretador.interpretar(resposta_distribuicao("138", 9, 9)).sem_novos


def test_soap_fault_levanta_erro():
    fault = (b'<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
             b"<soap:Fault><soap:Reason><soap:Text>Erro</soap:Text></soap:Reason></soap:Fault>"
             b"</soap:Body></soap:Envelope>")
    with pytest.raises(RespostaInvalidaErro, match="Erro"):
        InterpretadorRetorno().interpretar(fault)
