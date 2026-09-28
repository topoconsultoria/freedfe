import datetime as dt
from decimal import Decimal

from freedfe.documentos.leitor import LeitorDocumentoFiscal
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento
from freedfe.tipos import ModeloDocumento, SituacaoDocumento

from fabrica_xml import (
    CNPJ_FORNECEDOR,
    CNPJ_INTERESSADO,
    CNPJ_TRANSPORTADORA,
    cte_proc,
    gerar_chave,
    mdfe_proc,
    nfe_proc,
    proc_evento_nfe,
    res_nfe,
)

leitor = LeitorDocumentoFiscal()


def test_nfe_completa():
    chave = gerar_chave("55", CNPJ_FORNECEDOR, 1234)
    r = leitor.ler(nfe_proc(chave))
    assert isinstance(r, ResumoDocumento)
    assert r.chave == chave and r.modelo is ModeloDocumento.NFE and r.xml_completo
    assert r.numero == "1234" and r.serie == "1"
    assert r.emitente_documento == CNPJ_FORNECEDOR
    assert r.destinatario_documento == CNPJ_INTERESSADO
    assert r.valor_total == Decimal("1500.50")
    assert r.data_emissao.date() == dt.date(2026, 7, 15)
    assert r.protocolo == "129260000000001" and r.situacao is SituacaoDocumento.AUTORIZADO


def test_resumo_nfe_usa_interessado_como_destinatario():
    chave = gerar_chave("55", CNPJ_FORNECEDOR, 77, serie=2)
    r = leitor.ler(res_nfe(chave, situacao="2"), interessado=CNPJ_INTERESSADO)
    assert not r.xml_completo and r.tipo_xml == "resNFe"
    assert r.numero == "77" and r.serie == "2"
    assert r.destinatario_documento == CNPJ_INTERESSADO
    assert r.situacao is SituacaoDocumento.DENEGADO


def test_cte_tomador_nos_participantes():
    chave = gerar_chave("57", CNPJ_TRANSPORTADORA, 10)
    r = leitor.ler(cte_proc(chave))
    assert r.modelo is ModeloDocumento.CTE and r.numero == "10"
    assert r.valor_total == Decimal("8750.00")
    assert CNPJ_INTERESSADO in r.participantes()


def test_mdfe_contratante_nos_participantes():
    chave = gerar_chave("58", CNPJ_TRANSPORTADORA, 3)
    r = leitor.ler(mdfe_proc(chave))
    assert r.modelo is ModeloDocumento.MDFE and r.destinatario_documento is None
    assert r.valor_total == Decimal("120000.00")
    assert CNPJ_INTERESSADO in r.participantes()


def test_evento():
    chave = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    ev = leitor.ler(proc_evento_nfe(chave))
    assert isinstance(ev, EventoDocumento)
    assert ev.chave == chave and ev.tipo_evento == "110111" and ev.descricao == "Cancelamento"


def test_xml_desconhecido_ou_malformado():
    assert leitor.ler(b"<outro/>") is None
    assert leitor.ler(b"<quebrado") is None


def test_serializacao_ida_e_volta():
    r = leitor.ler(nfe_proc(gerar_chave("55", CNPJ_FORNECEDOR, 5)))
    assert ResumoDocumento.de_dict(r.para_dict()) == r
