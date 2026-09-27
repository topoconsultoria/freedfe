import pytest

from freedfe.distribuicao.estado import BLOQUEIO_SEGUNDOS, EstadoNSU
from freedfe.excecoes import LimiteConsultaErro


def test_bloqueio_de_uma_hora_mais_margem(tmp_path, relogio):
    estado = EstadoNSU(tmp_path / "e.json", relogio)
    assert estado.segundos_para_liberar() == 0
    estado.bloquear()
    assert estado.segundos_para_liberar() == BLOQUEIO_SEGUNDOS
    relogio.avancar(minutes=60)
    assert estado.segundos_para_liberar() == 5 * 60
    relogio.avancar(minutes=5)
    assert estado.segundos_para_liberar() == 0


def test_limite_de_20_consultas_pontuais_por_hora(tmp_path, relogio):
    estado = EstadoNSU(tmp_path / "e.json", relogio)
    for _ in range(20):
        estado.registrar_consulta_pontual()
    with pytest.raises(LimiteConsultaErro):
        estado.registrar_consulta_pontual()
    relogio.avancar(minutes=61)
    estado.registrar_consulta_pontual()


def test_persistencia(tmp_path, relogio):
    arquivo = tmp_path / "e.json"
    estado = EstadoNSU(arquivo, relogio)
    estado.atualizar_sequencia("000000000000042", "000000000000050")
    estado.bloquear()
    estado.salvar()
    recarregado = EstadoNSU(arquivo, relogio)
    assert recarregado.ult_nsu == "000000000000042"
    assert recarregado.max_nsu == "000000000000050"
    assert recarregado.segundos_para_liberar() > 0


def test_atualizar_sequencia_ignora_valores_vazios(tmp_path, relogio):
    estado = EstadoNSU(tmp_path / "e.json", relogio)
    estado.atualizar_sequencia("000000000000007", None)
    estado.atualizar_sequencia(None, None)
    assert estado.ult_nsu == "000000000000007"
