"""Concorrência na emissão de NFS-e: numeração sem colisão entre threads e instâncias.

Cada thread usa a sua própria instância do estado (ou da ``FreeDFe``) sobre a mesma pasta, como
fariam processos distintos. Quem não obtém a trava de arquivo tenta de novo após uma pequena
espera aleatória, com limite total de 30 s por teste (sem ``sleep`` longo e sem depender de sorte).
"""

from __future__ import annotations

import datetime as dt
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from freedfe import Ambiente, FreeDFe, SituacaoEmissao
from freedfe.certificado import CertificadoA1
from freedfe.distribuicao.trava import TravaArquivo
from freedfe.excecoes import SincronizacaoEmAndamentoErro
from freedfe.nfse.estado_emissao import EstadoEmissaoArquivo
from freedfe.nfse.servico import ConfiguracaoNfseLocal
from servidor_sefin_falso import CAMINHO_SEFIN, ServidorSefinFalso

from conftest import SENHA_TESTE, RelogioFalso
from contrato_estado_emissao import SERIE, montar_id
from fabrica_nfse import CNPJ_PRESTADOR, DATA_EMISSAO, dps_minima

LIMITE_TOTAL_SEGUNDOS = 30


def com_nova_tentativa(operacao, prazo: float, semente: int):
    """Repete ``operacao`` enquanto a trava estiver ocupada (espera de 1 a 5 ms, até o prazo)."""
    sorteio = random.Random(semente)
    while True:
        try:
            return operacao()
        except SincronizacaoEmAndamentoErro:
            if time.monotonic() > prazo:
                raise
            time.sleep(sorteio.uniform(0.001, 0.005))


def executar_em_threads(tarefa, quantidade: int) -> list:
    """Roda ``tarefa(indice)`` em ``quantidade`` threads e devolve os resultados (ou propaga)."""
    with ThreadPoolExecutor(max_workers=quantidade) as pool:
        futuros = [pool.submit(tarefa, indice) for indice in range(quantidade)]
        return [futuro.result(timeout=LIMITE_TOTAL_SEGUNDOS + 5) for futuro in futuros]


def test_reservas_concorrentes_com_trava_de_arquivo_nao_repetem_numero(tmp_path, relogio):
    """(a) 8 threads x 10 reservas na mesma série: 80 números distintos e contíguos."""
    arquivo = tmp_path / "_estado" / "emissoes_nfse.json"
    trava = tmp_path / "_estado" / "emissao_nfse.lock"
    prazo = time.monotonic() + LIMITE_TOTAL_SEGUNDOS

    def reservar_dez(indice: int) -> list[int]:
        estado = EstadoEmissaoArquivo(arquivo, relogio)  # uma instância por "processo"

        def reservar() -> int:
            with TravaArquivo(trava):
                return estado.reservar_proximo(SERIE, montar_id).numero

        return [com_nova_tentativa(reservar, prazo, 100 * indice + n) for n in range(10)]

    numeros = [n for lote in executar_em_threads(reservar_dez, 8) for n in lote]

    assert len(numeros) == 80 and len(set(numeros)) == 80
    assert sorted(numeros) == list(range(1, 81))
    gravados = EstadoEmissaoArquivo(arquivo, relogio).listar()
    assert [r.numero for r in gravados] == list(range(1, 81))
    assert not trava.exists()  # toda trava foi liberada


@pytest.fixture
def servidor(certificado):
    with certificado.arquivos_pem() as (caminho_cert, _):
        pem = Path(caminho_cert).read_bytes()
    with ServidorSefinFalso([pem]) as instancia:
        yield instancia


def test_fachadas_concorrentes_na_mesma_pasta_nao_repetem_ndps(
        tmp_path, pfx_teste, servidor):
    """(b) 4 threads, cada uma com a sua ``FreeDFe`` na mesma pasta, numeração automática."""
    relogio = RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))
    por_thread, threads = 5, 4
    prazo = time.monotonic() + LIMITE_TOTAL_SEGUNDOS
    config = ConfiguracaoNfseLocal(
        configuracao_api=servidor.configuracao, esperas_consulta=(0,),
        pausa=lambda segundos: None, pausa_entre_lotes_adn=0)

    def criar_dfe() -> FreeDFe:
        return FreeDFe(CertificadoA1(pfx_teste, SENHA_TESTE), cnpj=CNPJ_PRESTADOR,
                       ambiente=Ambiente.HOMOLOGACAO, pasta=tmp_path / "dados",
                       ca_bundle=servidor.ca_bundle, proxy=None, relogio=relogio,
                       config_nfse=config)

    def emitir_varias(indice: int) -> list[str]:
        dfe = criar_dfe()
        ids = []
        for n in range(por_thread):
            retorno = com_nova_tentativa(
                lambda: dfe.nfse.emitir(dps_minima(numero=None)), prazo, 100 * indice + n)
            ids.append(retorno.id_dps)
        return ids

    ids = [i for lote in executar_em_threads(emitir_varias, threads) for i in lote]

    total = threads * por_thread
    assert len(ids) == total and len(set(ids)) == total
    posts = servidor.chamadas("POST", f"{CAMINHO_SEFIN}/nfse")
    # Um número repetido seria o mesmo Id em dois POST: a Sefin responderia E0014 e a
    # biblioteca reconciliaria, deixando menos Ids distintos do que POST no servidor.
    assert len(posts) == total and len(servidor.dps_para_chave) == total
    assert sorted(int(id_dps[30:45]) for id_dps in servidor.dps_para_chave) == list(
        range(1, total + 1))
    registros = criar_dfe().nfse.emissoes()
    assert [r.numero for r in registros] == list(range(1, total + 1))
    assert {r.situacao for r in registros} == {SituacaoEmissao.AUTORIZADA}
    assert {r.tentativas for r in registros} == {1}
    assert not (tmp_path / "dados" / "homologacao" / CNPJ_PRESTADOR / "_estado"
                / "emissao_nfse.lock").exists()
