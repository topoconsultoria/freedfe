"""Dados sintéticos de NFS-e Nacional para os testes (sem rede, sem dados reais)."""

from __future__ import annotations

import copy
import datetime as dt
import json
from dataclasses import replace
from decimal import Decimal

from lxml import etree

from freedfe.nfse.envelope import comprimir_b64
from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.nfse.modelo import (
    BeneficioMunicipal,
    Destinatario,
    DiferimentoIbsCbs,
    Dps,
    Endereco,
    ExigibilidadeSuspensa,
    IbsCbs,
    Intermediario,
    Obra,
    OpcaoSimplesNacional,
    PisCofins,
    Prestador,
    RegimeApuracaoSimples,
    RegimeEspecialTributacao,
    RetencaoIssqn,
    Servico,
    Substituicao,
    TipoSuspensao,
    Tomador,
    TotTrib,
    TribFederal,
    TribMunicipal,
    TributacaoRegularIbsCbs,
    Valores,
)
from freedfe.nfse.montador_dps import montador_para
from freedfe.tipos import FUSO_BRASILIA, Ambiente
from freedfe.transporte import RespostaHttp
from freedfe.validadores import _digito_modulo_11
from freedfe.xml_utils import texto

from fabrica_xml import CNPJ_INTERESSADO

MUNICIPIO = "2927408"  # Salvador/BA (código IBGE de 7 dígitos)
MUNICIPIO_TOMADOR = "2910800"  # Feira de Santana/BA
CNPJ_PRESTADOR = CNPJ_INTERESSADO
CNPJ_TOMADOR = "11444777000161"
CPF_TOMADOR = "52998224725"


def gerar_chave_nfse(municipio: str = MUNICIPIO, documento: str = CNPJ_PRESTADOR,
                     numero: int = 1, aamm: str = "2610", codigo: int = 123456789,
                     ambiente_gerador: str = "2") -> str:
    """Chave de 50 posições; o DV usa módulo 11 por hipótese (a validação é estrutural)."""
    tipo = "2" if len(documento) == 14 else "1"
    inscricao = documento.rjust(14, "0")
    corpo = f"{municipio}{ambiente_gerador}{tipo}{inscricao}{numero:013d}{aamm}{codigo:09d}"
    return corpo + str(_digito_modulo_11(corpo, 9))


def resposta_emissao(xml_nfse: bytes, chave: str, status: int = 201,
                     estilo: str = "camel", **extras) -> RespostaHttp:
    """Resposta de sucesso do ``POST /nfse`` (``estilo``: ``camel`` ou ``pascal``)."""
    corpo = {"chaveAcesso": chave, "nfseXmlGZipB64": comprimir_b64(xml_nfse), "alertas": [],
             "tipoAmbiente": 2, "versaoAplicativo": "SefinNacional_1.6.0",
             "dataHoraProcessamento": "2026-10-29T10:00:00-03:00", **extras}
    if estilo == "pascal":
        corpo = {nome[0].upper() + nome[1:]: valor for nome, valor in corpo.items()}
    return RespostaHttp(status, json.dumps(corpo).encode(), {"content-type": "application/json"})


def resposta_erro(codigo: str, descricao: str = "Erro de teste", status: int = 400,
                  estilo: str = "camel", complemento: str = "") -> RespostaHttp:
    """Rejeição de negócio: ``estilo`` ``camel`` (erros/codigo) ou ``pascal`` (erro/Codigo)."""
    if estilo == "pascal":
        corpo = {"erro": [{"Codigo": codigo, "Descricao": descricao, "Complemento": complemento}]}
    else:
        corpo = {"erros": [{"codigo": codigo, "descricao": descricao,
                            "complemento": complemento}]}
    return RespostaHttp(status, json.dumps(corpo).encode(), {"content-type": "application/json"})


# ---------------------------------------------------------------------------
# Modelo da DPS
# ---------------------------------------------------------------------------

DATA_EMISSAO = dt.datetime(2026, 10, 29, 10, 0, 0, tzinfo=FUSO_BRASILIA)


def prestador_simples(**alteracoes) -> Prestador:
    """Prestador ME/EPP optante do Simples (emitente: sem endereço)."""
    dados = dict(cnpj=CNPJ_PRESTADOR, inscricao_municipal="152422",
                 opcao_simples=OpcaoSimplesNacional.ME_EPP,
                 regime_apuracao_simples=RegimeApuracaoSimples.FEDERAIS_E_MUNICIPAL_PELO_SIMPLES)
    dados.update(alteracoes)
    return Prestador(**dados)


def tomador_pj(**alteracoes) -> Tomador:
    dados = dict(cnpj=CNPJ_TOMADOR, nome="EMPRESA TOMADORA DE TESTE LTDA")
    dados.update(alteracoes)
    return Tomador(**dados)


def endereco_tomador() -> Endereco:
    return Endereco.nacional("Rua das Flores", "100", "Centro", MUNICIPIO_TOMADOR, "44001000",
                             complemento="Sala 2")


def dps_minima(**alteracoes) -> Dps:
    """DPS mínima válida: prestador do Simples, sem tomador identificado."""
    dados = dict(
        serie=900, numero=6, data_competencia=dt.date(2026, 10, 29), data_emissao=DATA_EMISSAO,
        codigo_municipio_emissor=MUNICIPIO, prestador=prestador_simples(),
        servico=Servico("010101", "Desenvolvimento de sistema sob encomenda",
                        codigo_municipio_prestacao=MUNICIPIO),
        valores=Valores(Decimal("1000.00")),
        trib_municipal=TribMunicipal(), tot_trib=TotTrib.simples("6.00"))
    dados.update(alteracoes)
    return Dps(**dados)


def servico_com_nbs(**alteracoes) -> Servico:
    dados = dict(codigo_tributacao_nacional="010101",
                 descricao="Desenvolvimento de sistema sob encomenda",
                 codigo_municipio_prestacao=MUNICIPIO, codigo_nbs="115022000")
    dados.update(alteracoes)
    return Servico(**dados)


def ibs_cbs_minimo(**alteracoes) -> IbsCbs:
    dados = dict(codigo_indicador_operacao="100301", indicador_destinatario=0, cst="000",
                 codigo_classificacao="000001")
    dados.update(alteracoes)
    return IbsCbs(**dados)


def ibs_cbs_completo() -> IbsCbs:
    return IbsCbs(
        codigo_indicador_operacao="100301", indicador_destinatario=1, cst="000",
        codigo_classificacao="000001", indicador_consumidor_final=1, tipo_operacao=1,
        ente_governamental=1, notas_referenciadas=(gerar_chave_nfse(numero=5),),
        destinatario=Destinatario(cnpj=CNPJ_TOMADOR, nome="DESTINATARIO LTDA",
                                  endereco=endereco_tomador(), telefone="7133334444",
                                  email="dest@exemplo.com.br"),
        codigo_credito_presumido="01",
        tributacao_regular=TributacaoRegularIbsCbs("000", "000001"),
        diferimento=DiferimentoIbsCbs("10.00", "20.00", "30.00"))


def dps_completa(**alteracoes) -> Dps:
    """DPS com todos os grupos opcionais suportados (emitente prestador, sem endereço)."""
    dados = dict(
        serie=7, numero=1234, data_competencia=dt.date(2026, 10, 28), data_emissao=DATA_EMISSAO,
        codigo_municipio_emissor=MUNICIPIO,
        prestador=Prestador(cnpj=CNPJ_PRESTADOR, inscricao_municipal="152422",
                            opcao_simples=OpcaoSimplesNacional.NAO_OPTANTE,
                            regime_especial=RegimeEspecialTributacao.SOCIEDADE_DE_PROFISSIONAIS,
                            telefone="7133334444", email="fiscal@prestador.com.br"),
        tomador=tomador_pj(inscricao_municipal="9988", endereco=endereco_tomador(),
                           telefone="7199998888", email="tomador@exemplo.com.br"),
        intermediario=Intermediario(cpf=CPF_TOMADOR, nome="Intermediario Pessoa Fisica"),
        servico=servico_com_nbs(
            codigo_tributacao_nacional="070201", descricao="Obra de engenharia\nSegunda linha",
            codigo_municipio_prestacao=MUNICIPIO_TOMADOR, codigo_tributacao_municipal="001",
            codigo_interno="ABC123",
            obra=Obra(codigo_obra="OB-1", inscricao_imobiliaria="IMOB123"),
            informacoes_complementares="Contrato 123/2026", pedido="PED-9",
            documento_referencia="DOC-1", documento_tecnico="DRT-1"),
        valores=Valores("1500.50", recebido="1500.50", desconto_incondicionado="10.00",
                        desconto_condicionado="5.00", deducao_valor="100.00"),
        trib_municipal=TribMunicipal(
            retencao=RetencaoIssqn.RETIDO_PELO_TOMADOR, aliquota="2.50",
            suspensao=ExigibilidadeSuspensa(TipoSuspensao.DECISAO_JUDICIAL, "1" * 30),
            beneficio=BeneficioMunicipal("1" * 14, reducao_percentual="10.00")),
        trib_federal=TribFederal(
            PisCofins("01", base_calculo="1400.50", aliquota_pis="0.65", aliquota_cofins="3.00",
                      valor_pis="9.10", valor_cofins="42.02", tipo_retencao=0),
            retencao_cp="0.00", retencao_irrf="21.00", retencao_csll="14.00"),
        tot_trib=TotTrib.percentuais("13.45", "0.00", "2.50"),
        substituicao=Substituicao(gerar_chave_nfse(numero=3), "01"),
        ibs_cbs=ibs_cbs_completo())
    dados.update(alteracoes)
    return Dps(**dados)


# ---------------------------------------------------------------------------
# XML da NFS-e e de eventos (estrutura do XSD NFSe_v1.01 / evento_v1.01)
# ---------------------------------------------------------------------------

NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"
ASSINATURA_FICTICIA = (
    f'<Signature xmlns="{NS_DSIG}"><SignedInfo>'
    '<CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>'
    '<SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
    '<Reference URI="#ID"><Transforms><Transform Algorithm="http://www.w3.org/2000/09/'
    'xmldsig#enveloped-signature"/></Transforms><DigestMethod Algorithm="http://www.w3.org/'
    '2001/04/xmlenc#sha256"/><DigestValue>AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=</DigestValue>'
    '</Reference></SignedInfo><SignatureValue>AAAA</SignatureValue><KeyInfo><X509Data>'
    '<X509Certificate>AAAA</X509Certificate></X509Data></KeyInfo></Signature>')


def _q(nome: str) -> str:
    return f"{{{NAMESPACE_NFSE}}}{nome}"


def _filho(pai, nome: str, texto=None):
    elemento = etree.SubElement(pai, _q(nome))
    if texto is not None:
        elemento.text = str(texto)
    return elemento


def _assinatura_ficticia(pai) -> None:
    pai.append(etree.fromstring(ASSINATURA_FICTICIA))


def nfse_xml(dps: Dps | None = None, numero_nfse: int = 1, aamm: str = "2610",
             codigo: int = 123456789, leiaute: VersaoLeiaute = VersaoLeiaute.NT004_V2
             ) -> tuple[str, bytes]:
    """NFS-e sintética no leiaute do XSD 1.01, com a DPS real embutida no ``leiaute`` pedido.

    Returns:
        ``(chave, xml)``. A chave usa o município, o emitente e o número informados.
    """
    dps = dps or dps_minima()
    if dps.numero is None:
        dps = replace(dps, numero=numero_nfse)
    relogio = lambda: DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5)  # noqa: E731
    dps_xml = montador_para(leiaute, Ambiente.HOMOLOGACAO, relogio=relogio).montar(dps)
    return nfse_de_dps_xml(dps_xml, numero_nfse, aamm, codigo)


def nfse_de_dps_xml(dps_xml: etree._Element, numero_nfse: int = 1, aamm: str = "2610",
                    codigo: int = 123456789) -> tuple[str, bytes]:
    """Envolve uma DPS (assinada ou não) na NFS-e que a Sefin devolveria, com assinatura fictícia.

    Os dados do emitente, do município e do valor são lidos da própria DPS.
    """
    municipio = texto(dps_xml, "cLocEmi") or MUNICIPIO
    documento = texto(dps_xml, "prest", "CNPJ") or texto(dps_xml, "prest", "CPF") or CNPJ_PRESTADOR
    valor_servico = texto(dps_xml, "vServ") or "0.00"
    chave = gerar_chave_nfse(municipio, documento, numero_nfse, aamm, codigo)
    raiz = etree.Element(_q("NFSe"), nsmap={None: NAMESPACE_NFSE}, versao="1.01")
    inf = etree.SubElement(raiz, _q("infNFSe"), Id=f"NFS{chave}")
    for nome, valor in (("xLocEmi", "Salvador"), ("xLocPrestacao", "Salvador"),
                        ("nNFSe", numero_nfse), ("cLocIncid", municipio),
                        ("xLocIncid", "Salvador"), ("xTribNac", "Analise e desenvolvimento"),
                        ("verAplic", "SefinNac_Pre_1.5.0"), ("ambGer", 2), ("tpEmis", 1),
                        ("procEmi", 1), ("cStat", 100), ("dhProc", "2026-10-29T10:00:05-03:00"),
                        ("nDFSe", 123456)):
        _filho(inf, nome, valor)
    emit = _filho(inf, "emit")
    _filho(emit, "CNPJ", documento)
    _filho(emit, "IM", "152422")
    _filho(emit, "xNome", "EMPRESA PRESTADORA DE TESTE LTDA")
    endereco = _filho(emit, "enderNac")
    for nome, valor in (("xLgr", "RUA DE TESTE"), ("nro", "100"), ("xBairro", "CENTRO"),
                        ("cMun", municipio), ("UF", "BA"), ("CEP", "40000000")):
        _filho(endereco, nome, valor)
    valores = _filho(inf, "valores")
    for nome, valor in (("vBC", valor_servico), ("pAliqAplic", "2.00"),
                        ("vISSQN", "20.00"), ("vLiq", valor_servico)):
        _filho(valores, nome, valor)
    inf.append(copy.deepcopy(dps_xml))
    _assinatura_ficticia(raiz)
    return chave, etree.tostring(raiz, encoding="utf-8")


def evento_nfse_xml(chave: str, tipo: str = "101101", sequencia: int = 1,
                    autor: str = CNPJ_PRESTADOR, motivo: str = "1",
                    dh_evento: str = "2026-10-29T11:00:00-03:00") -> bytes:
    """Evento registrado (raiz ``evento``) com o pedido de cancelamento embutido."""
    raiz = etree.Element(_q("evento"), nsmap={None: NAMESPACE_NFSE}, versao="1.01")
    inf = etree.SubElement(raiz, _q("infEvento"), Id=f"EVT{chave}{tipo}{sequencia:03d}")
    for nome, valor in (("verAplic", "SefinNac_Pre_1.5.0"), ("ambGer", 2),
                        ("nSeqEvento", sequencia), ("dhProc", "2026-10-29T11:00:05-03:00"),
                        ("nDFSe", 654321)):
        _filho(inf, nome, valor)
    pedido = etree.SubElement(inf, _q("pedRegEvento"), versao="1.01")
    info = etree.SubElement(pedido, _q("infPedReg"), Id=f"PRE{chave}{tipo}")
    _filho(info, "tpAmb", 2)
    _filho(info, "verAplic", "freedfe/test")
    _filho(info, "dhEvento", dh_evento)
    _filho(info, "CPFAutor" if len(autor) == 11 else "CNPJAutor", autor)
    _filho(info, "chNFSe", chave)
    descricoes = {"101101": "Cancelamento de NFS-e",
                  "105102": "Cancelamento de NFS-e por Substituicao"}
    evento = _filho(info, f"e{tipo}")
    _filho(evento, "xDesc", descricoes.get(tipo, "Evento de teste"))
    if tipo == "101101":
        _filho(evento, "cMotivo", motivo)
        _filho(evento, "xMotivo", "Cancelamento solicitado pelo prestador")
    _assinatura_ficticia(raiz)
    return etree.tostring(raiz, encoding="utf-8")


def resposta_evento(chave: str, tipo: str = "101101", status: int = 201,
                    com_xml: bool = True) -> RespostaHttp:
    """Resposta do ``POST /nfse/{chave}/eventos`` (com ou sem o XML do evento registrado)."""
    corpo = {"tipoAmbiente": 2, "versaoAplicativo": "SefinNacional_1.6.0",
             "dataHoraProcessamento": "2026-10-29T11:00:05-03:00"}
    if com_xml:
        corpo["eventoXmlGZipB64"] = comprimir_b64(evento_nfse_xml(chave, tipo))
    return RespostaHttp(status, json.dumps(corpo).encode(), {"content-type": "application/json"})


def resposta_lista_eventos(xmls: list[bytes], base64_duplo: bool = True) -> RespostaHttp:
    """Resposta de ``GET /nfse/{chave}/eventos``: ``arquivoXml`` com Base64 duplo (observado)."""
    import base64
    itens = []
    for xml in xmls:
        conteudo = comprimir_b64(xml)
        if base64_duplo:
            conteudo = base64.b64encode(conteudo.encode()).decode()
        itens.append({"arquivoXml": conteudo, "tipoEvento": 101101})
    return RespostaHttp(200, json.dumps({"eventos": itens}).encode(),
                        {"content-type": "application/json"})


def resposta_adn(itens: list[tuple[int, bytes]], status: str = "DOCUMENTOS_LOCALIZADOS",
                 estilo: str = "pascal", base64_duplo: bool = False,
                 http: int = 200) -> RespostaHttp:
    """Lote do ``GET /DFe/{NSU}`` do ADN (``itens``: pares ``(NSU, XML)``)."""
    import base64
    lote = []
    for nsu, xml in itens:
        conteudo = comprimir_b64(xml)
        if base64_duplo:
            conteudo = base64.b64encode(conteudo.encode()).decode()
        lote.append({"NSU": nsu, "ChaveAcesso": _chave_do_xml(xml), "ArquivoXml": conteudo,
                     "TipoDocumento": "NFSE",
                     "DataHoraGeracao": "2026-10-29T10:00:00"})
    # Forma observada no ADN de produção em 29/10/2026 (TipoAmbiente vem como texto).
    corpo = {"StatusProcessamento": status, "LoteDFe": lote, "Alertas": [], "Erros": [],
             "TipoAmbiente": "PRODUCAO", "VersaoAplicativo": "1.0.0.0",
             "DataHoraProcessamento": "2026-10-29T10:00:00.000-03:00"}
    if estilo == "camel":
        corpo = {"statusProcessamento": status, "loteDFe": [
            {chave[0].lower() + chave[1:]: valor for chave, valor in item.items()}
            for item in lote]}
    return RespostaHttp(http, json.dumps(corpo).encode(), {"content-type": "application/json"})


def _chave_do_xml(xml: bytes) -> str | None:
    """Chave do documento (``Id`` sem o prefixo de 3 letras), se houver."""
    elemento = next((e for e in etree.fromstring(xml).iter() if e.get("Id")), None)
    return elemento.get("Id")[3:53] if elemento is not None else None


def resposta_adn_vazia() -> RespostaHttp:
    return resposta_adn([], "NENHUM_DOCUMENTO_LOCALIZADO")
