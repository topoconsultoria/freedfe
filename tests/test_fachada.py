import datetime as dt
import json

import pytest

from freedfe import (
    Ambiente,
    CertificadoErro,
    FiltroConsulta,
    FormatoSaida,
    FreeDFe,
    ModeloDocumento,
    ResumoDocumento,
    SincronizacaoEmAndamentoErro,
    ValidacaoErro,
)
from freedfe.pdf.gerador import GeradorPdf

from conftest import TransporteFalso
from fabrica_xml import (
    CNPJ_FORNECEDOR,
    CNPJ_INTERESSADO,
    CNPJ_TRANSPORTADORA,
    cte_proc,
    doc_zip,
    gerar_chave,
    nfe_proc,
    res_nfe,
    resposta_distribuicao,
    resposta_evento,
)

CHAVE_COMPLETA = gerar_chave("55", CNPJ_FORNECEDOR, 1)
CHAVE_RESUMO = gerar_chave("55", CNPJ_FORNECEDOR, 2)
CHAVE_CTE = gerar_chave("57", CNPJ_TRANSPORTADORA, 1)


@pytest.fixture
def dfe(tmp_path, certificado, relogio):
    transporte = TransporteFalso([
        resposta_distribuicao("138", 2, 2, [
            doc_zip(nfe_proc(CHAVE_COMPLETA), 1, "procNFe_v4.00.xsd"),
            doc_zip(res_nfe(CHAVE_RESUMO), 2, "resNFe_v1.01.xsd")]),
        resposta_distribuicao("138", 1, 1, [doc_zip(cte_proc(CHAVE_CTE), 1, "procCTe_v4.00.xsd")],
                              ns_dados="http://www.portalfiscal.inf.br/cte"),
    ])
    instancia = FreeDFe(certificado, cnpj=CNPJ_INTERESSADO, uf="29",
                        ambiente=Ambiente.HOMOLOGACAO, pasta=tmp_path, transporte=transporte,
                        relogio=relogio, pausa_entre_lotes=0)
    instancia.sincronizar(["nfe", "cte"])
    instancia.transporte_falso = transporte
    return instancia


def test_sincronizar_organiza_pastas_por_ambiente_e_interessado(dfe, tmp_path):
    base = tmp_path / "homologacao" / CNPJ_INTERESSADO
    assert (base / "nfe" / "indice.json").exists()
    assert (base / "_estado" / "nsu_nfe.json").exists()
    assert (base / "_estado" / "chamadas.jsonl").exists()
    assert dfe.estado_nsu("nfe")["ult_nsu"] == "000000000000002"


def test_consultar_em_cada_formato(dfe, tmp_path):
    filtro = FiltroConsulta(data_inicial=dt.date(2026, 7, 1), data_final=dt.date(2026, 7, 31),
                            modelos=[ModeloDocumento.NFE])
    lista = dfe.consultar(filtro)
    assert {r.chave for r in lista} == {CHAVE_COMPLETA, CHAVE_RESUMO}
    assert all(isinstance(r, ResumoDocumento) for r in lista)
    assert len(json.loads(dfe.consultar(filtro, formato="json"))) == 2
    assert dfe.consultar(filtro, formato=FormatoSaida.CSV).count("\r\n") == 3
    assert dfe.consultar(filtro, formato="xml").count("<documento>") == 2
    assert dfe.consultar(filtro, formato="xlsx")[:2] == b"PK"
    destino = dfe.consultar(filtro, formato="xlsx", destino=tmp_path / "saida" / "dfe.xlsx")
    assert destino.exists()


def test_consultar_recebidos_inclui_cte_tomado(dfe):
    recebidos = dfe.consultar(FiltroConsulta(direcao="recebidos"))
    assert {r.chave for r in recebidos} == {CHAVE_COMPLETA, CHAVE_RESUMO, CHAVE_CTE}
    assert dfe.consultar(FiltroConsulta(direcao="emitidos")) == []


def test_baixar_xmls(dfe, tmp_path):
    arquivos = dfe.baixar_xmls(FiltroConsulta(modelos=["nfe"]), tmp_path / "xml")
    assert [a.name for a in arquivos] == [f"{CHAVE_COMPLETA}.xml"]
    assert arquivos[0].read_bytes() == nfe_proc(CHAVE_COMPLETA)
    com_resumos = dfe.baixar_xmls(FiltroConsulta(modelos=["nfe"]), tmp_path / "xml2",
                                  incluir_resumos=True)
    assert len(com_resumos) == 2


def test_gerar_pdf_danfe_real(dfe, tmp_path):
    resultado = dfe.gerar_pdfs(FiltroConsulta(modelos=["nfe"]), tmp_path / "pdf")
    assert resultado.falhas == {}
    [pdf] = resultado.gerados
    assert pdf.name == f"{CHAVE_COMPLETA}.pdf" and pdf.read_bytes().startswith(b"%PDF")


def test_gerar_pdf_continua_apos_falha(dfe, tmp_path):
    class GeradorQueFalha(GeradorPdf):
        def gerar(self, xml, modelo, destino):
            raise RuntimeError("layout inesperado")

    dfe._gerador_pdf = GeradorQueFalha()
    resultado = dfe.gerar_pdfs(destino=tmp_path / "pdf")
    assert resultado.gerados == [] and set(resultado.falhas) == {CHAVE_COMPLETA, CHAVE_CTE}


def test_pendentes_e_manifestacao(dfe):
    pendentes = dfe.pendentes_manifestacao()
    assert [p.chave for p in pendentes] == [CHAVE_RESUMO]
    dfe.transporte_falso.respostas.append(resposta_evento([CHAVE_RESUMO]))
    [retorno] = dfe.manifestar_ciencia([p.chave for p in pendentes])
    assert retorno.eventos[0].registrado


def test_consulta_local_sem_certificado(dfe, tmp_path):
    somente_leitura = FreeDFe(cnpj=CNPJ_INTERESSADO, ambiente=Ambiente.HOMOLOGACAO, pasta=tmp_path)
    assert len(somente_leitura.consultar()) == 3
    with pytest.raises(CertificadoErro):
        somente_leitura.sincronizar(["nfe"])


def test_uf_obrigatoria_para_cte_antes_de_qualquer_chamada(tmp_path, certificado):
    transporte = TransporteFalso()
    dfe = FreeDFe(certificado, cnpj=CNPJ_INTERESSADO, pasta=tmp_path, transporte=transporte)
    with pytest.raises(ValidacaoErro, match="CTE"):
        dfe.sincronizar()
    assert transporte.enviados == []


def test_trava_impede_consumo_concorrente(tmp_path, certificado):
    dfe = FreeDFe(certificado, cnpj=CNPJ_INTERESSADO, uf="29", pasta=tmp_path,
                  transporte=TransporteFalso())
    trava = dfe._arquivo_estado(ModeloDocumento.NFE).with_suffix(".lock")
    trava.parent.mkdir(parents=True)
    trava.write_text("pid=1")
    with pytest.raises(SincronizacaoEmAndamentoErro):
        dfe.sincronizar(["nfe"])
