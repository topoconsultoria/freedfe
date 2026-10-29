import json

import pytest

from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.estado_emissao import (
    SITUACOES_A_RECONCILIAR,
    EstadoEmissao,
    RegistroEmissao,
    SituacaoEmissao,
)

from conftest import RelogioFalso

ID_1 = "DPS" + "2927408" + "2" + "11222333000181" + "00900" + "000000000000001"
ID_2 = "DPS" + "2927408" + "2" + "11222333000181" + "00900" + "000000000000002"
ID_OUTRA_SERIE = "DPS" + "2927408" + "2" + "11222333000181" + "00001" + "000000000000007"


@pytest.fixture
def estado(tmp_path):
    return EstadoEmissao(tmp_path / "_estado" / "emissoes_nfse.json", RelogioFalso())


def test_reservar_cria_registro_pendente_e_persiste(estado, tmp_path):
    registro = estado.reservar(ID_1, 900, 1)
    assert registro.situacao is SituacaoEmissao.PENDENTE and registro.ts_reserva
    recarregado = EstadoEmissao(tmp_path / "_estado" / "emissoes_nfse.json")
    assert recarregado.obter(ID_1).situacao is SituacaoEmissao.PENDENTE
    assert json.loads((tmp_path / "_estado" / "emissoes_nfse.json").read_text())["emissoes"][
        ID_1]["situacao"] == "pendente"
    assert not list((tmp_path / "_estado").glob("*.tmp"))  # escrita atômica


def test_ciclo_de_vida_com_timestamps(estado):
    estado.reservar(ID_1, 900, 1)
    enviada = estado.marcar(ID_1, SituacaoEmissao.ENVIADA, caminho_dps="nfse/dps/x.xml")
    assert enviada.tentativas == 1 and enviada.ts_envio and enviada.caminho_dps
    autorizada = estado.marcar(ID_1, SituacaoEmissao.AUTORIZADA, chave="1" * 50)
    assert autorizada.chave == "1" * 50 and autorizada.ts_resposta


def test_reservar_exige_que_a_dps_nao_tenha_sido_enviada(estado):
    estado.reservar(ID_1, 900, 1)
    estado.marcar(ID_1, SituacaoEmissao.ENVIADA)
    with pytest.raises(ValidacaoErro, match="já está enviada"):
        estado.reservar(ID_1, 900, 1)
    estado.marcar(ID_1, SituacaoEmissao.REJEITADA, erros=[{"codigo": "E0712"}])
    reaberta = estado.reservar(ID_1, 900, 1)  # corrigida após rejeição
    assert reaberta.situacao is SituacaoEmissao.PENDENTE and reaberta.erros == []
    assert reaberta.tentativas == 1  # histórico de tentativas preservado


def test_garantir_nao_altera_registro_existente(estado):
    estado.reservar(ID_1, 900, 1)
    estado.marcar(ID_1, SituacaoEmissao.ENVIADA)
    assert estado.garantir(ID_1, 900, 1).situacao is SituacaoEmissao.ENVIADA
    assert estado.garantir(ID_2, 900, 2).situacao is SituacaoEmissao.PENDENTE


def test_proximo_numero_por_serie(estado):
    assert estado.proximo_numero(900) == 1
    estado.reservar(ID_1, 900, 1)
    estado.reservar(ID_2, 900, 2)
    estado.marcar(ID_2, SituacaoEmissao.REJEITADA)  # lacuna/rejeição não reaproveita número
    estado.reservar(ID_OUTRA_SERIE, 1, 7)
    assert estado.proximo_numero(900) == 3 and estado.proximo_numero("1") == 8
    assert estado.proximo_numero(5) == 1


def test_listar_ordenado_e_filtrado(estado):
    estado.reservar(ID_2, 900, 2)
    estado.reservar(ID_1, 900, 1)
    estado.reservar(ID_OUTRA_SERIE, 1, 7)
    estado.marcar(ID_1, SituacaoEmissao.INCERTA)
    assert [r.id_dps for r in estado.listar()] == [ID_OUTRA_SERIE, ID_1, ID_2]
    assert [r.id_dps for r in estado.listar("incerta")] == [ID_1]
    assert SituacaoEmissao.ENVIADA in SITUACOES_A_RECONCILIAR


def test_campo_desconhecido_e_serializacao(estado):
    estado.reservar(ID_1, 900, 1)
    with pytest.raises(ValidacaoErro, match="desconhecido"):
        estado.marcar(ID_1, SituacaoEmissao.ENVIADA, xml="<proibido/>")
    registro = estado.obter(ID_1)
    assert RegistroEmissao.de_dict(registro.para_dict()) == registro
