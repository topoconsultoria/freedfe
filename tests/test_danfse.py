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
    formatar_cep,
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


def identificacao(dados, rotulo):
    return next(c.valor for linha in dados.identificacao for c in linha if c.rotulo == rotulo)


# ---------------------------------------------------------------------------
# Formatação e extração (sem reportlab)
# ---------------------------------------------------------------------------

def test_formatadores():
    assert formatar_documento("52998224725") == "529.982.247-25"
    assert formatar_documento("11222333000181") == "11.222.333/0001-81"
    assert formatar_documento("12ABC34501DE35") == "12.ABC.345/01DE-35"
    assert formatar_documento(None) == "" and formatar_documento("abc") == "abc"
    assert formatar_cep("44001000") == "44.001-000" and formatar_cep(None) == ""
    assert formatar_moeda("1234567.5") == "R$ 1.234.567,50" and formatar_moeda(None) == ""
    assert formatar_moeda("x") == ""
    assert formatar_percentual("2.5") == "2,50%" and formatar_percentual("") == ""
    assert formatar_data("2026-10-29T10:05:09-03:00", True) == "29/10/2026 10:05:09"
    assert formatar_data("2026-10-29") == "29/10/2026" and formatar_data("nao-data") == "nao-data"


def test_ordem_dos_blocos_segue_o_anexo_i_da_nt_008():
    dados = extrair_dados(nfse_xml(dps_completa())[1])
    assert [b.titulo for b in dados.blocos] == [
        "PRESTADOR / FORNECEDOR", "TOMADOR / ADQUIRENTE DA OPERAÇÃO", "DESTINATÁRIO DA OPERAÇÃO",
        "INTERMEDIÁRIO DA OPERAÇÃO", "SERVIÇO PRESTADO", "TRIBUTAÇÃO MUNICIPAL (ISSQN)",
        "TRIBUTAÇÃO FEDERAL (EXCETO CBS)", "TRIBUTAÇÃO IBS / CBS", "VALOR TOTAL DA NFS-e",
        "INFORMAÇÕES COMPLEMENTARES"]


def test_identificacao_com_a_chave_em_bloco_unico():
    chave, xml = nfse_xml(dps_minima(), numero_nfse=15)
    dados = extrair_dados(xml)
    assert dados.chave == chave and dados.numero == "15"
    assert identificacao(dados, "CHAVE DE ACESSO DA NFS-E") == chave  # sem grupos de 4
    assert identificacao(dados, "NÚMERO DA NFS-E") == "15"
    assert identificacao(dados, "COMPETÊNCIA DA NFS-E") == "29/10/2026"
    assert identificacao(dados, "DATA E HORA DA EMISSÃO DA NFS-E") == "29/10/2026 10:00:05"
    assert identificacao(dados, "DATA E HORA DA EMISSÃO DA DPS") == "29/10/2026 10:00:00"
    assert identificacao(dados, "SÉRIE DA DPS") == "900" and identificacao(
        dados, "NÚMERO DA DPS") == "6"
    assert identificacao(dados, "EMITENTE DA NFS-E") == "Prestador"
    assert identificacao(dados, "SITUAÇÃO DA NFS-E") == "NFS-e Gerada"
    assert dados.ambiente is Ambiente.HOMOLOGACAO and dados.tipo_ambiente == "Homologação"
    assert dados.municipio == "Salvador" and dados.ambiente_gerador == "Sefin Nacional NFS-e"


@pytest.mark.parametrize("situacao, texto", [
    (SituacaoDocumento.CANCELADO, "NFS-e Cancelada"),
    (SituacaoDocumento.SUBSTITUIDO, "NFS-e Substituída"),
    (SituacaoDocumento.AUTORIZADO, "NFS-e Gerada")])
def test_situacao_na_identificacao(situacao, texto):
    dados = extrair_dados(nfse_xml(dps_minima())[1], situacao)
    assert identificacao(dados, "SITUAÇÃO DA NFS-E") == texto


def test_nfse_minima_suprime_blocos_sem_dados_e_marca_campos_vazios_com_traco():
    dados = extrair_dados(nfse_xml(dps_minima())[1])
    assert [b.mensagem for b in dados.blocos[1:4]] == [
        "TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e",
        "DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e",
        "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"]
    assert all(b.linhas == [] for b in dados.blocos[1:4])
    assert valor(dados, "PRESTADOR", "CNPJ / CPF / NIF") == "11.222.333/0001-81"
    assert valor(dados, "PRESTADOR", "Telefone") == "-"  # nota 12: traço quando vazio
    assert valor(dados, "PRESTADOR", "Código IBGE / CEP") == "2927408 / 40.000-000"
    assert valor(dados, "PRESTADOR", "Simples Nacional na Data de Competência") == (
        "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)")
    assert valor(dados, "SERVIÇO", "Código de Tributação Nacional / Municipal") == "010101"
    assert valor(dados, "TRIBUTAÇÃO MUNICIPAL", "BC ISSQN") == "R$ 1.000,00"
    assert valor(dados, "TRIBUTAÇÃO MUNICIPAL", "Alíquota Aplicada") == "2,00%"
    assert valor(dados, "VALOR TOTAL", "Valor da Operação / Serviço") == "R$ 1.000,00"
    assert valor(dados, "VALOR TOTAL", "Valor Líquido da NFS-e") == "R$ 1.000,00"
    assert all("IBS" not in b.titulo for b in dados.blocos)  # bloco opcional (nota 11)
    complementares = bloco(dados, "INFORMAÇÕES").linhas[0][0].valor
    assert complementares.endswith("Simples Nacional: 6,00%")


def test_dados_da_nfse_completa():
    dados = extrair_dados(nfse_xml(dps_completa())[1])
    assert valor(dados, "TOMADOR", "CNPJ / CPF / NIF") == "11.444.777/0001-61"
    assert valor(dados, "TOMADOR", "Endereço") == "Rua das Flores, 100, Sala 2, Centro"
    assert valor(dados, "DESTINATÁRIO", "Nome / Nome Empresarial") == "DESTINATARIO LTDA"
    assert valor(dados, "INTERMEDIÁRIO", "CNPJ / CPF / NIF") == "529.982.247-25"
    assert valor(dados, "SERVIÇO", "Código da NBS") == "1.1502.20.00"
    assert valor(dados, "SERVIÇO", "Descrição do Serviço") == "Obra de engenharia\nSegunda linha"
    assert valor(dados, "TRIBUTAÇÃO MUNICIPAL", "Retenção do ISSQN") == "Retido pelo Tomador"
    assert valor(dados, "TRIBUTAÇÃO MUNICIPAL", "Benefício Municipal") == "1" * 14
    assert valor(dados, "TRIBUTAÇÃO MUNICIPAL", "Desconto Incondicionado") == "R$ 10,00"
    assert valor(dados, "TRIBUTAÇÃO FEDERAL", "IRRF") == "R$ 21,00"
    assert valor(dados, "VALOR TOTAL", "Valor da Operação / Serviço") == "R$ 1.500,50"
    assert valor(dados, "VALOR TOTAL", "Desconto Condicionado") == "R$ 5,00"
    texto_complementar = bloco(dados, "INFORMAÇÕES").linhas[0][0].valor.split("\n")
    assert texto_complementar[0] == "Contrato 123/2026"
    assert texto_complementar[1].startswith("NFS-e Subst.: 29274082211222333000181")  # nota 7
    assert "Cod. Obra: OB-1; Insc. Imob.: IMOB123" in texto_complementar  # nota 8
    assert "Pedido: PED-9" in texto_complementar
    assert texto_complementar[-1] == (  # nota 10
        "Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: Federais: 13,45%; "
        "Estaduais: 0,00%; Municipais: 2,50%")


def test_bloco_issqn_vira_mensagem_quando_nao_ha_incidencia():
    xml = nfse_xml(dps_completa())[1].replace(b"<tribISSQN>1</tribISSQN>",
                                              b"<tribISSQN>4</tribISSQN>")
    issqn = bloco(extrair_dados(xml), "TRIBUTAÇÃO MUNICIPAL")
    assert issqn.mensagem == "TRIBUTAÇÃO MUNICIPAL (ISSQN) - OPERAÇÃO NÃO SUJEITA AO ISSQN"


def test_destinatario_e_o_proprio_tomador():
    xml = nfse_xml(dps_minima(tomador=tomador_pj()))[1].replace(
        b"<tpEmit>1</tpEmit>", b"<tpEmit>1</tpEmit><indDest>0</indDest>")
    assert bloco(extrair_dados(xml), "DESTINATÁRIO").mensagem == (
        "O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE DA OPERAÇÃO")


def test_pis_cofins_retidos_vao_para_contribuicoes_sociais_retidas():
    base = nfse_xml(dps_completa())[1]
    assert b"<tpRetPisCofins>0</tpRetPisCofins>" in base
    normal = extrair_dados(base)
    assert valor(normal, "TRIBUTAÇÃO FEDERAL", "Contribuições Sociais - Retidas") == "R$ 14,00"
    assert valor(normal, "TRIBUTAÇÃO FEDERAL", "PIS - Débito Apuração Própria") == "R$ 9,10"
    retido = extrair_dados(base.replace(b"<tpRetPisCofins>0</tpRetPisCofins>",
                                        b"<tpRetPisCofins>1</tpRetPisCofins>"))
    # CSLL 14,00 + PIS 9,10 + COFINS 42,02; débito de apuração própria zerado (NT 008).
    assert valor(retido, "TRIBUTAÇÃO FEDERAL", "Contribuições Sociais - Retidas") == "R$ 65,12"
    assert valor(retido, "TRIBUTAÇÃO FEDERAL", "PIS - Débito Apuração Própria") == "R$ 0,00"
    assert valor(retido, "TRIBUTAÇÃO FEDERAL", "COFINS - Débito Apuração Própria") == "R$ 0,00"
    assert valor(retido, "TRIBUTAÇÃO FEDERAL", "Descrição Contrib. Sociais - Retidas") == (
        "PIS/COFINS Retidos")


def test_descricao_das_contribuicoes_so_para_competencia_ate_2026():
    base = nfse_xml(dps_completa())[1]
    futuro = base.replace(b"<dCompet>2026-10-28</dCompet>", b"<dCompet>2027-01-05</dCompet>")
    assert futuro != base
    rotulos = [c.rotulo for linha in bloco(extrair_dados(futuro), "TRIBUTAÇÃO FEDERAL").linhas
               for c in linha]
    assert "Descrição Contrib. Sociais - Retidas" not in rotulos
    assert "Descrição Contrib. Sociais - Retidas" in [
        c.rotulo for linha in bloco(extrair_dados(base), "TRIBUTAÇÃO FEDERAL").linhas
        for c in linha]


def test_bloco_ibscbs_so_aparece_quando_o_xml_o_traz_calculado():
    chave, xml = nfse_xml(dps_minima())
    assert all("IBS" not in b.titulo for b in extrair_dados(xml).blocos)
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
    assert "TRIBUTAÇÃO IBS / CBS" in [b.titulo for b in dados.blocos]
    assert valor(dados, "TRIBUTAÇÃO IBS", "Base de Cálculo") == "R$ 1.000,00"
    assert valor(dados, "TRIBUTAÇÃO IBS", "Alíquota - CBS") == "0,90%"
    assert valor(dados, "TRIBUTAÇÃO IBS", "Valor Apurado Estadual - IBS") == "R$ 1,00"
    assert valor(dados, "TRIBUTAÇÃO IBS", "Valor Total Apurado - CBS") == "R$ 9,00"
    assert valor(dados, "VALOR TOTAL", "Total do IBS/CBS") == "R$ 10,00"
    assert valor(dados, "VALOR TOTAL", "Valor Líquido da NFS-e + IBS/CBS") == "R$ 1.000,00"


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
    assert pdf.startswith(b"%PDF") and b"PRESTADOR / FORNECEDOR" in pdf
    assert b"CANCELADA" not in pdf and b"SUBSTITU" not in pdf


@pytest.mark.parametrize("situacao, marca", [(SituacaoDocumento.CANCELADO, b"CANCELADA"),
                                             (SituacaoDocumento.SUBSTITUIDO, b"SUBSTITU")])
def test_marca_dagua_por_situacao(tmp_path, situacao, marca):
    assert marca in pdf_bruto(tmp_path, nfse_xml()[1], situacao)


def test_cabecalho_e_legenda_do_qr_code_da_nt_008(tmp_path):
    pdf = pdf_bruto(tmp_path, nfse_xml(dps_minima())[1])
    assert b"DANFSe v2.0" in pdf and b"Documento Auxiliar da NFS-e" in pdf
    assert b"A autenticidade desta NFS-e pode ser" in pdf


def test_sem_validade_juridica_so_fora_de_producao(tmp_path):
    xml = nfse_xml(dps_minima())[1]
    assert b"NFS-e SEM VALIDADE JUR" in pdf_bruto(tmp_path, xml)  # tpAmb=2
    producao = xml.replace(b"<tpAmb>2</tpAmb>", b"<tpAmb>1</tpAmb>")
    assert producao != xml
    assert b"SEM VALIDADE" not in pdf_bruto(tmp_path, producao)


def test_bloco_suprimido_mostra_so_a_mensagem(tmp_path):
    pdf = pdf_bruto(tmp_path, nfse_xml(dps_minima())[1])
    assert b"INTERMEDI" in pdf and b"NFS-e)" in pdf  # mensagem do bloco suprimido
    assert pdf.count(b"TOMADOR/ADQUIRENTE DA OPERA") == 1  # sem título duplicado


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
    assert valores == [
        f"https://www.producaorestrita.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}"]
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
