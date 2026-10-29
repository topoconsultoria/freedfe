"""DANFSe (Documento Auxiliar da NFS-e Nacional) em PDF, com QR Code, gerado localmente.

A API de geração do DANFSe da Sefin foi suspensa (NT 008 v1.02), então o PDF é montado a
partir do XML da NFS-e com ``reportlab`` (BSD): ``pip install 'freedfe[nfse]'``.

O documento é auxiliar; o documento fiscal é o XML. O layout é A4 retrato com cabeçalho,
QR Code (25 mm; o mínimo é 1,52 cm) apontando para a consulta pública, blocos de prestador,
tomador, intermediário, serviço, tributação municipal e federal, valores, IBS/CBS (quando
existir no XML), totais aproximados e informações complementares.

HIPÓTESE: o leiaute oficial do DANFSe v2.0 (anexo da NT 008) não estava disponível ao escrever
este módulo; a ordem e os rótulos dos blocos seguem o conteúdo do XML e podem ser ajustados
quando o anexo for conferido. A URL do QR Code vem de ``ConfiguracaoNfse`` (também hipótese).
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.sax.saxutils import escape

from freedfe._versao import __version__
from freedfe.excecoes import DependenciaAusenteErro, ValidacaoErro
from freedfe.nfse.identificadores import formatar_chave
from freedfe.nfse.texto import sanitizar
from freedfe.pdf.gerador import GeradorPdf
from freedfe.servicos import configuracao_nfse
from freedfe.tipos import Ambiente, ModeloDocumento, SituacaoDocumento
from freedfe.validadores import validar_chave_nfse
from freedfe.xml_utils import achar, nome_local, texto

TAMANHO_QR_MM = 25  # mínimo exigido: 1,52 cm
MARCAS_DAGUA = {SituacaoDocumento.CANCELADO: "CANCELADA",
                SituacaoDocumento.SUBSTITUIDO: "SUBSTITUÍDA"}

OPCAO_SIMPLES = {"1": "Não optante", "2": "Optante - MEI", "3": "Optante - ME/EPP"}
REGIME_ESPECIAL = {"0": "Nenhum", "1": "Ato cooperado", "2": "Estimativa",
                   "3": "Microempresa municipal", "4": "Notário ou registrador",
                   "5": "Profissional autônomo", "6": "Sociedade de profissionais", "9": "Outros"}
TRIBUTACAO_ISSQN = {"1": "Operação tributável", "2": "Imunidade", "3": "Exportação de serviço",
                    "4": "Não incidência"}
RETENCAO_ISSQN = {"1": "Não retido", "2": "Retido pelo tomador", "3": "Retido pelo intermediário"}
TIPO_EMITENTE = {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}


# ---------------------------------------------------------------------------
# Extração dos dados (sem reportlab)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Campo:
    """Rótulo e valor exibidos; ``peso`` é a largura relativa na linha."""

    rotulo: str
    valor: str
    peso: float = 1.0


@dataclass(frozen=True)
class Bloco:
    """Seção do DANFSe: título e linhas de campos."""

    titulo: str
    linhas: list[list[Campo]] = field(default_factory=list)


@dataclass(frozen=True)
class DadosDanfse:
    """Tudo o que o PDF precisa, já formatado para exibição."""

    chave: str
    numero: str
    emissao: str
    competencia: str
    serie_numero_dps: str
    ambiente: Ambiente
    blocos: list[Bloco]


def formatar_documento(valor: str | None) -> str:
    """CPF ``000.000.000-00`` ou CNPJ ``00.000.000/0000-00`` (aceita CNPJ alfanumérico)."""
    if not valor:
        return ""
    if len(valor) == 11 and valor.isdigit():
        return f"{valor[:3]}.{valor[3:6]}.{valor[6:9]}-{valor[9:]}"
    if len(valor) == 14:
        return f"{valor[:2]}.{valor[2:5]}.{valor[5:8]}/{valor[8:12]}-{valor[12:]}"
    return valor


def formatar_moeda(valor: str | None) -> str:
    """``1234.5`` -> ``R$ 1.234,50``; vazio se ausente."""
    numero = _decimal(valor)
    if numero is None:
        return ""
    corpo = f"{numero:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    return f"R$ {corpo}"


def formatar_percentual(valor: str | None) -> str:
    """``2.5`` -> ``2,50%``; vazio se ausente."""
    numero = _decimal(valor)
    return "" if numero is None else f"{numero:.2f}".replace(".", ",") + "%"


def formatar_data(valor: str | None, com_hora: bool = False) -> str:
    """ISO 8601 -> ``dd/mm/aaaa`` (ou com ``hh:mm:ss``)."""
    if not valor:
        return ""
    try:
        data = dt.datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return valor
    return data.strftime("%d/%m/%Y %H:%M:%S" if com_hora else "%d/%m/%Y")


def _decimal(valor: str | None) -> Decimal | None:
    try:
        return Decimal(valor) if valor else None
    except InvalidOperation:
        return None


def _limpo(valor: str | None, quebra_linha: bool = False) -> str:
    return sanitizar(valor, quebra_linha=quebra_linha) if valor else ""


def extrair_dados(xml: bytes) -> DadosDanfse:
    """Interpreta o XML da NFS-e e monta os blocos do DANFSe.

    Raises:
        ValidacaoErro: XML que não é uma NFS-e Nacional (sem ``infNFSe`` com Id de 53 posições).
    """
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as erro:
        raise ValidacaoErro(f"XML da NFS-e malformado: {erro}") from erro
    inf = achar(raiz, "infNFSe")
    identificador = inf.attrib.get("Id", "") if inf is not None else ""
    chave = identificador[3:]
    if not (identificador.startswith("NFS") and validar_chave_nfse(chave)):
        raise ValidacaoErro("O XML não é uma NFS-e Nacional (infNFSe sem Id de 53 posições).")
    dps = achar(inf, "infDPS")
    ambiente = Ambiente.PRODUCAO if texto(dps, "tpAmb") == "1" else Ambiente.HOMOLOGACAO
    numero_dps = f"{texto(dps, 'serie') or '-'} / {texto(dps, 'nDPS') or '-'}"
    blocos = [b for b in (
        _bloco_prestador(inf, dps), _bloco_tomador(dps), _bloco_intermediario(dps),
        _bloco_servico(inf, dps), _bloco_trib_municipal(inf, dps), _bloco_trib_federal(dps),
        _bloco_valores(inf, dps), _bloco_totais(dps), _bloco_ibscbs(inf),
        _bloco_complementares(inf, dps)) if b is not None]
    return DadosDanfse(
        chave=chave, numero=texto(inf, "nNFSe") or "",
        emissao=formatar_data(texto(dps, "dhEmi"), True),
        competencia=formatar_data(texto(dps, "dCompet")), serie_numero_dps=numero_dps,
        ambiente=ambiente, blocos=blocos)


def _endereco(no: ET.Element | None, municipio: str | None = None, uf: str | None = None) -> str:
    if no is None:
        return ""
    partes = [texto(no, "xLgr"), texto(no, "nro"), texto(no, "xCpl"), texto(no, "xBairro")]
    logradouro = ", ".join(_limpo(p) for p in partes if p)
    cep = texto(no, "CEP")
    codigo = texto(no, "cMun")
    local = " - ".join(p for p in (municipio or (f"IBGE {codigo}" if codigo else ""), uf) if p)
    return " | ".join(p for p in (logradouro, local, f"CEP {cep}" if cep else "") if p)


def _bloco_prestador(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    emit = achar(inf, "emit")
    regime = achar(dps, "regTrib")
    return Bloco("PRESTADOR DO SERVIÇO", [
        [Campo("Nome / Razão social", _limpo(texto(emit, "xNome")), 3),
         Campo("CNPJ / CPF", formatar_documento(texto(emit, "CNPJ") or texto(emit, "CPF")), 1.2),
         Campo("Inscrição municipal", texto(emit, "IM") or "", 1)],
        [Campo("Endereço", _endereco(achar(emit, "enderNac"), texto(inf, "xLocEmi"),
                                      texto(emit, "UF")), 3),
         Campo("Telefone", texto(emit, "fone") or "", 1.2)],
        [Campo("Simples Nacional", OPCAO_SIMPLES.get(texto(regime, "opSimpNac") or "", ""), 1.5),
         Campo("Regime especial", REGIME_ESPECIAL.get(texto(regime, "regEspTrib") or "", ""), 1.5),
         Campo("Emitente da DPS", TIPO_EMITENTE.get(texto(dps, "tpEmit") or "", ""), 1)]])


def _bloco_pessoa(titulo: str, no: ET.Element | None) -> Bloco | None:
    if no is None:
        return None
    return Bloco(titulo, [
        [Campo("Nome / Razão social", _limpo(texto(no, "xNome")), 3),
         Campo("CNPJ / CPF / NIF", formatar_documento(
             texto(no, "CNPJ") or texto(no, "CPF")) or texto(no, "NIF") or "", 1.2),
         Campo("Inscrição municipal", texto(no, "IM") or "", 1)],
        [Campo("Endereço", _endereco(achar(no, "end")), 3),
         Campo("Telefone", texto(no, "fone") or "", 1.2),
         Campo("E-mail", _limpo(texto(no, "email")), 1.8)]])


def _bloco_tomador(dps: ET.Element | None) -> Bloco:
    bloco = _bloco_pessoa("TOMADOR DO SERVIÇO", achar(dps, "toma"))
    return bloco or Bloco("TOMADOR DO SERVIÇO", [[Campo("Tomador", "NÃO IDENTIFICADO", 1)]])


def _bloco_intermediario(dps: ET.Element | None) -> Bloco | None:
    return _bloco_pessoa("INTERMEDIÁRIO DO SERVIÇO", achar(dps, "interm"))


def _bloco_servico(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    codigos = achar(dps, "cServ")
    return Bloco("SERVIÇO PRESTADO", [
        [Campo("Código de tributação nacional", " - ".join(
            p for p in (texto(codigos, "cTribNac"), _limpo(texto(inf, "xTribNac"))) if p), 3),
         Campo("Código tributação municipal", " - ".join(
             p for p in (texto(codigos, "cTribMun"), _limpo(texto(inf, "xTribMun"))) if p), 1.5)],
        [Campo("NBS", " - ".join(p for p in (texto(codigos, "cNBS"), _limpo(texto(inf, "xNBS")))
                                 if p), 3),
         Campo("Local da prestação", _limpo(texto(inf, "xLocPrestacao")), 1.5),
         Campo("Município de incidência", _limpo(texto(inf, "xLocIncid")), 1.5)],
        [Campo("Descrição do serviço", _limpo(texto(codigos, "xDescServ"), True), 1)]])


def _bloco_trib_municipal(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    municipal = achar(dps, "tribMun")
    valores = achar(inf, "valores")
    linhas = [
        [Campo("Tributação do ISSQN", TRIBUTACAO_ISSQN.get(texto(municipal, "tribISSQN") or "", ""),
               2),
         Campo("Retenção do ISSQN", RETENCAO_ISSQN.get(texto(municipal, "tpRetISSQN") or "", ""),
               2),
         Campo("Base de cálculo", formatar_moeda(texto(valores, "vBC")), 1.5),
         Campo("Alíquota aplicada", formatar_percentual(texto(valores, "pAliqAplic")), 1.2),
         Campo("ISSQN apurado", formatar_moeda(texto(valores, "vISSQN")), 1.5)]]
    extras = []
    if texto(municipal, "nBM"):
        extras.append(Campo("Benefício municipal", texto(municipal, "nBM") or "", 2))
    if texto(municipal, "tpSusp"):
        extras.append(Campo("Exigibilidade suspensa (processo)",
                            texto(municipal, "nProcesso") or "", 2))
    if texto(municipal, "tpImunidade"):
        extras.append(Campo("Tipo de imunidade", texto(municipal, "tpImunidade") or "", 1))
    if extras:
        linhas.append(extras)
    return Bloco("TRIBUTAÇÃO MUNICIPAL (ISSQN)", linhas)


def _bloco_trib_federal(dps: ET.Element | None) -> Bloco | None:
    federal = achar(dps, "tribFed")
    if federal is None:
        return None
    pis = achar(federal, "piscofins")
    return Bloco("TRIBUTAÇÃO FEDERAL", [
        [Campo("CST PIS/COFINS", texto(pis, "CST") or "", 1),
         Campo("Base PIS/COFINS", formatar_moeda(texto(pis, "vBCPisCofins")), 1.3),
         Campo("PIS", formatar_moeda(texto(pis, "vPis")), 1),
         Campo("COFINS", formatar_moeda(texto(pis, "vCofins")), 1)],
        [Campo("Retenção CP", formatar_moeda(texto(federal, "vRetCP")), 1),
         Campo("Retenção IRRF", formatar_moeda(texto(federal, "vRetIRRF")), 1),
         Campo("Retenção CSLL", formatar_moeda(texto(federal, "vRetCSLL")), 1)]])


def _bloco_valores(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    valores = achar(inf, "valores")
    return Bloco("VALORES", [
        [Campo("Valor do serviço", formatar_moeda(texto(dps, "vServ")), 1.3),
         Campo("Desconto incondicionado", formatar_moeda(texto(dps, "vDescIncond")), 1.3),
         Campo("Desconto condicionado", formatar_moeda(texto(dps, "vDescCond")), 1.3),
         Campo("Dedução/redução", formatar_moeda(texto(valores, "vCalcDR")
                                                 or texto(dps, "vDR")), 1.2)],
        [Campo("Total das retenções", formatar_moeda(texto(valores, "vTotalRet")), 1.3),
         Campo("ISSQN retido", formatar_moeda(
             texto(valores, "vISSQN") if texto(dps, "tpRetISSQN") in ("2", "3") else None), 1.3),
         Campo("VALOR LÍQUIDO DA NFS-e", formatar_moeda(texto(valores, "vLiq")), 2.5)]])


def _bloco_totais(dps: ET.Element | None) -> Bloco | None:
    total = achar(dps, "totTrib")
    if total is None:
        return None
    if achar(total, "vTotTrib") is not None:
        campos = [Campo("Federais", formatar_moeda(texto(total, "vTotTribFed"))),
                  Campo("Estaduais", formatar_moeda(texto(total, "vTotTribEst"))),
                  Campo("Municipais", formatar_moeda(texto(total, "vTotTribMun")))]
    elif achar(total, "pTotTrib") is not None:
        campos = [Campo("Federais", formatar_percentual(texto(total, "pTotTribFed"))),
                  Campo("Estaduais", formatar_percentual(texto(total, "pTotTribEst"))),
                  Campo("Municipais", formatar_percentual(texto(total, "pTotTribMun")))]
    elif achar(total, "pTotTribSN") is not None:
        campos = [Campo("Simples Nacional (alíquota efetiva)",
                        formatar_percentual(texto(total, "pTotTribSN")))]
    else:
        campos = [Campo("Total aproximado dos tributos", "Não informado")]
    return Bloco("TOTAIS APROXIMADOS DOS TRIBUTOS", [campos])


def _bloco_ibscbs(inf: ET.Element) -> Bloco | None:
    # O grupo calculado pela Sefin é filho direto de infNFSe; o IBSCBS dentro da DPS é o
    # informado pelo emissor (sem alíquotas nem totais) e não é exibido aqui.
    ibs = next((f for f in inf if nome_local(f.tag) == "IBSCBS"), None)
    if ibs is None:
        return None
    valores = achar(ibs, "valores")
    total = achar(ibs, "totCIBS")
    return Bloco("IBS / CBS (REFORMA TRIBUTÁRIA)", [
        [Campo("Local de incidência", _limpo(texto(ibs, "xLocalidadeIncid")), 2),
         Campo("Base de cálculo", formatar_moeda(texto(valores, "vBC")), 1.3),
         Campo("Alíquota IBS UF", formatar_percentual(texto(valores, "pAliqEfetUF")), 1.2),
         Campo("Alíquota IBS Mun.", formatar_percentual(texto(valores, "pAliqEfetMun")), 1.2),
         Campo("Alíquota CBS", formatar_percentual(texto(valores, "pAliqEfetCBS")), 1.2)],
        [Campo("IBS UF", formatar_moeda(texto(total, "vIBSUF")), 1),
         Campo("IBS Município", formatar_moeda(texto(total, "vIBSMun")), 1),
         Campo("IBS total", formatar_moeda(texto(total, "vIBSTot")), 1),
         Campo("CBS", formatar_moeda(texto(total, "vCBS")), 1),
         Campo("Total da NF (com IBS/CBS)", formatar_moeda(texto(total, "vTotNF")), 1.5)]])


def _bloco_complementares(inf: ET.Element, dps: ET.Element | None) -> Bloco | None:
    campos = [Campo("Informações complementares", _limpo(texto(dps, "xInfComp"), True), 3),
              Campo("Pedido", _limpo(texto(dps, "xPed")), 1),
              Campo("Documento de referência", _limpo(texto(dps, "docRef")), 1.5)]
    outras = _limpo(texto(inf, "xOutInf"), True)
    linhas = [[c for c in campos if c.valor]]
    if outras:
        linhas.append([Campo("Outras informações", outras, 1)])
    linhas = [linha for linha in linhas if linha]
    return Bloco("INFORMAÇÕES COMPLEMENTARES", linhas) if linhas else None


# ---------------------------------------------------------------------------
# Renderização (reportlab)
# ---------------------------------------------------------------------------

def _carregar_reportlab():
    try:
        from reportlab.graphics import renderPDF  # noqa: F401
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            KeepTogether,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as erro:
        raise DependenciaAusenteErro(
            "O DANFSe requer reportlab: pip install 'freedfe[nfse]'.") from erro
    return {"QrCodeWidget": QrCodeWidget, "Drawing": Drawing, "colors": colors, "A4": A4,
            "ParagraphStyle": ParagraphStyle, "mm": mm, "Paragraph": Paragraph,
            "SimpleDocTemplate": SimpleDocTemplate, "Spacer": Spacer,
            "KeepTogether": KeepTogether, "Table": Table, "TableStyle": TableStyle}


class GeradorDanfse(GeradorPdf):
    """Gera o DANFSe em PDF a partir do XML da NFS-e (``reportlab``).

    Args:
        url_consulta: função ``chave -> URL`` do QR Code. Padrão: consulta pública nacional do
            ambiente indicado no próprio XML (``tpAmb``).
        comprimir: comprime o PDF (desligue só para inspecionar o conteúdo em testes).
    """

    def __init__(self, url_consulta: Callable[[str], str] | None = None,
                 comprimir: bool = True):
        self._url_consulta = url_consulta
        self._comprimir = comprimir

    def gerar(self, xml: bytes, modelo: ModeloDocumento, destino: str | Path,
              situacao: SituacaoDocumento | None = None) -> Path:
        """Grava o PDF em ``destino``. ``situacao`` cancelada/substituída aplica marca d'água."""
        if ModeloDocumento.normalizar(modelo) is not ModeloDocumento.NFSE:
            raise ValidacaoErro("GeradorDanfse só gera o DANFSe de NFS-e Nacional.")
        rl = _carregar_reportlab()
        dados = extrair_dados(xml)
        url = self._url(dados)
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        marca = MARCAS_DAGUA.get(SituacaoDocumento(situacao)) if situacao else None

        mm = rl["mm"]
        documento = rl["SimpleDocTemplate"](
            str(destino), pagesize=rl["A4"], leftMargin=10 * mm, rightMargin=10 * mm,
            topMargin=10 * mm, bottomMargin=14 * mm, title=f"DANFSe {dados.chave}",
            author="freedfe", pageCompression=1 if self._comprimir else 0)
        elementos = self._cabecalho(rl, dados, url)
        for bloco in dados.blocos:
            elementos.append(rl["KeepTogether"](
                [rl["Spacer"](1, 2 * mm), self._tabela_bloco(rl, bloco, 190 * mm)]))

        def pagina(canvas, doc):
            self._decorar_pagina(rl, canvas, doc, marca)

        documento.build(elementos, onFirstPage=pagina, onLaterPages=pagina)
        return destino

    def _url(self, dados: DadosDanfse) -> str:
        if self._url_consulta is not None:
            return self._url_consulta(dados.chave)
        return configuracao_nfse(dados.ambiente).url_consulta_publica(dados.chave)

    def _cabecalho(self, rl: dict, dados: DadosDanfse, url: str) -> list:
        mm, colors = rl["mm"], rl["colors"]
        pequeno = rl["ParagraphStyle"]("pequeno", fontName="Helvetica", fontSize=7, leading=9)
        titulo = rl["ParagraphStyle"]("titulo", fontName="Helvetica-Bold", fontSize=13, leading=16)
        normal = rl["ParagraphStyle"]("normal", fontName="Helvetica", fontSize=8.5, leading=11)
        aviso = rl["ParagraphStyle"]("aviso", fontName="Helvetica-Bold", fontSize=8, leading=10,
                                     textColor=colors.HexColor("#b00020"))
        esquerda = [
            rl["Paragraph"]("DANFSe", titulo),
            rl["Paragraph"]("Documento Auxiliar da Nota Fiscal de Serviço Eletrônica", pequeno),
            rl["Paragraph"](f"<b>NFS-e nº {escape(dados.numero)}</b> &nbsp; DPS (série/nº): "
                            f"{escape(dados.serie_numero_dps)}", normal),
            rl["Paragraph"](f"Emissão: {escape(dados.emissao)} &nbsp; Competência: "
                            f"{escape(dados.competencia)}", normal),
            rl["Paragraph"](f"<b>Chave de acesso</b><br/>{escape(formatar_chave(dados.chave))}",
                            normal)]
        if dados.ambiente is Ambiente.HOMOLOGACAO:
            esquerda.append(rl["Paragraph"]("PRODUÇÃO RESTRITA - SEM VALIDADE JURÍDICA", aviso))
        qr = rl["QrCodeWidget"](url)
        limites = qr.getBounds()
        lado = TAMANHO_QR_MM * mm
        desenho = rl["Drawing"](lado, lado, transform=[
            lado / (limites[2] - limites[0]), 0, 0, lado / (limites[3] - limites[1]), 0, 0])
        desenho.add(qr)
        direita = [desenho, rl["Paragraph"]("Consulte a autenticidade pelo QR Code ou em "
                                            "www.nfse.gov.br/ConsultaPublica", pequeno)]
        tabela = rl["Table"]([[esquerda, direita]], colWidths=[150 * mm, 40 * mm])
        tabela.setStyle(rl["TableStyle"]([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.8, colors.black),
            ("LINEAFTER", (0, 0), (0, 0), 0.5, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
        return [tabela]

    def _tabela_bloco(self, rl: dict, bloco: Bloco, largura: float):
        colors = rl["colors"]
        estilo_valor = rl["ParagraphStyle"]("valor", fontName="Helvetica", fontSize=8.5,
                                            leading=10.5)
        titulo = rl["ParagraphStyle"]("tituloBloco", fontName="Helvetica-Bold", fontSize=8,
                                      leading=10)
        linhas = [[rl["Paragraph"](escape(bloco.titulo), titulo)]]
        estilos = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
                   ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                   ("SPAN", (0, 0), (-1, 0)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                   ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
        colunas = max((len(linha) for linha in bloco.linhas), default=1)
        for indice, linha in enumerate(bloco.linhas, start=1):
            celulas = [rl["Paragraph"](
                f"<font size=6 color='#555555'>{escape(c.rotulo.upper())}</font><br/>"
                f"{escape(c.valor).replace(chr(10), '<br/>') or '&nbsp;'}", estilo_valor)
                for c in linha]
            celulas += [""] * (colunas - len(celulas))
            linhas.append(celulas)
            if len(linha) < colunas:
                estilos.append(("SPAN", (len(linha) - 1, indice), (colunas - 1, indice)))
            estilos.append(("LINEABOVE", (0, indice), (-1, indice), 0.25, colors.lightgrey))
            estilos.append(("LINEAFTER", (0, indice), (-2, indice), 0.25, colors.lightgrey))
        # Larguras proporcionais aos pesos da linha mais cheia.
        referencia = max(bloco.linhas, key=len, default=[Campo("", "")])
        total = sum(c.peso for c in referencia)
        larguras = [largura * c.peso / total for c in referencia]
        tabela = rl["Table"](linhas, colWidths=larguras)
        tabela.setStyle(rl["TableStyle"](estilos))
        return tabela

    @staticmethod
    def _decorar_pagina(rl: dict, canvas, doc, marca: str | None) -> None:
        mm, colors = rl["mm"], rl["colors"]
        largura, altura = rl["A4"]
        canvas.saveState()
        if marca:
            canvas.setFillColor(colors.Color(0.8, 0.1, 0.1, alpha=0.18))
            canvas.setFont("Helvetica-Bold", 90)
            canvas.translate(largura / 2, altura / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, marca)
            canvas.rotate(-45)
            canvas.translate(-largura / 2, -altura / 2)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(10 * mm, 8 * mm, f"Documento auxiliar da NFS-e. O documento fiscal é o "
                                           f"XML. Gerado por freedfe {__version__}.")
        canvas.drawRightString(largura - 10 * mm, 8 * mm, f"Página {doc.page}")
        canvas.restoreState()
