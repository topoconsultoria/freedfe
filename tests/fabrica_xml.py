"""Geração de XMLs fiscais sintéticos para os testes (sem dados reais)."""

from __future__ import annotations

import base64
import gzip

from freedfe.validadores import _digito_modulo_11

CNPJ_INTERESSADO = "11222333000181"  # destinatário / titular do certificado
CNPJ_FORNECEDOR = "11444777000161"  # emitente de terceiros
CNPJ_TRANSPORTADORA = "33000167000101"
CPF_PRODUTOR = "52998224725"

NS_NFE = "http://www.portalfiscal.inf.br/nfe"
NS_CTE = "http://www.portalfiscal.inf.br/cte"
NS_MDFE = "http://www.portalfiscal.inf.br/mdfe"


def gerar_chave(modelo: str, cnpj: str, numero: int, aamm: str = "2607", cuf: str = "29",
                serie: int = 1) -> str:
    """Chave de acesso válida (44 posições) para testes."""
    corpo = f"{cuf}{aamm}{cnpj}{modelo}{serie:03d}{numero:09d}1{numero:08d}"
    return corpo + str(_digito_modulo_11(corpo, 9))


def nfe_proc(chave: str, emitente: str = CNPJ_FORNECEDOR, destinatario: str = CNPJ_INTERESSADO,
             dh_emi: str = "2026-07-15T10:30:00-03:00", valor: str = "1500.50") -> bytes:
    """``nfeProc`` com os grupos mínimos para extração e DANFE."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="{NS_NFE}" versao="4.00"><NFe xmlns="{NS_NFE}"><infNFe Id="NFe{chave}" versao="4.00">
<ide><cUF>29</cUF><cNF>{chave[35:43]}</cNF><natOp>VENDA DE INSUMOS</natOp><mod>55</mod>
<serie>{int(chave[22:25])}</serie><nNF>{int(chave[25:34])}</nNF><dhEmi>{dh_emi}</dhEmi>
<dhSaiEnt>{dh_emi}</dhSaiEnt><tpNF>1</tpNF><idDest>1</idDest><cMunFG>2903201</cMunFG>
<tpImp>1</tpImp><tpEmis>1</tpEmis><cDV>{chave[43]}</cDV><tpAmb>2</tpAmb><finNFe>1</finNFe>
<indFinal>0</indFinal><indPres>1</indPres><procEmi>0</procEmi><verProc>teste</verProc></ide>
<emit><CNPJ>{emitente}</CNPJ><xNome>FORNECEDOR DE INSUMOS LTDA</xNome><xFant>FORNECEDOR</xFant>
<enderEmit><xLgr>RUA A</xLgr><nro>100</nro><xBairro>CENTRO</xBairro><cMun>2903201</cMun>
<xMun>BARREIRAS</xMun><UF>BA</UF><CEP>47800000</CEP><cPais>1058</cPais><xPais>BRASIL</xPais>
<fone>7730000000</fone></enderEmit><IE>123456789</IE><CRT>3</CRT></emit>
<dest><CNPJ>{destinatario}</CNPJ><xNome>FAZENDA EXEMPLO LTDA</xNome>
<enderDest><xLgr>ESTRADA B</xLgr><nro>SN</nro><xBairro>ZONA RURAL</xBairro><cMun>2903201</cMun>
<xMun>BARREIRAS</xMun><UF>BA</UF><CEP>47800000</CEP><cPais>1058</cPais><xPais>BRASIL</xPais>
</enderDest><indIEDest>1</indIEDest><IE>987654321</IE></dest>
<det nItem="1"><prod><cProd>001</cProd><cEAN>SEM GTIN</cEAN><xProd>SEMENTE DE SOJA</xProd>
<NCM>12011000</NCM><CFOP>5101</CFOP><uCom>SC</uCom><qCom>10.0000</qCom><vUnCom>150.0500000000</vUnCom>
<vProd>{valor}</vProd><cEANTrib>SEM GTIN</cEANTrib><uTrib>SC</uTrib><qTrib>10.0000</qTrib>
<vUnTrib>150.0500000000</vUnTrib><indTot>1</indTot></prod>
<imposto><ICMS><ICMS40><orig>0</orig><CST>40</CST></ICMS40></ICMS>
<PIS><PISNT><CST>07</CST></PISNT></PIS><COFINS><COFINSNT><CST>07</CST></COFINSNT></COFINS>
</imposto></det>
<total><ICMSTot><vBC>0.00</vBC><vICMS>0.00</vICMS><vICMSDeson>0.00</vICMSDeson><vFCP>0.00</vFCP>
<vBCST>0.00</vBCST><vST>0.00</vST><vFCPST>0.00</vFCPST><vFCPSTRet>0.00</vFCPSTRet>
<vProd>{valor}</vProd><vFrete>0.00</vFrete><vSeg>0.00</vSeg><vDesc>0.00</vDesc><vII>0.00</vII>
<vIPI>0.00</vIPI><vIPIDevol>0.00</vIPIDevol><vPIS>0.00</vPIS><vCOFINS>0.00</vCOFINS>
<vOutro>0.00</vOutro><vNF>{valor}</vNF></ICMSTot></total>
<transp><modFrete>9</modFrete></transp>
<pag><detPag><tPag>90</tPag><vPag>0.00</vPag></detPag></pag>
<infAdic><infCpl>DOCUMENTO DE TESTE</infCpl></infAdic>
</infNFe></NFe>
<protNFe versao="4.00"><infProt><tpAmb>2</tpAmb><verAplic>teste</verAplic><chNFe>{chave}</chNFe>
<dhRecbto>{dh_emi}</dhRecbto><nProt>129260000000001</nProt><digVal>AAAA</digVal>
<cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe></nfeProc>
""".encode()


def res_nfe(chave: str, emitente: str = CNPJ_FORNECEDOR,
            dh_emi: str = "2026-07-15T10:30:00-03:00", valor: str = "1500.50",
            situacao: str = "1") -> bytes:
    """Resumo de NF-e (entregue ao destinatário antes da manifestação)."""
    return f"""<resNFe xmlns="{NS_NFE}" versao="1.01"><chNFe>{chave}</chNFe><CNPJ>{emitente}</CNPJ>
<xNome>FORNECEDOR DE INSUMOS LTDA</xNome><IE>123456789</IE><dhEmi>{dh_emi}</dhEmi><tpNF>1</tpNF>
<vNF>{valor}</vNF><digVal>AAAA</digVal><dhRecbto>{dh_emi}</dhRecbto><nProt>129260000000001</nProt>
<cSitNFe>{situacao}</cSitNFe></resNFe>""".encode()


def proc_evento_nfe(chave: str, tipo: str = "110111", descricao: str = "Cancelamento",
                    dh_evento: str = "2026-07-16T08:00:00-03:00") -> bytes:
    """``procEventoNFe`` (padrão: cancelamento)."""
    return f"""<procEventoNFe xmlns="{NS_NFE}" versao="1.00"><evento versao="1.00">
<infEvento Id="ID{tipo}{chave}01"><cOrgao>29</cOrgao><tpAmb>2</tpAmb><CNPJ>{CNPJ_FORNECEDOR}</CNPJ>
<chNFe>{chave}</chNFe><dhEvento>{dh_evento}</dhEvento><tpEvento>{tipo}</tpEvento>
<nSeqEvento>1</nSeqEvento><verEvento>1.00</verEvento><detEvento versao="1.00">
<descEvento>{descricao}</descEvento><nProt>129260000000001</nProt><xJust>ERRO DE DIGITACAO NO VALOR</xJust>
</detEvento></infEvento></evento><retEvento versao="1.00"><infEvento><tpAmb>2</tpAmb>
<cStat>135</cStat><chNFe>{chave}</chNFe><tpEvento>{tipo}</tpEvento><nProt>129260000000099</nProt>
</infEvento></retEvento></procEventoNFe>""".encode()


def cte_proc(chave: str, emitente: str = CNPJ_TRANSPORTADORA, tomador: str = CNPJ_INTERESSADO,
             dh_emi: str = "2026-08-01T14:00:00-03:00") -> bytes:
    """``cteProc`` mínimo: fazenda como tomadora (toma4)."""
    return f"""<cteProc xmlns="{NS_CTE}" versao="4.00"><CTe><infCte Id="CTe{chave}" versao="4.00">
<ide><cUF>29</cUF><mod>57</mod><serie>{int(chave[22:25])}</serie><nCT>{int(chave[25:34])}</nCT>
<dhEmi>{dh_emi}</dhEmi><toma4><toma>4</toma><CNPJ>{tomador}</CNPJ><xNome>FAZENDA</xNome></toma4>
</ide><emit><CNPJ>{emitente}</CNPJ><xNome>TRANSPORTADORA GRAOS LTDA</xNome></emit>
<rem><CNPJ>{CNPJ_FORNECEDOR}</CNPJ><xNome>ARMAZEM</xNome></rem>
<dest><CNPJ>{CNPJ_FORNECEDOR}</CNPJ><xNome>TRADING</xNome></dest>
<vPrest><vTPrest>8750.00</vTPrest><vRec>8750.00</vRec></vPrest></infCte></CTe>
<protCTe versao="4.00"><infProt><chCTe>{chave}</chCTe><nProt>329260000000001</nProt>
<cStat>100</cStat></infProt></protCTe></cteProc>""".encode()


def mdfe_proc(chave: str, emitente: str = CNPJ_TRANSPORTADORA,
              contratante: str = CNPJ_INTERESSADO,
              dh_emi: str = "2026-08-02T06:00:00-03:00") -> bytes:
    """``mdfeProc`` mínimo: fazenda como contratante."""
    return f"""<mdfeProc xmlns="{NS_MDFE}" versao="3.00"><MDFe><infMDFe Id="MDFe{chave}" versao="3.00">
<ide><cUF>29</cUF><mod>58</mod><serie>{int(chave[22:25])}</serie><nMDF>{int(chave[25:34])}</nMDF>
<dhEmi>{dh_emi}</dhEmi></ide><emit><CNPJ>{emitente}</CNPJ><xNome>TRANSPORTADORA GRAOS LTDA</xNome></emit>
<infModal><rodo><infANTT><infContratante><CNPJ>{contratante}</CNPJ></infContratante></infANTT></rodo></infModal>
<tot><qCTe>1</qCTe><vCarga>120000.00</vCarga><cUnid>01</cUnid><qCarga>37000.0000</qCarga></tot>
</infMDFe></MDFe><protMDFe versao="3.00"><infProt><chMDFe>{chave}</chMDFe>
<nProt>958260000000001</nProt><cStat>100</cStat></infProt></protMDFe></mdfeProc>""".encode()


def doc_zip(xml: bytes, nsu: int, schema: str) -> str:
    """Elemento ``docZip`` (base64 de gzip)."""
    conteudo = base64.b64encode(gzip.compress(xml)).decode()
    return f'<docZip NSU="{nsu:015d}" schema="{schema}">{conteudo}</docZip>'


def resposta_distribuicao(cstat: str, ult_nsu: int, max_nsu: int, doc_zips: list[str] = (),
                          ns_dados: str = NS_NFE, xmotivo: str = "motivo") -> bytes:
    """Envelope SOAP 1.2 com ``retDistDFeInt``."""
    lote = f"<loteDistDFeInt>{''.join(doc_zips)}</loteDistDFeInt>" if doc_zips else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<nfeDistDFeInteresseResponse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe">
<nfeDistDFeInteresseResult><retDistDFeInt xmlns="{ns_dados}" versao="1.01"><tpAmb>2</tpAmb>
<verAplic>1.0</verAplic><cStat>{cstat}</cStat><xMotivo>{xmotivo}</xMotivo>
<dhResp>2026-09-27T10:00:00-03:00</dhResp><ultNSU>{ult_nsu:015d}</ultNSU><maxNSU>{max_nsu:015d}</maxNSU>
{lote}</retDistDFeInt></nfeDistDFeInteresseResult></nfeDistDFeInteresseResponse>
</soap:Body></soap:Envelope>""".encode()


def resposta_evento(chaves: list[str], cstat: str = "135") -> bytes:
    """Envelope com ``retEnvEvento`` para a manifestação."""
    eventos = "".join(
        f"<retEvento versao=\"1.00\"><infEvento><tpAmb>2</tpAmb><cOrgao>91</cOrgao>"
        f"<cStat>{cstat}</cStat><xMotivo>Evento registrado</xMotivo><chNFe>{ch}</chNFe>"
        f"<tpEvento>210210</tpEvento><nProt>9{i:014d}</nProt>"
        f"<dhRegEvento>2026-09-27T10:00:00-03:00</dhRegEvento></infEvento></retEvento>"
        for i, ch in enumerate(chaves))
    return f"""<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<nfeRecepcaoEventoNFResult xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4">
<retEnvEvento xmlns="{NS_NFE}" versao="1.00"><idLote>1</idLote><tpAmb>2</tpAmb><cOrgao>91</cOrgao>
<cStat>128</cStat><xMotivo>Lote de evento processado</xMotivo>{eventos}</retEnvEvento>
</nfeRecepcaoEventoNFResult></soap:Body></soap:Envelope>""".encode()
