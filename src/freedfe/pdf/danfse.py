"""DANFSe (Documento Auxiliar da NFS-e Nacional) em PDF, com QR Code, gerado localmente.

A API de geração do DANFSe da Sefin foi suspensa (NT 008 v1.02), então o PDF é montado a
partir do XML da NFS-e com ``reportlab`` (BSD): ``pip install 'freedfe[nfse]'``.

O layout segue a **NT 008 v1.02 (DANFSe v2.0)**: A4 retrato, página com borda de 1 ponto e
margens de 3 mm; cabeçalho com "DANFSe v2.0" e "Documento Auxiliar da NFS-e"; identificação
com a chave em bloco único de 50 dígitos; QR Code de 25 mm (mínimo de 1,52 cm) para
``https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave=<chave>`` com a legenda oficial; blocos
na ordem do Anexo I (prestador, tomador, destinatário, intermediário, serviço, ISSQN, tributos
federais, IBS/CBS, valor total e informações complementares); campos sem dado com traço;
"NFS-e SEM VALIDADE JURÍDICA" para ``tpAmb`` 2; marca d'água diagonal em cinza para
CANCELADA e SUBSTITUÍDA.

Diferenças em relação ao modelo oficial: fonte Helvetica no lugar de Arial/Microsoft Sans
Serif (reportlab só embute fontes padrão sem arquivos extras), logomarca da NFS-e omitida
(texto "NFS-e") e posições calculadas pelo fluxo da página, não pelas coordenadas fixas do
Anexo I. O canhoto (opcional) não é gerado. Nomes de município só existem no XML para o
emitente (``xLocEmi``); para as demais partes o DANFSe mostra o código IBGE.
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
from freedfe.nfse.texto import sanitizar
from freedfe.pdf.gerador import GeradorPdf
from freedfe.servicos import configuracao_nfse
from freedfe.tipos import Ambiente, ModeloDocumento, SituacaoDocumento
from freedfe.validadores import validar_chave_nfse
from freedfe.xml_utils import achar, nome_local, texto

TAMANHO_QR_MM = 25  # NT 008: mínimo de 1,52 cm
LEGENDA_QR = ("A autenticidade desta NFS-e pode ser verificada pela leitura deste código QR "
              "ou pela consulta da chave de acesso no portal nacional da NFS-e")
SEM_VALIDADE = "NFS-e SEM VALIDADE JURÍDICA"
MARCAS_DAGUA = {SituacaoDocumento.CANCELADO: "CANCELADA",
                SituacaoDocumento.SUBSTITUIDO: "SUBSTITUÍDA"}

OPCAO_SIMPLES = {"1": "Não Optante", "2": "Optante - Microempreendedor Individual (MEI)",
                 "3": "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)"}
REGIME_APURACAO = {"1": "Tributos federais e municipal pelo Simples Nacional",
                   "2": "Tributos federais pelo Simples Nacional e ISSQN pela NFS-e",
                   "3": "Tributos federais e municipal pela NFS-e"}
REGIME_ESPECIAL = {"0": "Nenhum", "1": "Ato Cooperado", "2": "Estimativa",
                   "3": "Microempresa Municipal", "4": "Notário ou Registrador",
                   "5": "Profissional Autônomo", "6": "Sociedade de Profissionais", "9": "Outros"}
TRIBUTACAO_ISSQN = {"1": "Operação tributável", "2": "Imunidade", "3": "Exportação de serviço",
                    "4": "Não incidência"}
RETENCAO_ISSQN = {"1": "Não Retido", "2": "Retido pelo Tomador", "3": "Retido pelo Intermediário"}
TIPO_EMITENTE = {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}
SITUACAO_NFSE = {"100": "NFS-e Gerada", "102": "NFS-e de Decisão Judicial",
                 "103": "NFS-e Avulsa", "107": "NFS-e MEI"}
FINALIDADE = {"0": "NFS-e regular", "1": "NFS-e de crédito", "2": "NFS-e de débito"}
AMBIENTE_GERADOR = {"1": "Sistema próprio do município", "2": "Sefin Nacional NFS-e"}
SUSPENSAO = {"1": "Decisão judicial", "2": "Processo administrativo"}

TRACO = "-"


# ---------------------------------------------------------------------------
# Extração dos dados (sem reportlab)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Campo:
    """Rótulo e valor exibidos; ``peso`` é a largura relativa e ``destaque`` o fundo cinza."""

    rotulo: str
    valor: str
    peso: float = 1.0
    destaque: bool = False


@dataclass(frozen=True)
class Bloco:
    """Seção do DANFSe: título e linhas de campos, ou só uma ``mensagem`` (bloco suprimido)."""

    titulo: str
    linhas: list[list[Campo]] = field(default_factory=list)
    mensagem: str | None = None


@dataclass(frozen=True)
class DadosDanfse:
    """Tudo o que o PDF precisa, já formatado para exibição."""

    chave: str
    numero: str
    emissao: str
    competencia: str
    serie_numero_dps: str
    ambiente: Ambiente
    municipio: str
    ambiente_gerador: str
    tipo_ambiente: str
    identificacao: list[list[Campo]]
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


def formatar_cep(valor: str | None) -> str:
    """``44001000`` -> ``44.001-000``."""
    if valor and len(valor) == 8 and valor.isdigit():
        return f"{valor[:2]}.{valor[2:5]}-{valor[5:]}"
    return valor or ""


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


def _c(rotulo: str, valor: str | None, peso: float = 1.0, destaque: bool = False) -> Campo:
    """Campo com traço quando o XML não traz informação (NT 008, nota 12)."""
    return Campo(rotulo, valor if valor else TRACO, peso, destaque)


def _filho(no: ET.Element | None, nome: str) -> ET.Element | None:
    return next((f for f in no if nome_local(f.tag) == nome), None) if no is not None else None


def _vazia(linha: list[Campo]) -> bool:
    return all(c.valor == TRACO for c in linha)


def extrair_dados(xml: bytes, situacao: SituacaoDocumento | None = None) -> DadosDanfse:
    """Interpreta o XML da NFS-e e monta os blocos do DANFSe.

    Args:
        xml: XML da NFS-e (raiz ``NFSe``).
        situacao: situação conhecida localmente (cancelada/substituída altera a "Situação").

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
    emissao_dps = formatar_data(texto(dps, "dhEmi"), True)
    identificacao = _identificacao(inf, dps, chave, situacao)
    blocos = [b for b in (
        _bloco_prestador(inf, dps),
        _bloco_pessoa("TOMADOR / ADQUIRENTE DA OPERAÇÃO", achar(dps, "toma"),
                      "TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"),
        _bloco_destinatario(dps),
        _bloco_pessoa("INTERMEDIÁRIO DA OPERAÇÃO", achar(dps, "interm"),
                      "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"),
        _bloco_servico(inf, dps), _bloco_issqn(inf, dps), _bloco_federal(inf, dps),
        _bloco_ibscbs(inf, dps), _bloco_total(inf, dps),
        _bloco_complementares(inf, dps)) if b is not None]
    return DadosDanfse(
        chave=chave, numero=texto(inf, "nNFSe") or "", emissao=emissao_dps,
        competencia=formatar_data(texto(dps, "dCompet")),
        serie_numero_dps=f"{texto(dps, 'serie') or TRACO} / {texto(dps, 'nDPS') or TRACO}",
        ambiente=ambiente, municipio=_limpo(texto(inf, "xLocEmi")),
        ambiente_gerador=AMBIENTE_GERADOR.get(texto(inf, "ambGer") or "", ""),
        tipo_ambiente="Produção" if ambiente is Ambiente.PRODUCAO else "Homologação",
        identificacao=identificacao, blocos=blocos)


def _identificacao(inf: ET.Element, dps: ET.Element | None, chave: str,
                   situacao: SituacaoDocumento | None) -> list[list[Campo]]:
    if situacao is SituacaoDocumento.CANCELADO:
        situacao_texto = "NFS-e Cancelada"
    elif situacao is SituacaoDocumento.SUBSTITUIDO:
        situacao_texto = "NFS-e Substituída"
    else:
        situacao_texto = SITUACAO_NFSE.get(texto(inf, "cStat") or "", "")
    finalidade = texto(dps, "finNFSe") or "0"
    return [
        [_c("CHAVE DE ACESSO DA NFS-E", chave, 4)],
        [_c("NÚMERO DA NFS-E", texto(inf, "nNFSe")),
         _c("COMPETÊNCIA DA NFS-E", formatar_data(texto(dps, "dCompet"))),
         _c("DATA E HORA DA EMISSÃO DA NFS-E", formatar_data(texto(inf, "dhProc"), True))],
        [_c("NÚMERO DA DPS", texto(dps, "nDPS")), _c("SÉRIE DA DPS", texto(dps, "serie")),
         _c("DATA E HORA DA EMISSÃO DA DPS", formatar_data(texto(dps, "dhEmi"), True))],
        [_c("EMITENTE DA NFS-E", TIPO_EMITENTE.get(texto(dps, "tpEmit") or "", ""),
            destaque=True),
         _c("SITUAÇÃO DA NFS-E", situacao_texto),
         _c("FINALIDADE", FINALIDADE.get(finalidade, ""))]]


def _endereco(no: ET.Element | None) -> str:
    if no is None:
        return ""
    partes = [texto(no, "xLgr"), texto(no, "nro"), texto(no, "xCpl"), texto(no, "xBairro")]
    return ", ".join(_limpo(p) for p in partes if p)


def _codigo_cep(no: ET.Element | None, codigo: str | None = None) -> str:
    codigo = codigo or texto(no, "cMun")
    cep = formatar_cep(texto(no, "CEP"))
    return " / ".join(p for p in (codigo, cep) if p)


def _documento(no: ET.Element | None) -> str:
    return (formatar_documento(texto(no, "CNPJ") or texto(no, "CPF"))
            or texto(no, "NIF") or "")


def _bloco_prestador(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    emit = achar(inf, "emit")
    regime = achar(dps, "regTrib")
    ender = achar(emit, "enderNac")
    uf = texto(emit, "UF")
    municipio = " / ".join(p for p in (_limpo(texto(inf, "xLocEmi")), uf) if p)
    return Bloco("PRESTADOR / FORNECEDOR", [
        [_c("CNPJ / CPF / NIF", _documento(emit)), _c("Indicador Municipal (Inscrição)",
                                                     texto(emit, "IM")),
         _c("Telefone", texto(emit, "fone"))],
        [_c("Nome / Nome Empresarial", _limpo(texto(emit, "xNome")), 2),
         _c("Município / Sigla UF", municipio), _c("Código IBGE / CEP", _codigo_cep(ender))],
        [_c("Endereço", _endereco(ender), 2),
         _c("Simples Nacional na Data de Competência",
            OPCAO_SIMPLES.get(texto(regime, "opSimpNac") or "", "")),
         _c("Regime de Apuração Tributária pelo SN",
            REGIME_APURACAO.get(texto(regime, "regApTribSN") or "", ""))]])


def _bloco_pessoa(titulo: str, no: ET.Element | None, ausente: str) -> Bloco:
    """Bloco de parte da operação; sem dados, vira só a ``ausente`` (NT 008, item 2.3.1)."""
    if no is None:
        return Bloco(titulo, mensagem=ausente)
    ender = achar(no, "end")
    return Bloco(titulo, [
        [_c("CNPJ / CPF / NIF", _documento(no)),
         _c("Indicador Municipal (Inscrição)", texto(no, "IM")), _c("Telefone", texto(no, "fone"))],
        [_c("Nome / Nome Empresarial", _limpo(texto(no, "xNome")), 2),
         _c("Município / Sigla UF", ""), _c("Código IBGE / CEP", _codigo_cep(ender))],
        [_c("Endereço", _endereco(ender), 3)]])


def _bloco_destinatario(dps: ET.Element | None) -> Bloco:
    titulo = "DESTINATÁRIO DA OPERAÇÃO"
    destinatario = achar(dps, "dest")
    if destinatario is not None:
        return _bloco_pessoa(titulo, destinatario, "")
    if texto(dps, "indDest") == "0":
        return Bloco(titulo, mensagem="O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE DA OPERAÇÃO")
    return Bloco(titulo, mensagem="DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e")


def _bloco_servico(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    codigos = achar(dps, "cServ")
    nacional = " / ".join(p for p in (texto(codigos, "cTribNac"), texto(codigos, "cTribMun")) if p)
    descricao = " / ".join(p for p in (_limpo(texto(inf, "xTribNac")),
                                      _limpo(texto(inf, "xTribMun"))) if p)
    nbs = texto(codigos, "cNBS") or ""
    if len(nbs) == 9:
        nbs = f"{nbs[0]}.{nbs[1:5]}.{nbs[5:7]}.{nbs[7:]}"
    return Bloco("SERVIÇO PRESTADO", [
        [_c("Código de Tributação Nacional / Municipal", nacional, 2),
         _c("Código da NBS", nbs), _c("Local da Prestação", _limpo(texto(inf, "xLocPrestacao")))],
        [_c("Descrição do Código de Tributação Nacional / Municipal", descricao, 4)],
        [_c("Descrição do Serviço", _limpo(texto(codigos, "xDescServ"), True), 4)]])


def _bloco_issqn(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    municipal = achar(dps, "tribMun")
    valores = achar(inf, "valores")
    titulo = "TRIBUTAÇÃO MUNICIPAL (ISSQN)"
    if texto(municipal, "tribISSQN") == "4":
        return Bloco(titulo, mensagem=f"{titulo} - OPERAÇÃO NÃO SUJEITA AO ISSQN")
    regime = achar(dps, "regTrib")
    beneficio = [_c("Benefício Municipal", texto(municipal, "nBM")),
                 _c("Cálculo do BM", formatar_moeda(texto(valores, "vCalcBM"))),
                 _c("Suspensão da Exigibilidade do ISSQN",
                    SUSPENSAO.get(texto(municipal, "tpSusp") or "", "")),
                 _c("Número Processo Suspensão", texto(municipal, "nProcesso"))]
    linhas = [
        [_c("Tipo de Tributação do ISSQN", TRIBUTACAO_ISSQN.get(
            texto(municipal, "tribISSQN") or "", ""), 1.5),
         _c("Município / País da Incidência do ISSQN", _limpo(texto(inf, "xLocIncid")), 1.5),
         _c("Regime Especial de Tributação", REGIME_ESPECIAL.get(
             texto(regime, "regEspTrib") or "", "")),
         _c("Tipo de Imunidade do ISSQN", texto(municipal, "tpImunidade"))],
        beneficio,
        [_c("Total Deduções/Reduções", formatar_moeda(texto(valores, "vCalcDR"))),
         _c("Desconto Incondicionado", formatar_moeda(texto(dps, "vDescIncond"))),
         _c("BC ISSQN", formatar_moeda(texto(valores, "vBC"))),
         _c("Alíquota Aplicada", formatar_percentual(texto(valores, "pAliqAplic")))],
        [_c("Retenção do ISSQN", RETENCAO_ISSQN.get(texto(municipal, "tpRetISSQN") or "", ""), 2),
         _c("ISSQN Apurado", formatar_moeda(texto(valores, "vISSQN")), 2)]]
    if _vazia(beneficio):
        linhas.remove(beneficio)  # nota 5: a linha só aparece se houver dado
    return Bloco(titulo, linhas)


TIPO_RETENCAO_PIS_COFINS = {
    "0": "PIS/COFINS/CSLL Não Retidos", "1": "PIS/COFINS Retidos", "2": "PIS/COFINS Não Retidos",
    "3": "PIS/COFINS/CSLL Retidos", "4": "PIS/COFINS Retidos, CSLL Não Retido",
    "5": "PIS Retido, COFINS/CSLL Não Retido", "6": "COFINS Retido, PIS/CSLL Não Retido",
    "7": "PIS Não Retido, COFINS/CSLL Retidos", "8": "PIS/COFINS Não Retidos, CSLL Retido",
    "9": "COFINS Não Retido, PIS/CSLL Retidos"}


def _soma(*valores: str | None) -> Decimal | None:
    """Soma os valores presentes; ``None`` se nenhum estiver informado."""
    numeros = [n for n in (_decimal(v) for v in valores) if n is not None]
    return sum(numeros, Decimal("0")) if numeros else None


def _moeda(valor: Decimal | None) -> str:
    return "" if valor is None else formatar_moeda(str(valor))


def _bloco_federal(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    """Tributação federal (exceto CBS). Regras de PIS/COFINS retidos da NT 008 (v. campos)."""
    federal = achar(dps, "tribFed")
    pis = achar(federal, "piscofins")
    tipo = texto(pis, "tpRetPisCofins")
    retidos = tipo == "1"  # NT 008: "PIS/COFINS Retido" desloca PIS/COFINS para a coluna de retidos
    csll = texto(federal, "vRetCSLL")
    vpis, vcofins = texto(pis, "vPis"), texto(pis, "vCofins")
    sociais = _soma(csll, vpis, vcofins) if retidos else _decimal(csll)
    apuracao_propria = Decimal("0.00") if retidos and (vpis or vcofins) else None
    linhas = [
        [_c("IRRF", _moeda(_decimal(texto(federal, "vRetIRRF")))),
         _c("Contribuição Previdenciária - Retida", _moeda(_decimal(texto(federal, "vRetCP")))),
         _c("Contribuições Sociais - Retidas", _moeda(sociais))],
        [_c("PIS - Débito Apuração Própria",
            _moeda(apuracao_propria if apuracao_propria is not None else _decimal(vpis))),
         _c("COFINS - Débito Apuração Própria",
            _moeda(apuracao_propria if apuracao_propria is not None else _decimal(vcofins)))]]
    if _competencia_ate_2026(dps):  # nota 6: linha impressa até o fim do ano-calendário de 2026
        linhas[1].append(_c("Descrição Contrib. Sociais - Retidas",
                            TIPO_RETENCAO_PIS_COFINS.get(tipo or "", "")))
    return Bloco("TRIBUTAÇÃO FEDERAL (EXCETO CBS)", linhas)


def _competencia_ate_2026(dps: ET.Element | None) -> bool:
    competencia = texto(dps, "dCompet") or ""
    return not competencia[:4].isdigit() or int(competencia[:4]) <= 2026


def _bloco_ibscbs(inf: ET.Element, dps: ET.Element | None) -> Bloco | None:
    # O grupo calculado pela Sefin é filho direto de infNFSe; o IBSCBS dentro da DPS é o
    # informado pelo emissor (CST, classificação, indicadores) e complementa este bloco.
    calculado = _filho(inf, "IBSCBS")
    informado = achar(dps, "IBSCBS")
    if calculado is None and informado is None:
        return None
    valores = achar(calculado, "valores")
    total = achar(calculado, "totCIBS")
    cst = " / ".join(p for p in (texto(informado, "CST"), texto(informado, "cClassTrib")) if p)
    operacao = " / ".join(p for p in (
        texto(informado, "cIndOp"), texto(calculado, "cLocalidadeIncid"),
        _limpo(texto(calculado, "xLocalidadeIncid"))) if p)
    return Bloco("TRIBUTAÇÃO IBS / CBS", [
        [_c("CST / cClassTrib", cst), _c("Indicador de Operação / Município de Incidência",
                                        operacao, 3)],
        [_c("Base de Cálculo", formatar_moeda(texto(valores, "vBC"))),
         _c("Alíquota - IBS UF", formatar_percentual(texto(valores, "pIBSUF"))),
         _c("Alíquota - IBS Mun.", formatar_percentual(texto(valores, "pIBSMun"))),
         _c("Alíquota - CBS", formatar_percentual(texto(valores, "pCBS")))],
        [_c("Alíq. Efetiva Municipal - IBS",
            formatar_percentual(texto(valores, "pAliqEfetMun"))),
         _c("Valor Apurado Municipal - IBS", formatar_moeda(texto(total, "vIBSMun"))),
         _c("Alíq. Efetiva Estadual - IBS", formatar_percentual(texto(valores, "pAliqEfetUF"))),
         _c("Valor Apurado Estadual - IBS", formatar_moeda(texto(total, "vIBSUF")))],
        [_c("Valor Total Apurado - IBS", formatar_moeda(texto(total, "vIBSTot"))),
         _c("Alíquota Efetiva - CBS", formatar_percentual(texto(valores, "pAliqEfetCBS"))),
         _c("Valor Total Apurado - CBS", formatar_moeda(texto(total, "vCBS")), 2)]])


def _bloco_total(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    valores = achar(inf, "valores")
    total = achar(_filho(inf, "IBSCBS"), "totCIBS")
    return Bloco("VALOR TOTAL DA NFS-e", [
        [_c("Valor da Operação / Serviço", formatar_moeda(texto(dps, "vServ"))),
         _c("Desconto Incondicionado", formatar_moeda(texto(dps, "vDescIncond"))),
         _c("Desconto Condicionado", formatar_moeda(texto(dps, "vDescCond")))],
        [_c("Total das Retenções (ISSQN / Federais)", formatar_moeda(texto(valores, "vTotalRet"))),
         _c("Valor Líquido da NFS-e", formatar_moeda(texto(valores, "vLiq"))),
         _c("Total do IBS/CBS", _moeda(_soma(texto(total, "vIBSTot"), texto(total, "vCBS")))),
         _c("Valor Líquido da NFS-e + IBS/CBS", formatar_moeda(texto(total, "vTotNF")),
            destaque=True)]])


def _totais_aproximados(dps: ET.Element | None) -> str:
    """Nota 10 da NT 008: totais aproximados de tributos (Lei 12.741/2012)."""
    total = achar(dps, "totTrib")
    prefixo = "Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: "
    if achar(total, "vTotTrib") is not None:
        f = formatar_moeda
        return (f"{prefixo}Federais: {f(texto(total, 'vTotTribFed'))}; Estaduais: "
                f"{f(texto(total, 'vTotTribEst'))}; Municipais: {f(texto(total, 'vTotTribMun'))}")
    if achar(total, "pTotTribFed") is not None:
        p = formatar_percentual
        return (f"{prefixo}Federais: {p(texto(total, 'pTotTribFed'))}; Estaduais: "
                f"{p(texto(total, 'pTotTribEst'))}; Municipais: {p(texto(total, 'pTotTribMun'))}")
    if achar(total, "pTotTribSN") is not None:
        return f"{prefixo}Simples Nacional: {formatar_percentual(texto(total, 'pTotTribSN'))}"
    return f"{prefixo}Não informado"


def _bloco_complementares(inf: ET.Element, dps: ET.Element | None) -> Bloco:
    linhas = [_limpo(texto(dps, "xInfComp"), True)]
    if texto(dps, "chSubstda"):
        linhas.append(f"NFS-e Subst.: {texto(dps, 'chSubstda')}")
    obra = [f"{rotulo} {valor}" for rotulo, valor in (
        ("Cod. Obra:", texto(dps, "cObra")), ("Insc. Imob.:", texto(dps, "inscImobFisc"))) if valor]
    if obra:
        linhas.append("; ".join(obra))
    if texto(dps, "idAtvEvt"):
        linhas.append(f"Cod. Evt.: {texto(dps, 'idAtvEvt')}")
    for rotulo, valor in (("Pedido", texto(dps, "xPed")), ("Doc. ref.", texto(dps, "docRef"))):
        if valor:
            linhas.append(f"{rotulo}: {_limpo(valor)}")
    linhas.append(_limpo(texto(inf, "xOutInf"), True))
    linhas.append(_totais_aproximados(dps))
    return Bloco("INFORMAÇÕES COMPLEMENTARES", [
        [Campo("Informações Complementares", "\n".join(item for item in linhas if item))]])


# ---------------------------------------------------------------------------
# Renderização (reportlab)
# ---------------------------------------------------------------------------

def _carregar_reportlab():
    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            BaseDocTemplate,
            Frame,
            KeepTogether,
            PageTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as erro:
        raise DependenciaAusenteErro(
            "O DANFSe requer reportlab: pip install 'freedfe[nfse]'.") from erro
    return {"QrCodeWidget": QrCodeWidget, "Drawing": Drawing, "colors": colors, "A4": A4,
            "ParagraphStyle": ParagraphStyle, "mm": mm, "BaseDocTemplate": BaseDocTemplate,
            "Frame": Frame, "KeepTogether": KeepTogether, "PageTemplate": PageTemplate,
            "Paragraph": Paragraph, "Spacer": Spacer, "Table": Table, "TableStyle": TableStyle}


class GeradorDanfse(GeradorPdf):
    """Gera o DANFSe em PDF a partir do XML da NFS-e (``reportlab``).

    Args:
        url_consulta: função ``chave -> URL`` do QR Code. Padrão: consulta pública nacional do
            ambiente indicado no próprio XML (``tpAmb``).
        comprimir: comprime o PDF (desligue só para inspecionar o conteúdo em testes).
    """

    MARGEM_MM = 3  # X = 0,30 cm no Anexo I da NT 008
    LARGURA_MM = 210 - 2 * MARGEM_MM

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
        situacao = SituacaoDocumento(situacao) if situacao else None
        dados = extrair_dados(xml, situacao)
        url = self._url(dados)
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        marca = MARCAS_DAGUA.get(situacao) if situacao else None

        mm = rl["mm"]
        largura, altura = rl["A4"]
        documento = rl["BaseDocTemplate"](
            str(destino), pagesize=rl["A4"], title=f"DANFSe {dados.chave}", author="freedfe",
            pageCompression=1 if self._comprimir else 0)
        quadro = rl["Frame"](self.MARGEM_MM * mm, self.MARGEM_MM * mm,
                             largura - 2 * self.MARGEM_MM * mm,
                             altura - 2 * self.MARGEM_MM * mm,
                             leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

        def pagina(canvas, doc):
            self._decorar_pagina(rl, canvas, doc, marca)

        documento.addPageTemplates([rl["PageTemplate"](id="danfse", frames=[quadro],
                                                       onPage=pagina)])
        largura_util = self.LARGURA_MM * mm
        elementos = [self._cabecalho(rl, dados, largura_util),
                     self._identificacao(rl, dados, url, largura_util)]
        for bloco in dados.blocos:
            elementos.append(rl["KeepTogether"]([rl["Spacer"](1, 0.5 * mm),
                                                 self._tabela_bloco(rl, bloco, largura_util)]))
        documento.build(elementos)
        return destino

    def _url(self, dados: DadosDanfse) -> str:
        if self._url_consulta is not None:
            return self._url_consulta(dados.chave)
        return configuracao_nfse(dados.ambiente).url_consulta_publica(dados.chave)

    @staticmethod
    def _estilo(rl: dict, nome: str, tamanho: float, negrito: bool = False, **extra):
        return rl["ParagraphStyle"](nome, fontName="Helvetica-Bold" if negrito else "Helvetica",
                                    fontSize=tamanho, leading=tamanho * 1.22, **extra)

    def _cabecalho(self, rl: dict, dados: DadosDanfse, largura: float):
        colors, mm = rl["colors"], rl["mm"]
        centro = [rl["Paragraph"]("DANFSe v2.0", self._estilo(rl, "t1", 9, True, alignment=1)),
                  rl["Paragraph"]("Documento Auxiliar da NFS-e",
                                  self._estilo(rl, "t2", 9, True, alignment=1))]
        if dados.ambiente is Ambiente.HOMOLOGACAO:
            centro.append(rl["Paragraph"](SEM_VALIDADE, self._estilo(
                rl, "t3", 9, True, alignment=1, textColor=colors.Color(1, 0, 0))))
        direita = [rl["Paragraph"](escape(dados.municipio), self._estilo(rl, "d1", 8, alignment=2)),
                   rl["Paragraph"](f"Ambiente gerador: {escape(dados.ambiente_gerador or TRACO)}",
                                   self._estilo(rl, "d2", 6, alignment=2)),
                   rl["Paragraph"](f"Tipo de ambiente: {escape(dados.tipo_ambiente)}",
                                   self._estilo(rl, "d3", 6, alignment=2))]
        esquerda = rl["Paragraph"]("NFS-e", self._estilo(rl, "logo", 14, True))
        tabela = rl["Table"]([[esquerda, centro, direita]],
                             colWidths=[largura * 0.22, largura * 0.50, largura * 0.28])
        tabela.setStyle(rl["TableStyle"]([
            ("BACKGROUND", (0, 0), (-1, -1), colors.Color(0.95, 0.95, 0.95)),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm)]))
        return tabela

    def _identificacao(self, rl: dict, dados: DadosDanfse, url: str, largura: float):
        colors, mm = rl["colors"], rl["mm"]
        largura_qr = 48 * mm
        interno = self._tabela_campos(rl, dados.identificacao, largura - largura_qr,
                                      rotulo_caixa_alta=True)
        qr = rl["QrCodeWidget"](url)
        limites = qr.getBounds()
        lado = TAMANHO_QR_MM * mm
        desenho = rl["Drawing"](lado, lado, transform=[
            lado / (limites[2] - limites[0]), 0, 0, lado / (limites[3] - limites[1]), 0, 0])
        desenho.add(qr)
        legenda = rl["Paragraph"](LEGENDA_QR, self._estilo(rl, "qr", 6, alignment=1))
        celula_qr = rl["Table"]([[desenho], [legenda]], colWidths=[largura_qr - 2 * mm])
        celula_qr.setStyle(rl["TableStyle"]([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        tabela = rl["Table"]([[interno, celula_qr]], colWidths=[largura - largura_qr, largura_qr])
        tabela.setStyle(rl["TableStyle"]([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        return tabela

    def _tabela_campos(self, rl: dict, linhas: list[list[Campo]], largura: float,
                       rotulo_caixa_alta: bool = False, primeira_linha: int = 0):
        """Grade de campos (rótulo 6 pt negrito + conteúdo 7 pt), com larguras por ``peso``."""
        colors = rl["colors"]
        estilo_valor = self._estilo(rl, "valor", 7)
        tamanho_rotulo = 7 if rotulo_caixa_alta else 6
        colunas = max((len(linha) for linha in linhas), default=1)
        referencia = max(linhas, key=len, default=[Campo("", "")])
        total = sum(c.peso for c in referencia)
        larguras = [largura * c.peso / total for c in referencia]
        celulas, estilos = [], [("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("TOPPADDING", (0, 0), (-1, -1), 1.2),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
                                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 2)]
        for indice, linha in enumerate(linhas, start=primeira_linha):
            linha_celulas = []
            for posicao, campo in enumerate(linha):
                valor = escape(campo.valor).replace("\n", "<br/>")
                linha_celulas.append(rl["Paragraph"](
                    f"<font size={tamanho_rotulo}><b>{escape(campo.rotulo)}</b></font><br/>{valor}",
                    estilo_valor))
                if campo.destaque:
                    estilos.append(("BACKGROUND", (posicao, indice), (posicao, indice),
                                    colors.Color(0.95, 0.95, 0.95)))
            linha_celulas += [""] * (colunas - len(linha))
            celulas.append(linha_celulas)
            if len(linha) < colunas:
                estilos.append(("SPAN", (len(linha) - 1, indice - primeira_linha),
                                (colunas - 1, indice - primeira_linha)))
            if indice > primeira_linha:
                estilos.append(("LINEABOVE", (0, indice - primeira_linha),
                                (-1, indice - primeira_linha), 0.5, colors.black))
            estilos.append(("LINEAFTER", (0, indice - primeira_linha),
                            (-2, indice - primeira_linha), 0.5, colors.black))
        tabela = rl["Table"](celulas, colWidths=larguras)
        tabela.setStyle(rl["TableStyle"](
            [(c[0], (c[1][0], c[1][1] - primeira_linha), (c[2][0], c[2][1] - primeira_linha),
              *c[3:]) if c[0] in ("BACKGROUND",) else c for c in estilos]))
        return tabela

    def _tabela_bloco(self, rl: dict, bloco: Bloco, largura: float):
        colors = rl["colors"]
        if bloco.mensagem is not None:  # bloco suprimido: só a mensagem (NT 008, item 2.3.1)
            linha = rl["Paragraph"](f"<b>{escape(bloco.mensagem)}</b>", self._estilo(rl, "msg", 7))
            tabela = rl["Table"]([[linha]], colWidths=[largura])
            tabela.setStyle(rl["TableStyle"]([
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            return tabela
        titulo = rl["Paragraph"](escape(bloco.titulo), self._estilo(rl, "tit", 7, True))
        tabela = rl["Table"]([[titulo], [self._tabela_campos(rl, bloco.linhas, largura)]],
                             colWidths=[largura])
        tabela.setStyle(rl["TableStyle"]([
            ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.95, 0.95, 0.95)),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
            ("LEFTPADDING", (0, 0), (-1, 0), 2), ("TOPPADDING", (0, 0), (-1, 0), 1.2),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 1.2),
            ("LEFTPADDING", (0, 1), (-1, 1), 0), ("RIGHTPADDING", (0, 1), (-1, 1), 0),
            ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 0)]))
        return tabela

    @staticmethod
    def _decorar_pagina(rl: dict, canvas, doc, marca: str | None) -> None:
        mm, colors = rl["mm"], rl["colors"]
        largura, altura = rl["A4"]
        canvas.saveState()
        canvas.setLineWidth(1)  # borda da página: 1 ponto (NT 008, item 2.2.3)
        canvas.rect(1.75 * mm, 1.75 * mm, largura - 3.5 * mm, altura - 3.5 * mm)
        if marca:
            canvas.setFillColor(colors.Color(0.65, 0.65, 0.65, alpha=0.5))  # cinza K35
            canvas.setFont("Helvetica", 70)  # mínimo de 50 pontos, formato normal
            canvas.translate(largura / 2, altura / 2)
            canvas.rotate(55)
            canvas.drawCentredString(0, 0, marca)
        canvas.restoreState()
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.setFont("Helvetica", 5.5)
        canvas.drawString(4 * mm, 2.7 * mm, f"Documento auxiliar. O documento fiscal é o XML. "
                                            f"Gerado por freedfe {__version__}.")
        canvas.drawRightString(largura - 4 * mm, 2.7 * mm, f"Página {doc.page}")
        canvas.restoreState()
