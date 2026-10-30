"""Armazenamento de evidências da NFS-e: caminhos em arquivo idênticos aos de antes e contrato."""

from __future__ import annotations

import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from freedfe.nfse.evidencias import ArmazenamentoEvidencias, ArmazenamentoEvidenciasArquivo

from memoria_nfse import EvidenciasMemoria

ID_DPS = "DPS" + "2927408" + "2" + "11222333000181" + "00900" + "000000000000001"
CHAVE = "2927408" + "2" + "2" + "11222333000181" + "0000000000001" + "2610" + "123456789" + "0"
MES = "2026-10"


@pytest.fixture(params=["arquivo", "memoria"])
def armazenamento(request, tmp_path):
    """``(armazenamento, função que lê a referência devolvida)`` de cada implementação."""
    if request.param == "arquivo":
        return (ArmazenamentoEvidenciasArquivo(tmp_path),
                lambda referencia: (tmp_path / referencia).read_bytes())
    memoria = EvidenciasMemoria()
    return memoria, lambda referencia: memoria.conteudos[referencia]


# ---------------------------------------------------------------------------
# Contrato (vale para qualquer implementação)
# ---------------------------------------------------------------------------

def test_referencia_devolvida_leva_ao_conteudo_gravado(armazenamento):
    evidencias, ler = armazenamento
    assert ler(evidencias.gravar_dps(ID_DPS, MES, 1, "dps.xml", b"<DPS/>")) == b"<DPS/>"
    assert ler(evidencias.gravar_dps(ID_DPS, MES, 1, "resposta.json", b"{}")) == b"{}"
    assert ler(evidencias.gravar_evento(CHAVE, "101101", MES, "pedido.xml", b"<ped/>")) == b"<ped/>"


def test_tentativas_diferentes_da_mesma_dps_geram_entradas_novas(armazenamento):
    evidencias, ler = armazenamento
    primeira = evidencias.gravar_dps(ID_DPS, MES, 1, "dps.xml", b"primeira")
    segunda = evidencias.gravar_dps(ID_DPS, MES, 2, "dps.xml", b"segunda")
    terceira = evidencias.gravar_dps(ID_DPS, MES, 3, "dps.xml", b"terceira")
    assert len({primeira, segunda, terceira}) == 3
    assert [ler(r) for r in (primeira, segunda, terceira)] == [b"primeira", b"segunda", b"terceira"]


def test_eventos_repetidos_nunca_sobrescrevem(armazenamento):
    evidencias, ler = armazenamento
    referencias = [
        evidencias.gravar_evento(CHAVE, "101101", MES, "pedido.xml", f"pedido {n}".encode())
        for n in range(1, 5)]
    assert len(set(referencias)) == 4
    assert [ler(r) for r in referencias] == [f"pedido {n}".encode() for n in range(1, 5)]


def test_pedido_e_resposta_do_evento_sao_entradas_distintas(armazenamento):
    evidencias, ler = armazenamento
    pedido = evidencias.gravar_evento(CHAVE, "101101", MES, "pedido.xml", b"<ped/>")
    resposta = evidencias.gravar_evento(CHAVE, "101101", MES, "resposta.json", b"{}")
    outro_tipo = evidencias.gravar_evento(CHAVE, "105102", MES, "pedido.xml", b"<outro/>")
    assert len({pedido, resposta, outro_tipo}) == 3
    assert (ler(pedido), ler(resposta), ler(outro_tipo)) == (b"<ped/>", b"{}", b"<outro/>")


def test_contrato_e_abstrato():
    with pytest.raises(TypeError, match="abstract"):
        ArmazenamentoEvidencias()
    assert issubclass(ArmazenamentoEvidenciasArquivo, ArmazenamentoEvidencias)
    assert issubclass(EvidenciasMemoria, ArmazenamentoEvidencias)


# ---------------------------------------------------------------------------
# Implementação em arquivo: os mesmos caminhos de antes
# ---------------------------------------------------------------------------

@pytest.fixture
def arquivo(tmp_path):
    return ArmazenamentoEvidenciasArquivo(tmp_path)


def test_dps_primeira_tentativa_sem_numero_e_as_seguintes_com_numero(arquivo, tmp_path):
    base = f"nfse/dps/{MES}/{ID_DPS}"
    assert arquivo.gravar_dps(ID_DPS, MES, 1, "dps.xml", b"a") == f"{base}-dps.xml"
    assert arquivo.gravar_dps(ID_DPS, MES, 1, "resposta.json", b"b") == f"{base}-resposta.json"
    assert arquivo.gravar_dps(ID_DPS, MES, 2, "dps.xml", b"c") == f"{base}-dps-2.xml"
    assert arquivo.gravar_dps(ID_DPS, MES, 2, "resposta.json", b"d") == f"{base}-resposta-2.json"
    assert arquivo.gravar_dps(ID_DPS, MES, 3, "dps.xml", b"e") == f"{base}-dps-3.xml"
    assert sorted(p.name for p in (tmp_path / "nfse" / "dps" / MES).iterdir()) == sorted(
        f"{ID_DPS}-{n}" for n in ("dps.xml", "resposta.json", "dps-2.xml", "resposta-2.json",
                                  "dps-3.xml"))


def test_evento_primeira_gravacao_sem_numero_e_repeticoes_com_2_3(arquivo, tmp_path):
    base = f"nfse/eventos_enviados/{MES}/{CHAVE}-101101"
    assert arquivo.gravar_evento(CHAVE, "101101", MES, "pedido.xml", b"1") == f"{base}-pedido.xml"
    assert arquivo.gravar_evento(CHAVE, "101101", MES, "pedido.xml", b"2") == f"{base}-pedido-2.xml"
    assert arquivo.gravar_evento(CHAVE, "101101", MES, "pedido.xml", b"3") == f"{base}-pedido-3.xml"
    # a resposta tem a sua própria sequência
    assert arquivo.gravar_evento(CHAVE, "101101", MES, "resposta.json", b"r") == \
        f"{base}-resposta.json"
    assert (tmp_path / f"{base}-pedido-2.xml").read_bytes() == b"2"


def test_mes_sem_data_e_aceito_e_o_caminho_devolvido_e_posix(arquivo, tmp_path):
    referencia = arquivo.gravar_dps(ID_DPS, "sem-data", 1, "dps.xml", b"x")
    assert referencia == f"nfse/dps/sem-data/{ID_DPS}-dps.xml"
    assert "\\" not in referencia and not os.path.isabs(referencia)
    assert (tmp_path / referencia).read_bytes() == b"x"


def test_reexecucao_da_mesma_tentativa_substitui_o_conteudo_identico(arquivo, tmp_path):
    arquivo.gravar_dps(ID_DPS, MES, 1, "dps.xml", b"conteudo")
    referencia = arquivo.gravar_dps(ID_DPS, MES, 1, "dps.xml", b"conteudo")
    assert (tmp_path / referencia).read_bytes() == b"conteudo"
    assert len(list((tmp_path / "nfse" / "dps" / MES).iterdir())) == 1


def test_eventos_concorrentes_escolhem_nomes_distintos_sem_perder_nenhum(arquivo, tmp_path):
    barreira = threading.Barrier(8)

    def gravar(indice: int) -> str:
        barreira.wait(timeout=10)
        return arquivo.gravar_evento(CHAVE, "101101", MES, "pedido.xml", f"{indice}".encode())

    with ThreadPoolExecutor(max_workers=8) as pool:
        referencias = list(pool.map(gravar, range(8)))
    assert len(set(referencias)) == 8
    assert sorted((tmp_path / r).read_bytes() for r in referencias) == [
        f"{n}".encode() for n in range(8)]


def test_grava_com_fsync_do_arquivo_e_do_diretorio(arquivo, monkeypatch):
    sincronizados = []
    real = os.fsync

    def espiao(descritor):
        sincronizados.append(os.fstat(descritor).st_mode)
        real(descritor)

    monkeypatch.setattr(os, "fsync", espiao)
    arquivo.gravar_dps(ID_DPS, MES, 1, "dps.xml", b"x")
    assert any(stat.S_ISREG(modo) for modo in sincronizados)  # o conteúdo foi forçado ao disco
    assert any(stat.S_ISDIR(modo) for modo in sincronizados)  # e a entrada de diretório também
