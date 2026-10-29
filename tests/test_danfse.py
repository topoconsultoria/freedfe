"""DANFSe em PDF com QR Code: extração dos dados, renderização e integração."""

import builtins
import datetime as dt
from pathlib import Path

import pytest

from freedfe import Ambiente, FiltroConsulta, FreeDFe, ModeloDocumento, SituacaoDocumento
from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.excecoes import DependenciaAusenteErro, ValidacaoErro
from freedfe.nfse.modelo import Servico
from freedfe.nfse.servico import ConfiguracaoNfseLocal
from freedfe.pdf.danfse import (
    TAMANHO_QR_MM,
    GeradorDanfse,
    extrair_dados,
    formatar_data,
    formatar_documento,
    formatar_moeda,
    formatar_percentual,
)
from freedfe.pdf.gerador import GeradorPdf, GeradorPdfBrazilFiscalReport, GeradorPdfPadrao

from conftest import RelogioFalso
from fabrica_nfse import (
    CNPJ_PRESTADOR,
    DATA_EMISSAO,
    MUNICIPIO,
    dps_completa,
    dps_minima,
    evento_nfse_xml,
    nfse_xml,
    tomador_pj,
)
from fabrica_xml import CNPJ_FORNECEDOR, gerar_chave, nfe_proc


def bloco(dados, titulo):
    return next(b for b in dados.blocos if b.titulo.startswith(titulo))


def valor(dados, titulo, rotulo):
    return next(c.valor for linha in bloco(dados, titulo).linhas for c in linha
                if c.rotulo == rotulo)


# ---------------------------------------------------------------------------
# Formatação e extração (sem reportlab)
# ---------------------------------------------------------------------------

def test_formatadores():
    assert formatar_documento("52998224725") == "529.982.247-25"
    assert formatar_documento("11222333000181") == "11.222.333/0001-81"
    assert formatar_documento("12ABC34501DE35") == "12.ABC.345/01DE-35"
    assert formatar_documento(None) == "" and formatar_documento("abc") == "abc"
    assert formatar_moeda("1234567.5") == "R$ 1.234.567,50" and formatar_moeda(None) == ""
    assert formatar_moeda("x") == ""
    assert formatar_percentual("2.5") == "2,50%" and formatar_percentual("") == ""
    assert formatar_data("2026-10-29T10:05:09-03:00", True) == "29/10/2026 10:05:09"
    assert formatar_data("2026-10-29") == "29/10/2026" and formatar_data("nao-data") == "nao-data"


def test_dados_da_nfse_minima_sem_tomador():
    chave, xml = nfse_xml(dps_minima(), numero_nfse=15)
    dados = extrair_dados(xml)
    assert dados.chave == chave and dados.numero == "15"
    assert dados.emissao == "29/10/2026 10:00:00" and dados.competencia == "29/10/2026"
    assert dados.serie_numero_dps == "900 / 6" and dados.ambiente is Ambiente.HOMOLOGACAO
    assert [b.titulo for b in dados.blocos] == [
        "PRESTADOR DO SERVIÇO", "TOMADOR DO SERVIÇO", "SERVIÇO PRESTADO",
        "TRIBUTAÇÃO MUNICIPAL (ISSQN)", "VALORES", "TOTAIS APROXIMADOS DOS TRIBUTOS"]
    assert valor(dados, "TOMADOR", "Tomador") == "NÃO IDENTIFICADO"
    assert valor(dados, "PRESTADOR", "CNPJ / CPF") == "11.222.333/0001-81"
    assert valor(dados, "PRESTADOR", "Simples Nacional") == "Optante - ME/EPP"
    assert valor(dados, "VALORES", "Valor do serviço") == "R$ 1.000,00"
    assert valor(dados, "VALORES", "VALOR LÍQUIDO DA NFS-e") == "R$ 1.000,00"
    assert valor(dados, "TOTAIS", "Simples Nacional (alíquota efetiva)") == "6,00%"


def test_dados_da_nfse_completa():
    dados = extrair_dados(nfse_xml(dps_completa())[1])
    titulos = [b.titulo for b in dados.blocos]
    assert "INTERMEDIÁRIO DO SERVIÇO" in titulos and "TRIBUTAÇÃO FEDERAL" in titulos
    assert "INFORMAÇÕES COMPLEMENTARES" in titulos
    assert valor(dados, "TOMADOR", "CNPJ / CPF / NIF") == "11.444.777/0001-61"
    assert "Rua das Flores, 100, Sala 2, Centro" in valor(dados, "TOMADOR", "Endereço")
    assert valor(dados, "SERVIÇO", "NBS").startswith("115022000")
    assert valor(dados, "TRIBUTAÇÃO MUNICIPAL", "Retenção do ISSQN") == "Retido pelo tomador"
    assert valor(dados, "TRIBUTAÇÃO MUNICIPAL", "Benefício municipal") == "1" * 14
    assert valor(dados, "TRIBUTAÇÃO FEDERAL", "Retenção IRRF") == "R$ 21,00"
    assert valor(dados, "VALORES", "Valor do serviço") == "R$ 1.500,50"
    assert valor(dados, "VALORES", "Desconto incondicionado") == "R$ 10,00"
    assert valor(dados, "TOTAIS", "Federais") == "13,45%"
    assert valor(dados, "INFORMAÇÕES", "Pedido") == "PED-9"
    assert "Obra de engenharia\nSegunda linha" == valor(dados, "SERVIÇO", "Descrição do serviço")


def test_bloco_ibscbs_so_aparece_quando_o_xml_o_traz():
    chave, xml = nfse_xml(dps_minima())
    assert all("IBS" not in b.titulo for b in extrair_dados(xml).blocos)
    # IBSCBS apenas dentro da DPS (informado pelo emissor): sem cálculo, sem bloco.
    assert all("IBS" not in b.titulo for b in extrair_dados(nfse_xml(dps_completa())[1]).blocos)
    com_ibs = xml.replace(
        b"<DPS ", b"<IBSCBS><cLocalidadeIncid>2927408</cLocalidadeIncid><xLocalidadeIncid>"
                  b"Salvador</xLocalidadeIncid><valores><vBC>1000.00</vBC><uf><pIBSUF>0.10"
                  b"</pIBSUF><pAliqEfetUF>0.10</pAliqEfetUF></uf><mun><pIBSMun>0.00</pIBSMun>"
                  b"<pAliqEfetMun>0.00</pAliqEfetMun></mun><fed><pCBS>0.90</pCBS><pAliqEfetCBS>"
                  b"0.90</pAliqEfetCBS></fed></valores><totCIBS><vTotNF>1000.00</vTotNF><gIBS>"
                  b"<vIBSTot>1.00</vIBSTot><gIBSUFTot><vIBSUF>1.00</vIBSUF></gIBSUFTot>"
                  b"<gIBSMunTot><vIBSMun>0.00</vIBSMun></gIBSMunTot></gIBS><gCBS><vCBS>9.00"
                  b"</vCBS></gCBS></totCIBS></IBSCBS><DPS ", 1)
    dados = extrair_dados(com_ibs)
    assert valor(dados, "IBS", "Alíquota CBS") == "0,90%"
    assert valor(dados, "IBS", "CBS") == "R$ 9,00"
    assert valor(dados, "IBS", "Total da NF (com IBS/CBS)") == "R$ 1.000,00"


@pytest.mark.parametrize("xml", [b"<nao-fecha", b"<outra/>", nfe_proc(
    gerar_chave("55", CNPJ_FORNECEDOR, 1))])
def test_xml_que_nao_e_nfse_e_recusado(xml):
    with pytest.raises(ValidacaoErro):
        extrair_dados(xml)


# ---------------------------------------------------------------------------
# Renderização
# ---------------------------------------------------------------------------

def pdf_bruto(tmp_path, xml, situacao=None, url=None, comprimir=False) -> bytes:
    destino = GeradorDanfse(url, comprimir=comprimir).gerar(
        xml, ModeloDocumento.NFSE, tmp_path / "saida" / "danfse.pdf", situacao)
    return destino.read_bytes()


def test_pdf_valido_com_conteudo_e_sem_marca_dagua(tmp_path):
    chave, xml = nfse_xml(dps_completa())
    pdf = pdf_bruto(tmp_path, xml, SituacaoDocumento.AUTORIZADO)
    assert pdf.startswith(b"%PDF") and b"PRESTADOR" in pdf and b"CANCELADA" not in pdf
    assert b"SUBSTITU" not in pdf


@pytest.mark.parametrize("situacao, marca", [(SituacaoDocumento.CANCELADO, b"CANCELADA"),
                                             (SituacaoDocumento.SUBSTITUIDO, b"SUBSTITU")])
def test_marca_dagua_por_situacao(tmp_path, situacao, marca):
    assert marca in pdf_bruto(tmp_path, nfse_xml()[1], situacao)


def test_pdf_comprimido_por_padrao_e_menor(tmp_path):
    xml = nfse_xml(dps_completa())[1]
    assert len(pdf_bruto(tmp_path, xml, comprimir=True)) < len(pdf_bruto(tmp_path, xml))


def test_qr_code_aponta_para_a_consulta_publica_do_ambiente_do_xml(tmp_path, monkeypatch):
    from reportlab.graphics.barcode import qr

    valores = []
    original = qr.QrCodeWidget
    monkeypatch.setattr(qr, "QrCodeWidget", lambda v, **kw: valores.append(v) or original(v, **kw))
    chave, xml = nfse_xml(dps_minima())  # tpAmb=2: produção restrita
    pdf_bruto(tmp_path, xml)
    assert valores == [f"https://www.producaorestrita.nfse.gov.br/ConsultaPublica/?chave={chave}"]
    valores.clear()
    pdf_bruto(tmp_path, xml, url=lambda c: f"https://exemplo.gov.br/c/{c}")
    assert valores == [f"https://exemplo.gov.br/c/{chave}"]


def test_qr_code_tem_tamanho_minimo_exigido():
    assert TAMANHO_QR_MM >= 15.2  # 1,52 cm


def test_descricao_com_markup_e_texto_longo_nao_quebram_o_pdf(tmp_path):
    descricao = "A&B <b>negrito</b> </para> <i>" + "\n".join(f"linha {n} " * 8 for n in range(60))
    servico = Servico("010101", descricao[:2000], codigo_municipio_prestacao=MUNICIPIO)
    xml = nfse_xml(dps_minima(servico=servico))[1]
    pdf = pdf_bruto(tmp_path, xml)
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") >= 1


def test_so_gera_danfse_para_nfse(tmp_path):
    with pytest.raises(ValidacaoErro, match="NFS-e"):
        GeradorDanfse().gerar(nfe_proc(gerar_chave("55", CNPJ_FORNECEDOR, 1)),
                              ModeloDocumento.NFE, tmp_path / "x.pdf")


def test_dependencia_ausente(tmp_path, monkeypatch):
    importar = builtins.__import__

    def sem_reportlab(nome, *args, **kwargs):
        if nome.startswith("reportlab"):
            raise ImportError("reportlab ausente")
        return importar(nome, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sem_reportlab)
    with pytest.raises(DependenciaAusenteErro, match=r"freedfe\[nfse\]"):
        GeradorDanfse().gerar(nfse_xml()[1], ModeloDocumento.NFSE, tmp_path / "x.pdf")


# ---------------------------------------------------------------------------
# Gerador padrão e fachada
# ---------------------------------------------------------------------------

class GeradorEspiao(GeradorPdf):
    def __init__(self):
        self.chamadas = []

    def gerar(self, xml, modelo, destino, situacao=None):
        self.chamadas.append((modelo, situacao))
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"%PDF-espiao")
        return destino


def test_gerador_padrao_roteia_por_modelo(tmp_path):
    nfse, outros = GeradorEspiao(), GeradorEspiao()
    gerador = GeradorPdfPadrao(documentos_dfe=outros, danfse=nfse)
    gerador.gerar(b"<x/>", "nfse", tmp_path / "a.pdf", SituacaoDocumento.CANCELADO)
    gerador.gerar(b"<x/>", ModeloDocumento.NFE, tmp_path / "b.pdf")
    assert nfse.chamadas == [(ModeloDocumento.NFSE, SituacaoDocumento.CANCELADO)]
    assert outros.chamadas == [(ModeloDocumento.NFE, None)]
    assert isinstance(GeradorPdfPadrao()._documentos_dfe, GeradorPdfBrazilFiscalReport)


@pytest.fixture
def relogio():
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


def criar_dfe(tmp_path, relogio, **extra):
    dfe = FreeDFe(cnpj=CNPJ_PRESTADOR, ambiente=Ambiente.HOMOLOGACAO, pasta=tmp_path,
                  relogio=relogio, **extra)
    chave, xml = nfse_xml(dps_minima(tomador=tomador_pj()))
    dfe.repositorio.armazenar([DocumentoRecebido(xml=xml)])
    dfe.chave_teste = chave
    return dfe


def test_gerar_pdfs_da_fachada_inclui_nfse_com_a_situacao(tmp_path, relogio):
    espiao = GeradorEspiao()
    dfe = criar_dfe(tmp_path, relogio, gerador_pdf=espiao)
    dfe.repositorio.armazenar([DocumentoRecebido(xml=evento_nfse_xml(dfe.chave_teste))])
    resultado = dfe.gerar_pdfs(FiltroConsulta(modelos=["nfse"]), tmp_path / "pdf")
    assert resultado.falhas == {} and [p.name for p in resultado.gerados] == [
        f"{dfe.chave_teste}.pdf"]
    assert espiao.chamadas == [(ModeloDocumento.NFSE, SituacaoDocumento.CANCELADO)]


def test_gerar_pdfs_com_o_gerador_padrao_produz_pdf_real(tmp_path, relogio):
    dfe = criar_dfe(tmp_path, relogio)
    resultado = dfe.gerar_pdfs(FiltroConsulta(modelos=["nfse"]), tmp_path / "pdf")
    assert resultado.falhas == {}
    assert resultado.gerados[0].read_bytes().startswith(b"%PDF")


def test_gerar_danfse_no_servico(tmp_path, relogio, monkeypatch):
    from reportlab.graphics.barcode import qr

    valores = []
    original = qr.QrCodeWidget
    monkeypatch.setattr(qr, "QrCodeWidget", lambda v, **kw: valores.append(v) or original(v, **kw))
    dfe = criar_dfe(tmp_path, relogio, config_nfse=ConfiguracaoNfseLocal(
        url_consulta_publica="https://consulta.exemplo.gov.br/?c={chave}"))
    caminho = dfe.nfse.gerar_danfse(dfe.chave_teste)
    assert caminho == (tmp_path / "homologacao" / CNPJ_PRESTADOR / "nfse" / "danfse"
                       / f"{dfe.chave_teste}.pdf")
    assert caminho.read_bytes().startswith(b"%PDF")
    assert valores == [f"https://consulta.exemplo.gov.br/?c={dfe.chave_teste}"]
    personalizado = dfe.nfse.gerar_danfse(dfe.chave_teste, tmp_path / "meu" / "nota.pdf")
    assert personalizado.exists()


def test_gerar_danfse_de_nota_cancelada_usa_a_situacao_atual(tmp_path, relogio):
    espiao = GeradorEspiao()
    dfe = criar_dfe(tmp_path, relogio, gerador_pdf=espiao)
    dfe.repositorio.armazenar([DocumentoRecebido(xml=evento_nfse_xml(dfe.chave_teste))])
    dfe.nfse.gerar_danfse(dfe.chave_teste)
    assert espiao.chamadas == [(ModeloDocumento.NFSE, SituacaoDocumento.CANCELADO)]


def test_gerar_danfse_de_nota_ausente(tmp_path, relogio):
    dfe = criar_dfe(tmp_path, relogio)
    outra, _ = nfse_xml(dps_minima(), numero_nfse=99)
    with pytest.raises(ValidacaoErro, match="consultar_chave"):
        dfe.nfse.gerar_danfse(outra)
