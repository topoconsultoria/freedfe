"""Cache de parâmetros municipais: o mesmo comportamento em arquivo e em memória."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from freedfe.excecoes import ComunicacaoErro, RejeicaoNfseErro, RespostaInvalidaErro
from freedfe.nfse.parametros import TTL_PADRAO, CacheParametros, CacheParametrosArquivo

from conftest import RelogioFalso
from fabrica_nfse import MUNICIPIO
from memoria_nfse import CacheParametrosMemoria


@pytest.fixture
def relogio():
    return RelogioFalso()


@pytest.fixture(params=["arquivo", "memoria"])
def criar_cache(request, tmp_path, relogio):
    """Fábrica de caches da implementação, com ``ttl`` opcional."""
    if request.param == "arquivo":
        return lambda ttl=TTL_PADRAO: CacheParametrosArquivo(tmp_path, relogio, ttl)
    return lambda ttl=TTL_PADRAO: CacheParametrosMemoria(relogio, ttl)


def contador():
    chamadas = []

    def buscar():
        chamadas.append(1)
        return {"v": len(chamadas)}

    return buscar, chamadas


def falha(erro):
    def buscar():
        raise erro
    return buscar


def test_valor_valido_nao_consulta_de_novo(criar_cache, relogio):
    cache = criar_cache()
    buscar, chamadas = contador()
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar) == {"v": 1}
    relogio.avancar(hours=23, minutes=59)
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar) == {"v": 1}
    assert len(chamadas) == 1


def test_vencido_e_atualizar_consultam_de_novo(criar_cache, relogio):
    cache = criar_cache()
    buscar, _ = contador()
    cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar)
    relogio.avancar(hours=24, minutes=1)
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar) == {"v": 2}
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar, atualizar=True) == {"v": 3}


def test_ttl_configuravel(criar_cache, relogio):
    cache = criar_cache(ttl=dt.timedelta(minutes=10))
    buscar, chamadas = contador()
    cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar)
    relogio.avancar(minutes=9)
    cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar)
    assert len(chamadas) == 1
    relogio.avancar(minutes=2)
    cache.obter_ou_buscar(MUNICIPIO, "convenio", buscar)
    assert len(chamadas) == 2


@pytest.mark.parametrize("erro", [ComunicacaoErro("sem internet"), RespostaInvalidaErro("lixo")])
def test_valor_vencido_e_usado_quando_a_consulta_falha(criar_cache, relogio, caplog, erro):
    cache = criar_cache()
    cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"v": 1})
    relogio.avancar(days=3)
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", falha(erro)) == {"v": 1}
    assert f"Parâmetros {MUNICIPIO}/convenio indisponíveis" in caplog.text
    assert "usando cache de" in caplog.text
    with pytest.raises(type(erro)):
        cache.obter_ou_buscar(MUNICIPIO, "outro", falha(erro))  # sem valor anterior: propaga


def test_rejeicao_da_sefin_nao_e_mascarada_pelo_cache(criar_cache, relogio):
    cache = criar_cache()
    cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"v": 1})
    relogio.avancar(days=3)
    with pytest.raises(RejeicaoNfseErro):
        cache.obter_ou_buscar(MUNICIPIO, "convenio", falha(RejeicaoNfseErro([], status_http=404)))


def test_municipio_e_nome_formam_chaves_independentes(criar_cache):
    cache = criar_cache()
    assert cache.obter_ou_buscar("2927408", "convenio", lambda: {"m": 1}) == {"m": 1}
    assert cache.obter_ou_buscar("2910800", "convenio", lambda: {"m": 2}) == {"m": 2}
    assert cache.obter_ou_buscar("2927408", "servico-010101", lambda: {"m": 3}) == {"m": 3}
    assert cache.obter_ou_buscar("2927408", "convenio", lambda: {"m": 9}) == {"m": 1}


def test_valor_devolvido_e_gravado_nao_se_afetam(criar_cache):
    cache = criar_cache()
    dados = {"lista": [1]}
    cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: dados)
    dados["lista"].append(2)  # alterar o que a consulta devolveu não muda o que foi guardado
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"x": 1}) == {"lista": [1]}


def test_contrato_e_abstrato_e_as_duas_implementacoes_o_cumprem():
    with pytest.raises(TypeError, match="abstract"):
        CacheParametros()
    assert issubclass(CacheParametrosArquivo, CacheParametros)
    assert issubclass(CacheParametrosMemoria, CacheParametros)


# ---------------------------------------------------------------------------
# Implementação em arquivo: layout e tolerância a cache corrompido
# ---------------------------------------------------------------------------

def test_layout_municipio_e_nome_saneado(tmp_path, relogio):
    cache = CacheParametrosArquivo(tmp_path, relogio)
    cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"a": 1})
    cache.obter_ou_buscar(MUNICIPIO, "servico-010101", lambda: {"b": 2})
    cache.obter_ou_buscar(MUNICIPIO, "contribuinte-11222333000181", lambda: {"c": 3})
    cache.obter_ou_buscar(MUNICIPIO, "../fora/x y", lambda: {"d": 4})
    assert sorted(p.name for p in (tmp_path / MUNICIPIO).iterdir()) == [
        ".._fora_x_y.json", "contribuinte-11222333000181.json", "convenio.json",
        "servico-010101.json"]
    assert not (tmp_path / "fora").exists()  # o nome nunca sai da pasta do município
    conteudo = json.loads((tmp_path / MUNICIPIO / "convenio.json").read_text())
    assert conteudo == {"consultado_em": relogio().isoformat(), "dados": {"a": 1}}
    assert not list(tmp_path.rglob("*.tmp"))  # escrita atômica


@pytest.mark.parametrize("lixo", ["{nao e json", "[]", '{"consultado_em": "ontem", "dados": {}}',
                                  '{"dados": {}}'])
def test_arquivo_corrompido_e_ignorado_e_refeito(tmp_path, relogio, lixo):
    cache = CacheParametrosArquivo(tmp_path, relogio)
    cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"v": 1})
    (tmp_path / MUNICIPIO / "convenio.json").write_text(lixo)
    assert cache.obter_ou_buscar(MUNICIPIO, "convenio", lambda: {"v": 2}) == {"v": 2}
    assert json.loads((tmp_path / MUNICIPIO / "convenio.json").read_text())["dados"] == {"v": 2}


def test_outra_instancia_enxerga_o_valor_gravado(tmp_path, relogio):
    CacheParametrosArquivo(tmp_path, relogio).obter_ou_buscar(
        MUNICIPIO, "convenio", lambda: {"v": 1})
    buscar, chamadas = contador()
    assert CacheParametrosArquivo(tmp_path, relogio).obter_ou_buscar(
        MUNICIPIO, "convenio", buscar) == {"v": 1}
    assert chamadas == []
