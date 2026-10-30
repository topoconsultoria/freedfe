import json
import threading

import pytest

from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.estado_emissao import (
    SITUACOES_A_RECONCILIAR,
    EstadoEmissao,
    EstadoEmissaoArquivo,
    RegistroEmissao,
    SituacaoEmissao,
)

from conftest import RelogioFalso
from contrato_estado_emissao import SERIE, ContratoEstadoEmissao, montar_id
from memoria_nfse import EstadoEmissaoMemoria

ID_1 = "DPS" + "2927408" + "2" + "11222333000181" + "00900" + "000000000000001"
ID_2 = "DPS" + "2927408" + "2" + "11222333000181" + "00900" + "000000000000002"
ID_OUTRA_SERIE = "DPS" + "2927408" + "2" + "11222333000181" + "00001" + "000000000000007"


@pytest.fixture
def estado(tmp_path):
    return EstadoEmissaoArquivo(tmp_path / "_estado" / "emissoes_nfse.json", RelogioFalso())


def test_reservar_cria_registro_pendente_e_persiste(estado, tmp_path):
    registro = estado.reservar(ID_1, 900, 1)
    assert registro.situacao is SituacaoEmissao.PENDENTE and registro.ts_reserva
    recarregado = EstadoEmissaoArquivo(tmp_path / "_estado" / "emissoes_nfse.json")
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


# ---------------------------------------------------------------------------
# Contrato de EstadoEmissao: o mesmo conjunto de casos para cada implementação
# ---------------------------------------------------------------------------

class TestContratoArquivo(ContratoEstadoEmissao):
    """``EstadoEmissaoArquivo``: instâncias distintas sobre o mesmo JSON (como dois processos)."""

    @pytest.fixture
    def criar_estado(self, tmp_path, relogio):
        arquivo = tmp_path / "_estado" / "emissoes_nfse.json"
        return lambda: EstadoEmissaoArquivo(arquivo, relogio)

    def test_instancia_ja_criada_enxerga_o_que_outro_processo_gravou(self, tmp_path, relogio):
        arquivo = tmp_path / "emissoes_nfse.json"
        antiga = EstadoEmissaoArquivo(arquivo, relogio)
        EstadoEmissaoArquivo(arquivo, relogio).reservar(ID_1, 900, 1)  # "outro processo"
        assert antiga.obter(ID_1).situacao is SituacaoEmissao.PENDENTE
        assert antiga.proximo_numero(900) == 2
        assert [r.id_dps for r in antiga.listar()] == [ID_1]

    def test_descartar_regrava_o_json_sem_o_registro(self, tmp_path, relogio):
        arquivo = tmp_path / "emissoes_nfse.json"
        estado = EstadoEmissaoArquivo(arquivo, relogio)
        estado.reservar(ID_1, 900, 1)
        estado.reservar(ID_2, 900, 2)
        estado.descartar(ID_2)
        assert list(json.loads(arquivo.read_text())["emissoes"]) == [ID_1]
        assert not list(tmp_path.glob("*.tmp"))

    def test_reservar_proximo_grava_o_id_montado(self, tmp_path, relogio):
        arquivo = tmp_path / "emissoes_nfse.json"
        registro = EstadoEmissaoArquivo(arquivo, relogio).reservar_proximo(900, montar_id_900)
        assert registro.id_dps == ID_1
        assert json.loads(arquivo.read_text())["emissoes"][ID_1]["numero"] == 1


class TestContratoMemoria(ContratoEstadoEmissao):
    """``EstadoEmissaoMemoria``: segundo backend, sem arquivos."""

    @pytest.fixture
    def criar_estado(self, relogio):
        base = EstadoEmissaoMemoria(relogio)
        return base.outra_visao

    def test_reservar_proximo_e_atomico_sem_trava_externa(self, estado):
        total = 8 * 10
        numeros, falhas = [], []
        guarda = threading.Lock()

        def reservar_varios():
            try:
                for _ in range(10):
                    numero = estado.reservar_proximo(SERIE, montar_id).numero
                    with guarda:
                        numeros.append(numero)
            except Exception as erro:  # noqa: BLE001 - reporta ao teste principal
                falhas.append(erro)

        threads = [threading.Thread(target=reservar_varios) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not falhas and not any(t.is_alive() for t in threads)
        assert sorted(numeros) == list(range(1, total + 1))
        assert len(estado.listar()) == total

    def test_registro_devolvido_nao_altera_o_estado_armazenado(self, estado):
        registro = estado.reservar(ID_1, 900, 1)
        registro.situacao = SituacaoEmissao.AUTORIZADA
        registro.erros.append({"codigo": "E0001"})
        guardado = estado.obter(ID_1)
        assert guardado.situacao is SituacaoEmissao.PENDENTE and guardado.erros == []


def montar_id_900(numero: int) -> str:
    """Id da DPS da série 900 usada nas constantes deste módulo."""
    return "DPS" + "2927408" + "2" + "11222333000181" + "00900" + f"{numero:015d}"


def test_estado_emissao_e_um_contrato_abstrato():
    with pytest.raises(TypeError, match="abstract"):
        EstadoEmissao(RelogioFalso())
    assert issubclass(EstadoEmissaoArquivo, EstadoEmissao)
    assert issubclass(EstadoEmissaoMemoria, EstadoEmissao)
