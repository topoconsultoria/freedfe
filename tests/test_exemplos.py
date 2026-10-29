"""Os exemplos precisam continuar montando DPS válidas (não acessam a rede)."""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from freedfe import Ambiente, VersaoLeiaute
from freedfe.nfse.montador_dps import montador_para

from conftest import RelogioFalso
from fabrica_nfse import CNPJ_PRESTADOR, MUNICIPIO

PASTA = Path(__file__).resolve().parent.parent / "examples"


def carregar(nome: str):
    especificacao = importlib.util.spec_from_file_location(nome, PASTA / f"{nome}.py")
    modulo = importlib.util.module_from_spec(especificacao)
    especificacao.loader.exec_module(modulo)
    return modulo


@pytest.mark.skipif(not PASTA.exists(), reason="pasta de exemplos ausente (instalação do pacote)")
@pytest.mark.parametrize("nome, fabrica", [
    ("emitir_nfse", "montar_dps"), ("nfse_smoke_producao_restrita", "montar_dps_com_defeito")])
@pytest.mark.parametrize("leiaute", list(VersaoLeiaute))
def test_dps_dos_exemplos_sao_montadas_nos_dois_leiautes(nome, fabrica, leiaute):
    dps = getattr(carregar(nome), fabrica)(CNPJ_PRESTADOR, MUNICIPIO, "152422")
    relogio = RelogioFalso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5))
    montador = montador_para(leiaute, Ambiente.HOMOLOGACAO, relogio=relogio)
    xml = montador.serializar(__import__("dataclasses").replace(dps, numero=1))
    assert xml.startswith(b"<DPS ") and b"<infDPS Id=" in xml
