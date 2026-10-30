"""Contrato de teste de ``EstadoEmissao``: toda implementação deve passar nestes casos.

Uso: herde ``ContratoEstadoEmissao`` numa classe ``Test...`` e implemente a fixture
``criar_estado``, que devolve uma função sem argumentos. Cada chamada cria **uma nova instância
sobre o mesmo armazenamento** (o mesmo arquivo, a mesma tabela...), como se fossem dois
processos; a primeira chamada de cada teste parte de um armazenamento vazio.

A atomicidade de ``reservar_proximo`` sem trava externa não é verificada aqui: backends que
a oferecem por conta própria (memória, banco) a testam à parte, e o backend em arquivo a
obtém da ``TravaArquivo`` (ver ``test_concorrencia_emissao.py``).
"""

from __future__ import annotations

import pytest

from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.estado_emissao import EstadoEmissao, RegistroEmissao, SituacaoEmissao
from freedfe.nfse.identificadores import id_dps

MUNICIPIO = "2927408"
EMITENTE = "11222333000181"
SERIE = 900
OUTRA_SERIE = 1


def montar_id(numero: int, serie: int = SERIE) -> str:
    """Id da DPS (45 posições) do emitente de teste."""
    return id_dps(MUNICIPIO, EMITENTE, serie, numero)


class ContratoEstadoEmissao:
    """Casos de teste do contrato; as subclasses fornecem ``criar_estado``."""

    @pytest.fixture
    def criar_estado(self):
        """Função que cria uma instância sobre o armazenamento do teste (a implementar)."""
        raise NotImplementedError("a subclasse deve implementar a fixture criar_estado")

    @pytest.fixture
    def estado(self, criar_estado) -> EstadoEmissao:
        return criar_estado()

    # -- reservar -------------------------------------------------------

    def test_reservar_cria_registro_pendente(self, estado, relogio):
        registro = estado.reservar(montar_id(1), SERIE, 1)
        assert registro.id_dps == montar_id(1) and (registro.serie, registro.numero) == (SERIE, 1)
        assert registro.situacao is SituacaoEmissao.PENDENTE and registro.tentativas == 0
        assert registro.ts_reserva == relogio().isoformat()
        assert estado.obter(montar_id(1)) == registro
        assert estado.obter(montar_id(2)) is None

    def test_reservar_pendente_e_idempotente(self, estado, relogio):
        primeira = estado.reservar(montar_id(1), SERIE, 1)
        relogio.avancar(minutes=5)
        segunda = estado.reservar(montar_id(1), SERIE, 1)
        assert segunda == primeira and len(estado.listar()) == 1

    def test_reservar_reabre_rejeitada_e_preserva_tentativas_e_ts_reserva(self, estado, relogio):
        reservada = estado.reservar(montar_id(1), SERIE, 1)
        relogio.avancar(minutes=1)
        estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA)
        estado.marcar(montar_id(1), SituacaoEmissao.REJEITADA,
                      erros=[{"codigo": "E0712"}], motivo="rejeitada")
        relogio.avancar(minutes=1)
        reaberta = estado.reservar(montar_id(1), SERIE, 1)
        assert reaberta.situacao is SituacaoEmissao.PENDENTE
        assert reaberta.erros == [] and reaberta.motivo is None
        assert reaberta.tentativas == 1
        assert reaberta.ts_reserva == reservada.ts_reserva
        assert estado.obter(montar_id(1)) == reaberta

    @pytest.mark.parametrize("situacao", [
        SituacaoEmissao.ENVIADA, SituacaoEmissao.INCERTA, SituacaoEmissao.AUTORIZADA])
    def test_reservar_bloqueia_dps_enviada_incerta_ou_autorizada(self, estado, situacao):
        estado.reservar(montar_id(1), SERIE, 1)
        estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA)
        if situacao is not SituacaoEmissao.ENVIADA:
            estado.marcar(montar_id(1), situacao)
        antes = estado.obter(montar_id(1))
        with pytest.raises(ValidacaoErro, match=f"já está {situacao.value}"):
            estado.reservar(montar_id(1), SERIE, 1)
        assert estado.obter(montar_id(1)) == antes

    # -- marcar ---------------------------------------------------------

    def test_marcar_registra_tentativas_e_carimbos(self, estado, relogio):
        estado.reservar(montar_id(1), SERIE, 1)
        enviada = estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA, caminho_dps="nfse/x.xml")
        assert enviada.situacao is SituacaoEmissao.ENVIADA and enviada.tentativas == 1
        assert enviada.ts_envio == relogio().isoformat() and enviada.caminho_dps == "nfse/x.xml"
        assert enviada.ts_resposta is None
        relogio.avancar(seconds=3)
        autorizada = estado.marcar(montar_id(1), SituacaoEmissao.AUTORIZADA, chave="1" * 50)
        assert autorizada.chave == "1" * 50 and autorizada.tentativas == 1
        assert autorizada.ts_resposta == relogio().isoformat()
        assert estado.obter(montar_id(1)) == autorizada

    @pytest.mark.parametrize("situacao", [SituacaoEmissao.REJEITADA, SituacaoEmissao.INCERTA])
    def test_marcar_rejeitada_ou_incerta_preenche_ts_resposta(self, estado, relogio, situacao):
        estado.reservar(montar_id(1), SERIE, 1)
        estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA)
        marcada = estado.marcar(montar_id(1), situacao, motivo="causa")
        assert marcada.ts_resposta == relogio().isoformat() and marcada.motivo == "causa"

    def test_marcar_campo_desconhecido_nao_altera_o_registro(self, estado):
        estado.reservar(montar_id(1), SERIE, 1)
        antes = estado.obter(montar_id(1))
        with pytest.raises(ValidacaoErro, match="desconhecido"):
            estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA, chave="1" * 50,
                          xml="<proibido/>")
        depois = estado.obter(montar_id(1))
        assert depois.situacao is antes.situacao is SituacaoEmissao.PENDENTE
        assert depois.tentativas == antes.tentativas == 0
        assert depois == antes  # nem a parte válida (chave) nem os carimbos foram gravados

    def test_marcar_id_inexistente_levanta_keyerror(self, estado):
        with pytest.raises(KeyError):
            estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA)

    # -- listar e numeração ---------------------------------------------

    def test_listar_ordena_por_serie_numero_e_id_e_filtra_por_situacao(self, estado):
        for numero in (3, 1, 2):
            estado.reservar(montar_id(numero), SERIE, numero)
        estado.reservar(montar_id(7, OUTRA_SERIE), OUTRA_SERIE, 7)
        estado.marcar(montar_id(2), SituacaoEmissao.INCERTA)
        assert [r.id_dps for r in estado.listar()] == [
            montar_id(7, OUTRA_SERIE), montar_id(1), montar_id(2), montar_id(3)]
        assert [r.id_dps for r in estado.listar("incerta")] == [montar_id(2)]
        assert [r.id_dps for r in estado.listar(SituacaoEmissao.PENDENTE)] == [
            montar_id(7, OUTRA_SERIE), montar_id(1), montar_id(3)]
        assert estado.listar("autorizada") == []

    def test_listar_desempata_pelo_id_quando_serie_e_numero_coincidem(self, estado):
        outro_emitente = id_dps(MUNICIPIO, "11444777000161", SERIE, 1)
        estado.reservar(outro_emitente, SERIE, 1)
        estado.reservar(montar_id(1), SERIE, 1)
        assert [r.id_dps for r in estado.listar()] == sorted([outro_emitente, montar_id(1)])

    def test_proximo_numero_por_serie(self, estado):
        assert estado.proximo_numero(SERIE) == 1
        estado.reservar(montar_id(1), SERIE, 1)
        estado.reservar(montar_id(2), SERIE, 2)
        estado.marcar(montar_id(2), SituacaoEmissao.REJEITADA)  # rejeição não devolve o número
        estado.reservar(montar_id(7, OUTRA_SERIE), OUTRA_SERIE, 7)
        assert estado.proximo_numero(SERIE) == 3
        assert estado.proximo_numero("1") == 8 and estado.proximo_numero(5) == 1

    # -- reservar_proximo -----------------------------------------------

    def test_reservar_proximo_numera_a_serie_e_monta_o_id(self, estado):
        recebidos = []

        def montar(numero: int) -> str:
            recebidos.append(numero)
            return montar_id(numero)

        primeiro = estado.reservar_proximo(SERIE, montar)
        segundo = estado.reservar_proximo(SERIE, montar)
        assert (primeiro.numero, segundo.numero) == (1, 2) and recebidos == [1, 2]
        assert primeiro.id_dps == montar_id(1) and segundo.id_dps == montar_id(2)
        assert primeiro.serie == SERIE and primeiro.situacao is SituacaoEmissao.PENDENTE
        assert primeiro.tentativas == 0 and primeiro.ts_reserva
        assert estado.obter(montar_id(2)) == segundo

    def test_reservar_proximo_e_independente_por_serie(self, estado):
        estado.reservar_proximo(SERIE, montar_id)
        estado.reservar_proximo(SERIE, montar_id)
        outra = estado.reservar_proximo(
            OUTRA_SERIE, lambda numero: montar_id(numero, OUTRA_SERIE))
        assert (outra.serie, outra.numero) == (OUTRA_SERIE, 1)
        assert estado.proximo_numero(SERIE) == 3

    def test_reservar_proximo_depois_de_reserva_explicita_continua_da_maior(self, estado):
        estado.reservar(montar_id(10), SERIE, 10)
        assert estado.reservar_proximo(SERIE, montar_id).numero == 11

    # -- descartar ------------------------------------------------------

    def test_descartar_pendente_nunca_enviada_devolve_o_numero(self, estado):
        reservada = estado.reservar_proximo(SERIE, montar_id)
        estado.descartar(reservada.id_dps)
        assert estado.obter(reservada.id_dps) is None and estado.listar() == []
        assert estado.reservar_proximo(SERIE, montar_id).numero == reservada.numero

    def test_descartar_preserva_os_demais_registros(self, estado):
        estado.reservar_proximo(SERIE, montar_id)
        segunda = estado.reservar_proximo(SERIE, montar_id)
        estado.descartar(segunda.id_dps)
        assert [r.numero for r in estado.listar()] == [1]

    @pytest.mark.parametrize("situacao", [
        SituacaoEmissao.ENVIADA, SituacaoEmissao.INCERTA, SituacaoEmissao.AUTORIZADA,
        SituacaoEmissao.REJEITADA])
    def test_descartar_recusa_dps_que_ja_foi_enviada(self, estado, situacao):
        estado.reservar(montar_id(1), SERIE, 1)
        estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA)
        if situacao is not SituacaoEmissao.ENVIADA:
            estado.marcar(montar_id(1), situacao)
        antes = estado.obter(montar_id(1))
        with pytest.raises(ValidacaoErro, match="não pode ser descartada"):
            estado.descartar(montar_id(1))
        assert estado.obter(montar_id(1)) == antes

    def test_descartar_recusa_pendente_reaberta_com_tentativas(self, estado):
        estado.reservar(montar_id(1), SERIE, 1)
        estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA)
        estado.marcar(montar_id(1), SituacaoEmissao.REJEITADA)
        reaberta = estado.reservar(montar_id(1), SERIE, 1)
        assert reaberta.situacao is SituacaoEmissao.PENDENTE and reaberta.tentativas == 1
        with pytest.raises(ValidacaoErro, match="não pode ser descartada"):
            estado.descartar(montar_id(1))
        assert estado.obter(montar_id(1)) == reaberta

    def test_descartar_id_inexistente_levanta_validacao_erro(self, estado):
        with pytest.raises(ValidacaoErro, match="não consta"):
            estado.descartar(montar_id(1))

    # -- garantir -------------------------------------------------------

    def test_garantir_cria_pendente_para_dps_desconhecida(self, estado):
        registro = estado.garantir(montar_id(2), SERIE, 2)
        assert registro.situacao is SituacaoEmissao.PENDENTE
        assert (registro.serie, registro.numero) == (SERIE, 2)
        assert estado.obter(montar_id(2)) == registro

    def test_garantir_nao_altera_registro_existente(self, estado):
        estado.reservar(montar_id(1), SERIE, 1)
        estado.marcar(montar_id(1), SituacaoEmissao.ENVIADA)
        antes = estado.obter(montar_id(1))
        assert estado.garantir(montar_id(1), SERIE, 1) == antes
        assert estado.obter(montar_id(1)) == antes

    # -- leitura da fonte a cada operação -------------------------------

    def test_cada_chamada_le_o_estado_atual_da_fonte(self, criar_estado):
        a, b = criar_estado(), criar_estado()
        a.reservar(montar_id(1), SERIE, 1)
        assert b.obter(montar_id(1)).situacao is SituacaoEmissao.PENDENTE
        assert b.proximo_numero(SERIE) == 2
        b.marcar(montar_id(1), SituacaoEmissao.ENVIADA)
        assert a.obter(montar_id(1)).situacao is SituacaoEmissao.ENVIADA
        assert [r.id_dps for r in a.listar("enviada")] == [montar_id(1)]
        with pytest.raises(ValidacaoErro, match="já está enviada"):
            a.reservar(montar_id(1), SERIE, 1)

    def test_reservar_proximo_em_instancias_distintas_nao_repete_numero(self, criar_estado):
        a, b = criar_estado(), criar_estado()
        numeros = [a.reservar_proximo(SERIE, montar_id).numero,
                   b.reservar_proximo(SERIE, montar_id).numero,
                   a.reservar_proximo(SERIE, montar_id).numero]
        assert numeros == [1, 2, 3]

    def test_descartar_em_uma_instancia_vale_para_as_outras(self, criar_estado):
        a, b = criar_estado(), criar_estado()
        reservada = a.reservar_proximo(SERIE, montar_id)
        b.descartar(reservada.id_dps)
        assert a.obter(reservada.id_dps) is None
        assert a.reservar_proximo(SERIE, montar_id).numero == 1

    # -- registro devolvido ---------------------------------------------

    def test_registro_devolvido_e_serializavel(self, estado):
        registro = estado.reservar(montar_id(1), SERIE, 1)
        assert RegistroEmissao.de_dict(registro.para_dict()) == registro
