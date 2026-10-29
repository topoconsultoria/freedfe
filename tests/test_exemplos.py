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


@pytest.mark.skipif(not PASTA.exists(), reason="pasta de exemplos ausente (instalação do pacote)")
@pytest.mark.parametrize("codigos, veredito", [
    (["E0712"], "ok"),
    (["E0712", "E0160"], "ok"),
    (["E0710"], "inconclusivo"),
    (["E1229"], "falha"),  # XML sem declaração UTF-8: antes passava como "OK"
    (["E0714"], "falha"), (["E0715"], "falha"), (["E0712", "E0714"], "falha"),
    (["E1235"], "falha"), (["E0014"], "falha"), ([], "falha"),
    (["E0084"], "cadastro"),  # observado na produção restrita em 29/10/2026
    (["E0084", "E0119"], "cadastro"), (["E0084", "E1235"], "falha"),
])
def test_veredito_do_smoke_so_aceita_o_defeito_proposital(codigos, veredito):
    assert carregar("nfse_smoke_producao_restrita").classificar_rejeicao(codigos) == veredito
