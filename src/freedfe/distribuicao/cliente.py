"""Cliente do Web Service de Distribuição DF-e para um interessado e um modelo."""

from __future__ import annotations

import time
from dataclasses import dataclass

from freedfe.auditoria import RegistroAuditoria
from freedfe.distribuicao.estado import EstadoNSU
from freedfe.distribuicao.mensagens import MontadorDistribuicao
from freedfe.distribuicao.retorno import InterpretadorRetorno, RetornoDistribuicao
from freedfe.interessado import Interessado
from freedfe.repositorio.base import RepositorioDocumentos
from freedfe.servicos import ConfiguracaoServico
from freedfe.tipos import Ambiente, ModeloDocumento
from freedfe.transporte import Transporte
from freedfe.validadores import formatar_nsu


@dataclass
class ResultadoSincronizacao:
    """Resumo de uma execução de ``sincronizar``."""

    modelo: ModeloDocumento
    executado: bool
    lotes: int = 0
    documentos: int = 0
    ult_nsu: str | None = None
    max_nsu: str | None = None
    ultimo_cstat: str | None = None
    ultimo_xmotivo: str | None = None
    proxima_consulta_em_s: int = 0
    motivo: str | None = None


class ClienteDistribuicao:
    """Consome a sequência de NSU de um interessado em um serviço (NF-e, CT-e ou MDF-e).

    Regras preservadas da NT 2014.002 (item 3.11.4):
        - ``distNSU`` sempre continua do ``ultNSU`` devolvido pela chamada anterior.
        - 137 ou ``ultNSU == maxNSU``: bloqueio local de 1 h (+ margem).
        - 656 (NF-e/CT-e) ou 678 (MDF-e): bloqueio de 1 h; o ``ultNSU`` devolvido é adotado.
        - Consultas pontuais limitadas a 20 por hora.
    """

    def __init__(self, configuracao: ConfiguracaoServico, ambiente: Ambiente,
                 interessado: Interessado, transporte: Transporte, estado: EstadoNSU,
                 repositorio: RepositorioDocumentos, auditoria: RegistroAuditoria,
                 pausa_entre_lotes: float = 1.0):
        self._cfg = configuracao
        self._ambiente = Ambiente(ambiente)
        self._interessado = interessado
        self._transporte = transporte
        self._estado = estado
        self._repositorio = repositorio
        self._auditoria = auditoria
        self._pausa = pausa_entre_lotes
        self._montador = MontadorDistribuicao(configuracao, ambiente)
        self._interpretador = InterpretadorRetorno()

    @property
    def estado(self) -> EstadoNSU:
        """Estado da sequência de NSU."""
        return self._estado

    def sincronizar(self, max_lotes: int = 200) -> ResultadoSincronizacao:
        """Executa ``distNSU`` em loop até esgotar (137 / ``ult == max``) ou ``max_lotes``."""
        espera = self._estado.segundos_para_liberar()
        if espera:
            return self._resultado(executado=False, proxima=espera,
                                   motivo=f"Bloqueio local ativo; aguarde {espera}s.")
        lotes = documentos = 0
        ret: RetornoDistribuicao | None = None
        while lotes < max_lotes:
            ret = self._consultar_lote()
            lotes += 1
            documentos += self._processar_lote(ret)
            if self._deve_parar(ret):
                break
            time.sleep(self._pausa)
        return self._resultado(executado=True, lotes=lotes, documentos=documentos, retorno=ret)

    def consultar_nsu(self, nsu: str | int) -> RetornoDistribuicao:
        """``consNSU``: busca um único NSU (lacuna). Conta no limite de 20/hora."""
        self._registrar_consulta_pontual()
        dados = self._montador.consulta_nsu(self._interessado, nsu)
        ret = self._chamar(dados, modo="consNSU", parametro=formatar_nsu(nsu))
        self._armazenar(ret)
        return ret

    def consultar_chave(self, chave: str) -> RetornoDistribuicao:
        """``consChNFe`` (somente NF-e). Conta no limite de 20/hora."""
        self._registrar_consulta_pontual()
        dados = self._montador.consulta_chave(self._interessado, chave)
        ret = self._chamar(dados, modo="consChNFe", parametro=chave)
        self._armazenar(ret)
        return ret

    # ------------------------------------------------------------------
    # Etapas da sincronização
    # ------------------------------------------------------------------

    def _consultar_lote(self) -> RetornoDistribuicao:
        ult_nsu = self._estado.ult_nsu
        dados = self._montador.distribuicao_nsu(self._interessado, ult_nsu)
        return self._chamar(dados, modo="distNSU", parametro=ult_nsu)

    def _processar_lote(self, ret: RetornoDistribuicao) -> int:
        """Grava os documentos, atualiza a sequência e aplica bloqueio. Devolve a qtde gravada."""
        self._estado.registrar_execucao(ret.cstat)
        gravados = self._armazenar(ret) if ret.documentos_localizados else 0
        if ret.cstat in ("137", "138") or self._consumo_indevido(ret):
            self._estado.atualizar_sequencia(ret.ult_nsu, ret.max_nsu)
        if self._consumo_indevido(ret) or ret.sem_novos:
            self._estado.bloquear()
        self._estado.salvar()
        return gravados

    def _deve_parar(self, ret: RetornoDistribuicao) -> bool:
        """Para em bloqueio, sequência esgotada ou rejeição (não insiste em loop)."""
        return self._consumo_indevido(ret) or ret.sem_novos or not ret.documentos_localizados

    def _consumo_indevido(self, ret: RetornoDistribuicao) -> bool:
        return ret.cstat in self._cfg.cstat_consumo_indevido

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------

    def _chamar(self, dados_xml: str, modo: str, parametro: str) -> RetornoDistribuicao:
        corpo = self._montador.envelope(dados_xml, self._interessado.uf)
        inicio = time.monotonic()
        resposta = self._transporte.enviar(self._cfg.url(self._ambiente), corpo,
                                           self._cfg.acao_soap)
        ret = self._interpretador.interpretar(resposta)
        self._auditoria.registrar(
            modelo=self._cfg.modelo.value, ambiente=int(self._ambiente),
            interessado=self._interessado.documento, modo=modo, parametro=parametro,
            cstat=ret.cstat, xmotivo=ret.xmotivo, ult_nsu=ret.ult_nsu, max_nsu=ret.max_nsu,
            qtd_docs=len(ret.documentos), duracao_ms=int((time.monotonic() - inicio) * 1000))
        return ret

    def _armazenar(self, ret: RetornoDistribuicao) -> int:
        if not ret.documentos:
            return 0
        return self._repositorio.armazenar(ret.documentos, sequencia=self._cfg.modelo).total

    def _registrar_consulta_pontual(self) -> None:
        self._estado.registrar_consulta_pontual()
        self._estado.salvar()

    def _resultado(self, executado: bool, lotes: int = 0, documentos: int = 0,
                   retorno: RetornoDistribuicao | None = None, proxima: int | None = None,
                   motivo: str | None = None) -> ResultadoSincronizacao:
        return ResultadoSincronizacao(
            modelo=self._cfg.modelo, executado=executado, lotes=lotes, documentos=documentos,
            ult_nsu=self._estado.ult_nsu, max_nsu=self._estado.max_nsu,
            ultimo_cstat=self._estado.ultimo_cstat,
            ultimo_xmotivo=retorno.xmotivo if retorno else None,
            proxima_consulta_em_s=(proxima if proxima is not None
                                   else self._estado.segundos_para_liberar()),
            motivo=motivo,
        )
