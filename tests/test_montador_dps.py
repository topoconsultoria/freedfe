import datetime as dt
import os
from pathlib import Path

import pytest
from lxml import etree

from freedfe.assinatura import PERFIL_NFSE, AssinadorXml, verificar_assinatura
from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.identificadores import id_dps
from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.nfse.modelo import TipoEmitente, TotTrib
from freedfe.nfse.montador_dps import MontadorDps, MontadorDpsNt009, montador_para
from freedfe.nfse.validador_xsd import ValidadorXsd
from freedfe.tipos import FUSO_BRASILIA, Ambiente

from conftest import RelogioFalso
from fabrica_nfse import (
    CNPJ_PRESTADOR,
    CNPJ_TOMADOR,
    DATA_EMISSAO,
    MUNICIPIO,
    dps_completa,
    dps_minima,
    ibs_cbs_minimo,
    servico_com_nbs,
    tomador_pj,
)

NS = {"n": NAMESPACE_NFSE}


@pytest.fixture
def relogio_dps():
    """Cinco minutos depois da emissão fixa das fábricas."""
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


@pytest.fixture
def montador(relogio_dps):
    return MontadorDps(Ambiente.HOMOLOGACAO, relogio_dps)


def nomes(elemento) -> list[str]:
    return [etree.QName(filho).localname for filho in elemento]


def test_ordem_dos_elementos_da_dps_minima(montador):
    raiz = montador.montar(dps_minima())
    assert raiz.get("versao") == "1.01" and nomes(raiz) == ["infDPS"]
    assert nomes(raiz[0]) == ["tpAmb", "dhEmi", "verAplic", "serie", "nDPS", "dCompet", "tpEmit",
                              "cLocEmi", "prest", "serv", "valores"]
    assert nomes(raiz[0].find("n:prest", NS)) == ["CNPJ", "IM", "regTrib"]
    assert nomes(raiz[0].find("n:valores", NS)) == ["vServPrest", "trib"]
    assert nomes(raiz[0].find("n:valores/n:trib", NS)) == ["tribMun", "totTrib"]


def test_ordem_dos_elementos_da_dps_completa(montador):
    raiz = montador.montar(dps_completa())
    inf = raiz[0]
    assert nomes(inf) == ["tpAmb", "dhEmi", "verAplic", "serie", "nDPS", "dCompet", "tpEmit",
                          "cLocEmi", "subst", "prest", "toma", "interm", "serv", "valores",
                          "IBSCBS"]
    assert nomes(inf.find("n:toma", NS)) == ["CNPJ", "IM", "xNome", "end", "fone", "email"]
    assert nomes(inf.find("n:toma/n:end", NS)) == ["endNac", "xLgr", "nro", "xCpl", "xBairro"]
    assert nomes(inf.find("n:serv", NS)) == ["locPrest", "cServ", "obra", "infoCompl"]
    assert nomes(inf.find("n:serv/n:cServ", NS)) == ["cTribNac", "cTribMun", "xDescServ", "cNBS",
                                                     "cIntContrib"]
    assert nomes(inf.find("n:serv/n:infoCompl", NS)) == ["idDocTec", "docRef", "xPed",
                                                         "xInfComp"]
    assert nomes(inf.find("n:valores", NS)) == ["vServPrest", "vDescCondIncond", "vDedRed",
                                                "trib"]
    assert nomes(inf.find("n:valores/n:trib/n:tribMun", NS)) == [
        "tribISSQN", "exigSusp", "BM", "tpRetISSQN", "pAliq"]
    assert nomes(inf.find("n:valores/n:trib", NS)) == ["tribMun", "tribFed", "totTrib"]
    assert nomes(inf.find("n:valores/n:trib/n:tribFed", NS)) == [
        "piscofins", "vRetCP", "vRetIRRF", "vRetCSLL"]


def test_id_e_campos_de_identificacao(montador):
    dps = dps_minima()
    raiz = montador.montar(dps)
    inf = raiz[0]
    assert inf.get("Id") == id_dps(MUNICIPIO, CNPJ_PRESTADOR, 900, 6) == montador.identificador(dps)
    assert inf.findtext("n:serie", namespaces=NS) == "900"  # sem zeros à esquerda no elemento
    assert inf.findtext("n:nDPS", namespaces=NS) == "6"
    assert len(inf.findtext("n:verAplic", namespaces=NS)) <= 20


def test_emitente_tomador_usa_o_documento_do_tomador_no_id(montador):
    dps = dps_minima(tipo_emitente=TipoEmitente.TOMADOR, tomador=tomador_pj())
    assert f"2{CNPJ_TOMADOR}" in montador.montar(dps)[0].get("Id")


def test_tpamb_vem_do_ambiente(relogio_dps):
    homologacao = MontadorDps(Ambiente.HOMOLOGACAO, relogio_dps).montar(dps_minima())
    producao = MontadorDps(Ambiente.PRODUCAO, relogio_dps).montar(dps_minima())
    assert homologacao[0].findtext("n:tpAmb", namespaces=NS) == "2"
    assert producao[0].findtext("n:tpAmb", namespaces=NS) == "1"


def test_dhemi_sem_microssegundos_e_no_fuso_de_brasilia(montador):
    utc_com_micro = dt.datetime(2026, 10, 29, 13, 0, 0, 123456, tzinfo=dt.timezone.utc)
    inf = montador.montar(dps_minima(data_emissao=utc_com_micro))[0]
    assert inf.findtext("n:dhEmi", namespaces=NS) == "2026-10-29T10:00:00-03:00"


def test_dhemi_padrao_aplica_folga_ao_relogio(relogio_dps):
    montador = MontadorDps(Ambiente.HOMOLOGACAO, relogio_dps, folga_emissao_segundos=90)
    inf = montador.montar(dps_minima(data_emissao=None))[0]
    esperado = (relogio_dps().astimezone(FUSO_BRASILIA) - dt.timedelta(seconds=90))
    assert inf.findtext("n:dhEmi", namespaces=NS) == esperado.replace(microsecond=0).isoformat()


def test_dhemi_no_futuro_e_competencia_posterior(montador, relogio_dps):
    futuro = relogio_dps().astimezone(FUSO_BRASILIA) + dt.timedelta(minutes=1)
    with pytest.raises(ValidacaoErro, match="E0008"):
        montador.montar(dps_minima(data_emissao=futuro, data_competencia=dt.date(2026, 10, 1)))
    relogio_dps.agora = dt.datetime(2026, 10, 29, 3, 0, tzinfo=dt.timezone.utc)  # 00:00 em BRT
    with pytest.raises(ValidacaoErro, match="competência"):
        montador.montar(dps_minima(data_emissao=None, data_competencia=dt.date(2026, 10, 29)))


def test_numero_ausente_e_erro_no_montador(montador):
    with pytest.raises(ValidacaoErro, match="número"):
        montador.montar(dps_minima(numero=None))


def test_textos_saem_sanitizados_e_escapados(montador):
    servico = servico_com_nbs(descricao="Serviço “especial” – A&B <ok>")
    xml = montador.serializar(dps_minima(servico=servico))
    assert 'Serviço "especial" - A&amp;B &lt;ok&gt;'.encode() in xml
    assert "“".encode() not in xml


@pytest.mark.parametrize("totrib, ramo", [
    (TotTrib.valores("1.00", "2.00", "3.00"), ["vTotTrib"]),
    (TotTrib.percentuais("1.00", "2.00", "3.00"), ["pTotTrib"]),
    (TotTrib.nao_informado(), ["indTotTrib"]),
    (TotTrib.simples("6.00"), ["pTotTribSN"]),
])
def test_tottrib_emite_um_unico_ramo(montador, totrib, ramo):
    assert nomes(montador.montar(dps_minima(tot_trib=totrib))[0].find(
        "n:valores/n:trib/n:totTrib", NS)) == ramo


def test_decimais_com_duas_casas(montador):
    inf = montador.montar(dps_completa())[0]
    assert inf.findtext("n:valores/n:vServPrest/n:vServ", namespaces=NS) == "1500.50"
    assert inf.findtext("n:valores/n:trib/n:tribMun/n:pAliq", namespaces=NS) == "2.50"
    assert inf.findtext("n:valores/n:trib/n:totTrib/n:pTotTrib/n:pTotTribEst",
                        namespaces=NS) == "0.00"


def test_serializacao_compacta_sem_declaracao(montador):
    xml = montador.serializar(dps_minima())
    assert xml.startswith(b'<DPS xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01">')
    assert b"\n" not in xml and b"<?xml" not in xml


class TestIbsCbs:
    def test_nt004_cst_dentro_de_gibscbs_e_dest_dentro_de_ibscbs(self, montador):
        dps = dps_completa()
        ibs = montador.montar(dps)[0].find("n:IBSCBS", NS)
        assert nomes(ibs) == ["finNFSe", "indFinal", "cIndOp", "tpOper", "gRefNFSe",
                              "tpEnteGov", "indDest", "dest", "valores"]
        assert nomes(ibs.find("n:valores", NS)) == ["trib"]
        assert nomes(ibs.find("n:valores/n:trib/n:gIBSCBS", NS)) == [
            "CST", "cClassTrib", "cCredPres", "gTribRegular", "gDif"]
        assert nomes(ibs.find("n:dest", NS)) == ["CNPJ", "xNome", "end", "fone", "email"]

    def test_nt009_cst_antes_de_gibscbs_e_dest_na_raiz(self, relogio_dps):
        montador = MontadorDpsNt009(Ambiente.HOMOLOGACAO, relogio_dps)
        inf = montador.montar(dps_completa())[0]
        topo = nomes(inf)
        assert topo.index("indDest") == topo.index("interm") + 1 and "dest" in topo
        ibs = inf.find("n:IBSCBS", NS)
        assert "indDest" not in nomes(ibs) and "dest" not in nomes(ibs)
        assert nomes(ibs.find("n:valores", NS)) == ["vAjusteBC", "trib"]
        assert nomes(ibs.find("n:valores/n:trib", NS)) == ["CST", "cClassTrib", "gIBSCBS"]
        assert nomes(ibs.find("n:valores/n:trib/n:gIBSCBS", NS)) == [
            "cCredPres", "gTribRegular", "gDif"]

    def test_nt009_omite_gibscbs_sem_detalhe(self, relogio_dps):
        montador = MontadorDpsNt009(Ambiente.HOMOLOGACAO, relogio_dps)
        dps = dps_minima(servico=servico_com_nbs(), ibs_cbs=ibs_cbs_minimo())
        trib = montador.montar(dps)[0].find("n:IBSCBS/n:valores/n:trib", NS)
        assert nomes(trib) == ["CST", "cClassTrib"]
        trib004 = MontadorDps(Ambiente.HOMOLOGACAO, relogio_dps).montar(dps)[0].find(
            "n:IBSCBS/n:valores/n:trib", NS)
        assert nomes(trib004) == ["gIBSCBS"]

    def test_sem_ibscbs_os_dois_leiautes_geram_o_mesmo_xml(self, relogio_dps):
        dps = dps_completa(ibs_cbs=None)
        nt004 = MontadorDps(Ambiente.HOMOLOGACAO, relogio_dps).serializar(dps)
        nt009 = MontadorDpsNt009(Ambiente.HOMOLOGACAO, relogio_dps).serializar(dps)
        assert nt004 == nt009


def test_montador_para_escolhe_a_classe(relogio_dps):
    assert type(montador_para(VersaoLeiaute.NT004_V2, Ambiente.PRODUCAO)) is MontadorDps
    nt009 = montador_para(VersaoLeiaute.NT009_V101, Ambiente.PRODUCAO, relogio=relogio_dps)
    assert isinstance(nt009, MontadorDpsNt009) and nt009.leiaute is VersaoLeiaute.NT009_V101


# ---------------------------------------------------------------------------
# ValidadorXsd (esquema mínimo local)
# ---------------------------------------------------------------------------

XSD_MINIMO = """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           targetNamespace="http://www.sped.fazenda.gov.br/nfse"
           xmlns="http://www.sped.fazenda.gov.br/nfse" elementFormDefault="qualified">
  <xs:element name="DPS"><xs:complexType><xs:sequence>
    <xs:element name="infDPS" type="xs:string"/>
  </xs:sequence></xs:complexType></xs:element>
</xs:schema>"""


def test_validador_xsd_com_esquema_local(tmp_path):
    (tmp_path / "DPS_v0.01.xsd").write_text(XSD_MINIMO)
    (tmp_path / "DPS_v0.02.xsd").write_text(XSD_MINIMO)
    validador = ValidadorXsd(tmp_path)
    assert validador.arquivo.name == "DPS_v0.02.xsd"  # a maior versão
    validador.validar(f'<DPS xmlns="{NAMESPACE_NFSE}"><infDPS>x</infDPS></DPS>'.encode())
    with pytest.raises(ValidacaoErro, match="fora do schema DPS_v0.02.xsd"):
        validador.validar(f'<DPS xmlns="{NAMESPACE_NFSE}"><outro/></DPS>'.encode())
    assert validador.mensagens(etree.fromstring(
        f'<DPS xmlns="{NAMESPACE_NFSE}"><infDPS/><extra/></DPS>'.encode()))


def test_validador_xsd_pasta_ou_arquivo_ausente(tmp_path):
    with pytest.raises(ValidacaoErro, match="inexistente"):
        ValidadorXsd(tmp_path / "nao_existe")
    with pytest.raises(ValidacaoErro, match="Nenhum DPS"):
        ValidadorXsd(tmp_path)
    with pytest.raises(ValidacaoErro, match="não encontrado"):
        ValidadorXsd(tmp_path, "x.xsd")
    (tmp_path / "ruim.xsd").write_text("<nao-e-xsd/>")
    with pytest.raises(ValidacaoErro, match="inválido"):
        ValidadorXsd(tmp_path, "ruim.xsd")


# ---------------------------------------------------------------------------
# Schema oficial (opcional): FREEDFE_XSD_NFSE_NT004 / FREEDFE_XSD_NFSE_NT009
# ---------------------------------------------------------------------------

def _validador(variavel: str) -> ValidadorXsd:
    pasta = os.environ.get(variavel)
    if not pasta:
        pytest.skip(f"defina {variavel} com a pasta dos XSD oficiais")
    return ValidadorXsd(Path(pasta))


@pytest.fixture(scope="module")
def validador_nt004() -> ValidadorXsd:
    """XSD da NT 004 v2.0 (pacote v1.01), em ``FREEDFE_XSD_NFSE_NT004``."""
    return _validador("FREEDFE_XSD_NFSE_NT004")


@pytest.fixture(scope="module")
def validador_nt009() -> ValidadorXsd:
    """XSD da NT 009 v1.01 (pacote PRODREST), em ``FREEDFE_XSD_NFSE_NT009``."""
    return _validador("FREEDFE_XSD_NFSE_NT009")


@pytest.mark.parametrize("fabrica", [dps_minima, dps_completa])
def test_schema_oficial_nt004_dps_valida(validador_nt004, montador, fabrica):
    validador_nt004.validar(montador.montar(fabrica()))


def test_schema_oficial_nt004_dps_assinada_valida(validador_nt004, montador, certificado):
    """A Signature irmã de infDPS não pode causar E1235 (falha de schema)."""
    raiz = AssinadorXml(certificado, PERFIL_NFSE).assinar(montador.montar(dps_completa()))
    reparsed = etree.fromstring(etree.tostring(raiz))
    validador_nt004.validar(reparsed)
    assert verificar_assinatura(reparsed, PERFIL_NFSE)


@pytest.mark.parametrize("totrib", [
    TotTrib.valores("1.00", "2.00", "3.00"), TotTrib.percentuais("1.00", "2.00", "3.00"),
    TotTrib.nao_informado(), TotTrib.simples("6.00")])
def test_schema_oficial_nt004_todos_os_ramos_de_tottrib(validador_nt004, montador, totrib):
    validador_nt004.validar(montador.montar(dps_minima(tot_trib=totrib)))


def test_schema_oficial_nt004_emitente_tomador_e_ibscbs_minimo(validador_nt004, montador):
    dps = dps_minima(tipo_emitente=TipoEmitente.TOMADOR, tomador=tomador_pj(),
                     servico=servico_com_nbs(), ibs_cbs=ibs_cbs_minimo())
    validador_nt004.validar(montador.montar(dps))


@pytest.mark.parametrize("fabrica", [dps_minima, dps_completa])
def test_schema_oficial_nt009_dps_valida(validador_nt009, relogio_dps, fabrica):
    montador = MontadorDpsNt009(Ambiente.HOMOLOGACAO, relogio_dps)
    validador_nt009.validar(montador.montar(fabrica()))
