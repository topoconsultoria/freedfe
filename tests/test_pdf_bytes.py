"""PDF em memória: ``gerar_bytes`` do contrato (diretório temporário) e do DANFSe (sem disco)."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from freedfe import SituacaoDocumento
from freedfe.excecoes import ValidacaoErro
from freedfe.pdf.danfse import GeradorDanfse
from freedfe.pdf.gerador import GeradorPdf, GeradorPdfBrazilFiscalReport, GeradorPdfPadrao
from freedfe.tipos import ModeloDocumento

from fabrica_nfse import dps_completa, dps_minima, nfse_xml
from fabrica_xml import CNPJ_FORNECEDOR, gerar_chave, nfe_proc

NFSE = ModeloDocumento.NFSE


def paginas(pdf: bytes) -> int:
    return pdf.count(b"/Type /Page\n")


def textos(pdf: bytes) -> set[bytes]:
    """Textos literais do PDF sem compressão, descartando as datas dos metadados."""
    literais = re.findall(rb"\(((?:\\.|[^\\()])*)\)", pdf)
    return {texto for texto in literais if not texto.startswith(b"D:")}


# ---------------------------------------------------------------------------
# GeradorDanfse: núcleo comum e geração sem disco
# ---------------------------------------------------------------------------

def test_gerar_bytes_do_danfse_equivale_ao_arquivo_gerado(tmp_path):
    pytest.importorskip("reportlab")
    xml = nfse_xml(dps_completa())[1]
    gerador = GeradorDanfse(comprimir=False)  # sem compressão, o texto fica legível
    em_arquivo = gerador.gerar(xml, NFSE, tmp_path / "a.pdf").read_bytes()
    em_memoria = gerador.gerar_bytes(xml, NFSE)
    assert em_memoria.startswith(b"%PDF") and em_memoria.rstrip().endswith(b"%%EOF")
    assert paginas(em_memoria) == paginas(em_arquivo) >= 1
    textos_memoria = textos(em_memoria)
    assert textos_memoria == textos(em_arquivo)
    assert b"PRESTADOR / FORNECEDOR" in textos_memoria and len(textos_memoria) > 20


def test_gerar_bytes_comprimido_e_um_pdf_valido_e_menor(tmp_path):
    pytest.importorskip("reportlab")
    xml = nfse_xml(dps_completa())[1]
    comprimido = GeradorDanfse().gerar_bytes(xml, NFSE)
    legivel = GeradorDanfse(comprimir=False).gerar_bytes(xml, NFSE)
    assert comprimido.startswith(b"%PDF") and len(comprimido) < len(legivel)


@pytest.mark.parametrize("situacao, marca", [(SituacaoDocumento.CANCELADO, b"CANCELADA"),
                                             (SituacaoDocumento.SUBSTITUIDO, b"SUBSTITU")])
def test_gerar_bytes_aplica_a_marca_dagua_da_situacao(situacao, marca):
    pytest.importorskip("reportlab")
    xml = nfse_xml(dps_minima())[1]
    assert marca in GeradorDanfse(comprimir=False).gerar_bytes(xml, NFSE, situacao)
    assert marca not in GeradorDanfse(comprimir=False).gerar_bytes(xml, NFSE)


def test_gerar_bytes_nao_toca_o_disco(tmp_path, monkeypatch):
    pytest.importorskip("reportlab")
    monkeypatch.chdir(tmp_path)
    vazio = tmp_path / "tmp"
    vazio.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(vazio))

    def proibido(*args, **kwargs):
        raise AssertionError("gerar_bytes do DANFSe não pode passar por arquivo")

    monkeypatch.setattr(GeradorDanfse, "gerar", proibido)
    monkeypatch.setattr(tempfile, "mkdtemp", proibido)
    pdf = GeradorDanfse().gerar_bytes(nfse_xml(dps_minima())[1], NFSE)
    assert pdf.startswith(b"%PDF")
    assert [p.name for p in tmp_path.iterdir()] == ["tmp"] and list(vazio.iterdir()) == []


def test_gerar_bytes_so_gera_danfse_para_nfse():
    with pytest.raises(ValidacaoErro, match="NFS-e"):
        GeradorDanfse().gerar_bytes(nfe_proc(gerar_chave("55", CNPJ_FORNECEDOR, 1)),
                                    ModeloDocumento.NFE)


def test_gerar_continua_criando_a_pasta_so_depois_de_validar_o_xml(tmp_path):
    pytest.importorskip("reportlab")
    with pytest.raises(ValidacaoErro):
        GeradorDanfse().gerar(b"<nao-e-nfse/>", NFSE, tmp_path / "nova" / "x.pdf")
    assert not (tmp_path / "nova").exists()


# ---------------------------------------------------------------------------
# GeradorPdf.gerar_bytes: padrão com diretório temporário
# ---------------------------------------------------------------------------

class GeradorDeArquivo(GeradorPdf):
    """Gera um PDF de mentira e registra onde e como foi chamado."""

    def __init__(self):
        self.chamadas: list[tuple[Path, dict]] = []

    def gerar(self, xml, modelo, destino, situacao=None):
        destino = Path(destino)
        self.chamadas.append((destino, {"situacao": situacao}))
        destino.write_bytes(b"%PDF-falso " + xml)
        return destino


def test_gerar_bytes_padrao_usa_diretorio_temporario_e_o_remove():
    gerador = GeradorDeArquivo()
    pdf = gerador.gerar_bytes(b"<x/>", ModeloDocumento.NFE)
    assert pdf == b"%PDF-falso <x/>"
    [(destino, _)] = gerador.chamadas
    assert destino.name == "documento.pdf" and not destino.exists()
    assert not destino.parent.exists()  # o diretório temporário não fica para trás


def test_gerar_bytes_padrao_repassa_a_situacao_so_quando_informada():
    gerador = GeradorDeArquivo()
    gerador.gerar_bytes(b"<x/>", NFSE, SituacaoDocumento.CANCELADO)
    gerador.gerar_bytes(b"<x/>", NFSE)
    assert [argumentos["situacao"] for _, argumentos in gerador.chamadas] == [
        SituacaoDocumento.CANCELADO, None]


def test_gerar_bytes_padrao_aceita_gerador_antigo_sem_o_parametro_situacao():
    class GeradorAntigo(GeradorPdf):
        def gerar(self, xml, modelo, destino):  # assinatura anterior à NFS-e
            Path(destino).write_bytes(b"%PDF-antigo")
            return Path(destino)

    assert GeradorAntigo().gerar_bytes(b"<x/>", ModeloDocumento.NFE) == b"%PDF-antigo"


def test_gerar_bytes_padrao_remove_o_temporario_mesmo_com_erro():
    class GeradorQueFalha(GeradorDeArquivo):
        def gerar(self, xml, modelo, destino, situacao=None):
            self.chamadas.append((Path(destino), {}))
            raise RuntimeError("layout inesperado")

    gerador = GeradorQueFalha()
    with pytest.raises(RuntimeError):
        gerador.gerar_bytes(b"<x/>", ModeloDocumento.NFE)
    assert not gerador.chamadas[0][0].parent.exists()


# ---------------------------------------------------------------------------
# GeradorPdfPadrao e BrazilFiscalReport
# ---------------------------------------------------------------------------

class GeradorDeBytes(GeradorPdf):
    def __init__(self, rotulo: bytes):
        self.rotulo = rotulo
        self.chamadas: list = []

    def gerar(self, xml, modelo, destino, situacao=None):
        raise AssertionError("o caminho em bytes não deve passar por gerar()")

    def gerar_bytes(self, xml, modelo, situacao=None):
        self.chamadas.append((modelo, situacao))
        return self.rotulo + xml


def test_padrao_encaminha_a_nfse_ao_danfse_com_a_situacao():
    danfse, dfe = GeradorDeBytes(b"danfse:"), GeradorDeBytes(b"dfe:")
    gerador = GeradorPdfPadrao(danfse=danfse, documentos_dfe=dfe)
    assert gerador.gerar_bytes(b"<n/>", "nfse", SituacaoDocumento.CANCELADO) == b"danfse:<n/>"
    assert danfse.chamadas == [(NFSE, SituacaoDocumento.CANCELADO)] and dfe.chamadas == []


def test_padrao_encaminha_nfe_cte_mdfe_ao_gerador_dfe():
    danfse, dfe = GeradorDeBytes(b"danfse:"), GeradorDeBytes(b"dfe:")
    gerador = GeradorPdfPadrao(danfse=danfse, documentos_dfe=dfe)
    assert gerador.gerar_bytes(b"<n/>", "nfe") == b"dfe:<n/>"
    assert dfe.chamadas == [(ModeloDocumento.NFE, None)] and danfse.chamadas == []


def test_padrao_usa_o_danfse_local_sem_disco_para_a_nfse(tmp_path, monkeypatch):
    pytest.importorskip("reportlab")
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: pytest.fail("usou disco"))
    pdf = GeradorPdfPadrao().gerar_bytes(nfse_xml(dps_minima())[1], NFSE)
    assert pdf.startswith(b"%PDF")


def test_brazilfiscalreport_gera_bytes_pelo_padrao_com_diretorio_temporario():
    pytest.importorskip("brazilfiscalreport")
    xml = nfe_proc(gerar_chave("55", CNPJ_FORNECEDOR, 1))
    pdf = GeradorPdfBrazilFiscalReport().gerar_bytes(xml, ModeloDocumento.NFE)
    assert pdf.startswith(b"%PDF")
