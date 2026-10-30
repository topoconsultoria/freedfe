"""Implementações em memória dos contratos de persistência da NFS-e (somente para testes).

Servem de segundo backend para provar que os contratos (``EstadoEmissao``, ``Trava``,
``EstadoSequenciaNsu``, ``CacheParametros``, ``ArmazenamentoEvidencias``, ``Auditoria`` e
``RepositorioDocumentos``) não dependem de arquivos e que ``ServicoNfse`` aceita qualquer
implementação injetada. Não são exportadas pela biblioteca.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import threading
from collections.abc import Callable, Iterable
from typing import Any

from freedfe.auditoria import Auditoria
from freedfe.distribuicao.estado import (
    BLOQUEIO_SEGUNDOS,
    EstadoSequenciaNsu,
    Relogio,
    relogio_utc,
)
from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.distribuicao.trava import Trava
from freedfe.documentos.leitor import LeitorDocumentoFiscal
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento
from freedfe.excecoes import SincronizacaoEmAndamentoErro
from freedfe.nfse.estado_emissao import EstadoEmissao, RegistroEmissao, SituacaoEmissao
from freedfe.nfse.evidencias import ArmazenamentoEvidencias
from freedfe.nfse.parametros import TTL_PADRAO, CacheParametros
from freedfe.repositorio.base import RepositorioDocumentos
from freedfe.tipos import ModeloDocumento


class EstadoEmissaoMemoria(EstadoEmissao):
    """Estado das emissões em um ``dict``, protegido por um ``threading.Lock``.

    ``reservar_proximo`` é atômico sem trava externa (a leitura do próximo número e a reserva
    acontecem sob o mesmo ``Lock``), como exige o contrato. Devolve cópias dos registros, como
    um banco de dados faria: alterar o retorno não altera o estado armazenado.

    ``outra_visao`` cria outra instância sobre o mesmo armazenamento, o que simula outro
    processo lendo o mesmo banco.
    """

    def __init__(self, relogio: Relogio = relogio_utc, *,
                 registros: dict[str, RegistroEmissao] | None = None,
                 trava: threading.Lock | None = None):
        super().__init__(relogio)
        self._registros = registros if registros is not None else {}
        self._trava = trava or threading.Lock()

    def outra_visao(self) -> EstadoEmissaoMemoria:
        """Outra instância sobre os mesmos registros."""
        return EstadoEmissaoMemoria(self._relogio, registros=self._registros, trava=self._trava)

    def obter(self, id_dps: str) -> RegistroEmissao | None:
        with self._trava:
            return copy.deepcopy(self._registros.get(id_dps))

    def listar(self, situacao: SituacaoEmissao | str | None = None) -> list[RegistroEmissao]:
        alvo = SituacaoEmissao(situacao) if situacao is not None else None
        with self._trava:
            itens = [r for r in self._registros.values() if alvo is None or r.situacao is alvo]
            return copy.deepcopy(sorted(itens, key=lambda r: (r.serie, r.numero, r.id_dps)))

    def reservar(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        with self._trava:
            return self._reservar(id_dps, serie, numero)

    def garantir(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        with self._trava:
            existente = self._registros.get(id_dps)
            if existente is not None:
                return copy.deepcopy(existente)
            return self._reservar(id_dps, serie, numero)

    def marcar(self, id_dps: str, situacao: SituacaoEmissao, **dados) -> RegistroEmissao:
        with self._trava:
            registro = self._registros[id_dps]
            self._aplicar_marcacao(registro, situacao, dados)
            return copy.deepcopy(registro)

    def proximo_numero(self, serie: int) -> int:
        with self._trava:
            return self._proximo_numero(serie)

    def reservar_proximo(self, serie: int,
                         montar_id: Callable[[int], str]) -> RegistroEmissao:
        with self._trava:
            numero = self._proximo_numero(serie)
            return self._reservar(montar_id(numero), int(serie), numero)

    def descartar(self, id_dps: str) -> None:
        with self._trava:
            self._validar_descarte(id_dps, self._registros.get(id_dps))
            del self._registros[id_dps]

    # Os métodos abaixo exigem ``self._trava`` já adquirida (``Lock`` não é reentrante).

    def _proximo_numero(self, serie: int) -> int:
        usados = [r.numero for r in self._registros.values() if r.serie == int(serie)]
        return max(usados, default=0) + 1

    def _reservar(self, id_dps: str, serie: int, numero: int) -> RegistroEmissao:
        existente = self._registros.get(id_dps)
        self._validar_reserva(existente)
        registro = self._registro_reservado(existente, id_dps, serie, numero)
        self._registros[id_dps] = registro
        return copy.deepcopy(registro)


class TravaMemoria(Trava):
    """Trava de um recurso baseada em ``threading.Lock``, *fail-fast*.

    Quem não consegue adquirir levanta ``SincronizacaoEmAndamentoErro`` de imediato. Registra
    em ``eventos`` (lista compartilhada) cada aquisição e liberação, para os testes conferirem.
    """

    def __init__(self, recurso: str, trava: threading.Lock,
                 eventos: list[tuple[str, str]] | None = None):
        self.recurso = recurso
        self._trava = trava
        self._eventos = eventos if eventos is not None else []

    def __enter__(self) -> TravaMemoria:
        if not self._trava.acquire(blocking=False):
            raise SincronizacaoEmAndamentoErro(
                f"Recurso em uso por outro processo (trava em memória: {self.recurso}).")
        self._eventos.append(("adquirida", self.recurso))
        return self

    def __exit__(self, *_excecao) -> None:
        self._eventos.append(("liberada", self.recurso))
        self._trava.release()


class TravasMemoria:
    """Fábrica de ``TravaMemoria``: um ``threading.Lock`` por nome de recurso.

    É chamável como ``FabricaTrava``. ``eventos`` guarda ``("adquirida" | "liberada", recurso)``
    na ordem em que ocorreram.
    """

    def __init__(self) -> None:
        self.eventos: list[tuple[str, str]] = []
        self._travas: dict[str, threading.Lock] = {}
        self._tabela = threading.Lock()

    def __call__(self, recurso: str) -> TravaMemoria:
        with self._tabela:
            trava = self._travas.setdefault(recurso, threading.Lock())
        return TravaMemoria(recurso, trava, self.eventos)

    def recursos(self, tipo: str) -> list[str]:
        """Recursos com o evento ``tipo`` (``"adquirida"`` ou ``"liberada"``), em ordem."""
        return [recurso for evento, recurso in self.eventos if evento == tipo]


class EstadoNsuMemoria(EstadoSequenciaNsu):
    """Estado de uma sequência de NSU guardado em um ``dict`` compartilhado (recurso -> dados)."""

    def __init__(self, armazenamento: dict[str, dict], recurso: str,
                 relogio: Relogio = relogio_utc, bloqueio_segundos: int = BLOQUEIO_SEGUNDOS):
        self.armazenamento = armazenamento
        self.recurso = recurso
        super().__init__(relogio, bloqueio_segundos)

    @classmethod
    def fabrica(cls, armazenamento: dict[str, dict], relogio: Relogio = relogio_utc,
                bloqueio_segundos: int = BLOQUEIO_SEGUNDOS) -> Callable[[str], EstadoNsuMemoria]:
        """``FabricaEstadoNsu`` sobre o ``armazenamento`` informado."""
        return lambda recurso: cls(armazenamento, recurso, relogio, bloqueio_segundos)

    def _ler_dados(self) -> dict | None:
        return copy.deepcopy(self.armazenamento.get(self.recurso))

    def _gravar_dados(self, dados: dict) -> None:
        self.armazenamento[self.recurso] = copy.deepcopy(dados)


class RepositorioMemoria(RepositorioDocumentos):
    """Repositório de documentos em dicionários, com a lógica herdada da base.

    Devolve cópias, como um banco faria. O XML fica indexado por ``caminho_xml`` (uma referência
    ``memoria:...`` no lugar do caminho relativo de ``RepositorioArquivos``).
    """

    def __init__(self, interessado: str | None = None, leitor: LeitorDocumentoFiscal | None = None):
        super().__init__(interessado, leitor)
        self._documentos: dict[ModeloDocumento, dict[str, ResumoDocumento]] = {}
        self._eventos: dict[ModeloDocumento, dict[str, list[EventoDocumento]]] = {}
        self._xmls: dict[str, bytes] = {}
        self._sequencias: dict[ModeloDocumento, set[str]] = {}
        self.nao_reconhecidos: list[DocumentoRecebido] = []

    def obter(self, modelo: ModeloDocumento, chave: str) -> ResumoDocumento | None:
        resumo = self._documentos.get(ModeloDocumento.normalizar(modelo), {}).get(chave)
        return copy.deepcopy(resumo)

    def eventos(self, modelo: ModeloDocumento, chave: str) -> list[EventoDocumento]:
        return copy.deepcopy(
            self._eventos.get(ModeloDocumento.normalizar(modelo), {}).get(chave, []))

    def ler_xml(self, resumo: ResumoDocumento) -> bytes:
        return self._xmls[resumo.caminho_xml]

    def _resumos(self, modelo: ModeloDocumento) -> Iterable[ResumoDocumento]:
        return copy.deepcopy(list(self._documentos.get(ModeloDocumento.normalizar(modelo), {})
                                  .values()))

    def _nsus(self, modelo: ModeloDocumento) -> Iterable[str]:
        return set(self._sequencias.get(ModeloDocumento.normalizar(modelo), set()))

    def _gravar_documento(self, resumo: ResumoDocumento, xml: bytes) -> None:
        documentos = self._documentos.setdefault(resumo.modelo, {})
        existente = documentos.get(resumo.chave)
        if existente and existente.xml_completo and not resumo.xml_completo:
            return  # não rebaixa XML completo para resumo
        resumo.caminho_xml = f"memoria:{resumo.modelo.value}/{resumo.chave}-{resumo.tipo_xml}"
        self._xmls[resumo.caminho_xml] = xml
        documentos[resumo.chave] = resumo

    def _gravar_evento(self, evento: EventoDocumento, xml: bytes) -> None:
        conhecidos = self._eventos.setdefault(evento.modelo, {}).setdefault(evento.chave, [])
        identificador = evento.protocolo or evento.nsu or hashlib.sha256(xml).hexdigest()[:16]
        evento.caminho_xml = (f"memoria:{evento.modelo.value}/eventos/"
                              f"{evento.chave}-{evento.tipo_evento}-{identificador}")
        if any(e.caminho_xml == evento.caminho_xml for e in conhecidos):
            return  # evento já armazenado
        self._xmls[evento.caminho_xml] = xml
        conhecidos.append(evento)

    def _gravar_nao_reconhecido(self, recebido: DocumentoRecebido) -> None:
        self.nao_reconhecidos.append(recebido)

    def _registrar_nsu(self, modelo: ModeloDocumento, nsu: str) -> None:
        self._sequencias.setdefault(ModeloDocumento.normalizar(modelo), set()).add(nsu)

    def _confirmar(self) -> None:
        """Nada a persistir: os dicionários já são o armazenamento."""


class CacheParametrosMemoria(CacheParametros):
    """Cache de parâmetros em um dicionário ``(município, nome) -> (momento, dados)``."""

    def __init__(self, relogio: Relogio = relogio_utc, ttl: dt.timedelta = TTL_PADRAO):
        super().__init__(relogio, ttl)
        self.itens: dict[tuple[str, str], tuple[dt.datetime, dict[str, Any]]] = {}

    def _ler(self, municipio: str, nome: str) -> tuple[dt.datetime, dict[str, Any]] | None:
        return copy.deepcopy(self.itens.get((municipio, nome)))

    def _gravar(self, municipio: str, nome: str, momento: dt.datetime,
                dados: dict[str, Any]) -> None:
        self.itens[(municipio, nome)] = (momento, copy.deepcopy(dados))


class EvidenciasMemoria(ArmazenamentoEvidencias):
    """Evidências em dicionário. A referência segue o desenho dos caminhos em arquivo.

    ``conteudos`` mapeia referência -> bytes e ``eventos`` registra cada gravação de evento
    (referência, chave, tipo, mês, sufixo), na ordem em que ocorreram.
    """

    def __init__(self) -> None:
        self.conteudos: dict[str, bytes] = {}
        self.dps: list[tuple[str, str, int, str]] = []
        self.eventos: list[tuple[str, str, str, str, str]] = []

    def gravar_dps(self, id_dps: str, mes: str, tentativa: int, sufixo: str,
                   conteudo: bytes) -> str:
        base, extensao = sufixo.rsplit(".", 1)
        nome = base if tentativa == 1 else f"{base}-{tentativa}"
        referencia = f"memoria:nfse/dps/{mes}/{id_dps}-{nome}.{extensao}"
        self.conteudos[referencia] = conteudo
        self.dps.append((referencia, id_dps, tentativa, sufixo))
        return referencia

    def gravar_evento(self, chave: str, tipo_evento: str, mes: str, sufixo: str,
                      conteudo: bytes) -> str:
        base, extensao = sufixo.rsplit(".", 1)
        indice = 1
        while True:
            nome = base if indice == 1 else f"{base}-{indice}"
            referencia = (f"memoria:nfse/eventos_enviados/{mes}/"
                          f"{chave}-{tipo_evento}-{nome}.{extensao}")
            if referencia not in self.conteudos:
                break
            indice += 1
        self.conteudos[referencia] = conteudo
        self.eventos.append((referencia, chave, tipo_evento, mes, sufixo))
        return referencia


class AuditoriaMemoria(Auditoria):
    """Trilha de auditoria em uma lista, protegida por ``Lock``."""

    def __init__(self) -> None:
        self.registros: list[dict[str, Any]] = []
        self._trava = threading.Lock()

    def registrar(self, **dados) -> None:
        with self._trava:
            self.registros.append(dict(dados))
