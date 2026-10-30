"""Parâmetros municipais da NFS-e Nacional (convênio, serviço e contribuinte) com cache.

Rotas (relatório técnico, out/2026), sob ``base_parametrizacao`` (host ADN)::

    GET /{codMun}/convenio              aderência do município e data de ativação
    GET /{codMun}/{codigoServico}       alíquota, regimes especiais, deduções/reduções
    GET /{codMun}/{CPF|CNPJ}            retenções e benefícios do contribuinte

A rota de convênio na nova base foi confirmada com o A1 real na produção restrita em
29/10/2026; a resposta traz os indicadores dentro de ``parametrosConvenio`` (ver ``Convenio``).
HIPÓTESE: o manual v1.2 ainda descreve as rotas legadas ``/parametros_municipais/...`` na base da
Sefin (``rota_legada=True``) e as rotas de serviço e contribuinte não foram testadas na nova base.
Os nomes dos campos não são oficiais: a leitura é tolerante, procura em objetos aninhados e o
retorno bruto (``dict``) fica sempre disponível.

O cache (TTL de 24 h) evita consultar a Sefin a cada emissão e mantém a emissão viável com
internet instável: se a consulta falhar, o último valor, mesmo vencido, é usado com aviso.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from freedfe.distribuicao.estado import Relogio, relogio_utc
from freedfe.excecoes import ComunicacaoErro, RespostaInvalidaErro, ValidacaoErro
from freedfe.nfse.cliente_base import ClienteNfseBase
from freedfe.nfse.resposta import ler_json, levantar_rejeicao, procurar
from freedfe.validadores import exigir_codigo_ibge, validar_cnpj, validar_cpf

logger = logging.getLogger(__name__)

TTL_PADRAO = dt.timedelta(hours=24)
_VERDADEIRO = {"true", "1", "s", "sim", "y", "yes"}
_FALSO = {"false", "0", "n", "nao", "não", "no"}


@dataclass(frozen=True)
class Convenio:
    """Convênio do município com a NFS-e Nacional.

    A resposta real (produção restrita, 29/10/2026) traz os indicadores dentro de
    ``parametrosConvenio``, como inteiros 0/1: ``aderenteAmbienteNacional`` e
    ``aderenteEmissorNacional``. A semântica de cada um não tem documentação oficial.

    Attributes:
        aderente: ``aderenteAmbienteNacional``. ``None`` quando a resposta não permite concluir:
            a verificação pré-emissão só bloqueia quando a aderência é explicitamente negativa.
        aderente_emissor_nacional: ``aderenteEmissorNacional`` (``None`` se ausente).
        data_ativacao: data de ativação do convênio, quando a resposta a traz.
        bruto: resposta completa, para consultar campos não interpretados.
    """

    codigo_municipio: str
    aderente: bool | None
    data_ativacao: dt.date | None
    bruto: dict[str, Any] = field(default_factory=dict, compare=False)
    aderente_emissor_nacional: bool | None = None

    @classmethod
    def de_dict(cls, codigo_municipio: str, dados: dict[str, Any]) -> Convenio:
        """Interpreta a resposta de forma tolerante a nomes, formatos e aninhamento."""
        aderente = _como_bool(procurar(
            dados, "aderenteAmbienteNacional", "aderente_ambiente_nacional", "aderente",
            "aderenteAN", "convenioAtivo"))
        emissor = _como_bool(procurar(
            dados, "aderenteEmissorNacional", "aderente_emissor_nacional"))
        ativacao = _como_data(procurar(
            dados, "dataAtivacao", "data_ativacao", "dtAtivacao", "dataInicio", "dataAdesao"))
        return cls(codigo_municipio, aderente, ativacao, dados, emissor)


def _como_bool(valor: Any) -> bool | None:
    if isinstance(valor, bool):
        return valor
    texto = str(valor).strip().lower() if valor is not None else ""
    if texto in _VERDADEIRO:
        return True
    return False if texto in _FALSO else None


def _como_data(valor: Any) -> dt.date | None:
    texto = str(valor).strip() if valor else ""
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", texto):
        dia, mes, ano = texto.split("/")
        texto = f"{ano}-{mes}-{dia}"
    try:
        return dt.datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def interpretar_parametros(resposta) -> dict[str, Any]:
    """Corpo JSON de uma consulta de parâmetros (2xx); listas viram ``{"itens": [...]}``."""
    if not 200 <= resposta.status < 300:
        levantar_rejeicao(resposta, None)
    dados = ler_json(resposta)
    if dados is None:
        return {}
    return dados if isinstance(dados, dict) else {"itens": dados}


class ClienteParametros(ClienteNfseBase):
    """Consultas de parametrização municipal (somente ``GET``)."""

    servico = "nfse-parametros"

    def __init__(self, *args, rota_legada: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._legada = rota_legada

    def convenio(self, codigo_municipio: str) -> dict[str, Any]:
        """Aderência do município ao ambiente nacional."""
        municipio = exigir_codigo_ibge(codigo_municipio)
        return self._consultar("convenio", f"{municipio}/convenio", municipio=municipio)

    def servico_municipal(self, codigo_municipio: str, codigo_servico: str) -> dict[str, Any]:
        """Alíquota, regimes especiais e deduções do serviço (``cTribNac`` de 6 dígitos)."""
        municipio = exigir_codigo_ibge(codigo_municipio)
        servico = re.sub(r"\D", "", str(codigo_servico))
        if len(servico) != 6:
            raise ValidacaoErro(f"Código de serviço deve ter 6 dígitos: {codigo_servico!r}")
        return self._consultar("servico", f"{municipio}/{servico}", municipio=municipio,
                               codigo_servico=servico)

    def contribuinte(self, codigo_municipio: str, documento: str) -> dict[str, Any]:
        """Retenções e benefícios do contribuinte (CNPJ ou CPF) no município."""
        municipio = exigir_codigo_ibge(codigo_municipio)
        documento = documento.strip().upper()
        if not (validar_cnpj(documento) or validar_cpf(documento)):
            raise ValidacaoErro(f"CNPJ/CPF inválido: {documento!r}")
        return self._consultar("contribuinte", f"{municipio}/{documento}", municipio=municipio)

    def _consultar(self, operacao: str, caminho: str, **referencias: str) -> dict[str, Any]:
        if self._legada:
            url = self._cfg.url_sefin(f"/parametros_municipais/{caminho}")
        else:
            url = self._cfg.url_parametrizacao(f"/{caminho}")
        return interpretar_parametros(self._requisitar(operacao, "GET", url, **referencias))


class CacheParametros(ABC):
    """Cache de parâmetros por município e consulta, com TTL (*template method*).

    ``obter_ou_buscar`` concentra a regra: devolve o valor em cache enquanto válido; vencido ou
    com ``atualizar=True``, chama ``buscar`` e grava o resultado. Se ``buscar`` falhar por rede ou
    resposta inválida e existir um valor guardado, mesmo vencido, usa-o com aviso no log, o que
    mantém a emissão viável com internet instável. As subclasses só dizem onde o valor fica
    (``_ler`` e ``_gravar``).

    Args:
        relogio: relógio UTC injetável.
        ttl: validade do valor (padrão 24 h; a parametrização muda raramente).
    """

    def __init__(self, relogio: Relogio = relogio_utc, ttl: dt.timedelta = TTL_PADRAO):
        self._relogio = relogio
        self._ttl = ttl

    @abstractmethod
    def _ler(self, municipio: str, nome: str) -> tuple[dt.datetime, dict[str, Any]] | None:
        """Valor guardado e o instante da consulta, ou ``None`` se não existe (ou é ilegível)."""

    @abstractmethod
    def _gravar(self, municipio: str, nome: str, momento: dt.datetime,
                dados: dict[str, Any]) -> None:
        """Guarda ``dados`` consultados em ``momento``, substituindo o valor anterior."""

    def obter_ou_buscar(self, municipio: str, nome: str, buscar: Callable[[], dict[str, Any]],
                        atualizar: bool = False) -> dict[str, Any]:
        """Valor em cache ainda válido ou o resultado de ``buscar`` (que é então gravado)."""
        guardado = self._ler(municipio, nome)
        agora = self._relogio()
        if guardado and not atualizar and agora - guardado[0] < self._ttl:
            return guardado[1]
        try:
            dados = buscar()
        except (ComunicacaoErro, RespostaInvalidaErro) as erro:
            if guardado:
                logger.warning("Parâmetros %s/%s indisponíveis (%s); usando cache de %s.",
                               municipio, nome, erro, guardado[0].isoformat())
                return guardado[1]
            raise
        self._gravar(municipio, nome, agora, dados)
        return dados


class CacheParametrosArquivo(CacheParametros):
    """Cache em JSON: ``<pasta>/<município>/<nome saneado>.json``, com escrita atômica.

    Um arquivo corrompido é ignorado e refeito.

    Args:
        pasta: raiz do cache (``<estado>/parametros_nfse``).
        relogio: relógio UTC injetável.
        ttl: validade do valor (padrão 24 h).
    """

    def __init__(self, pasta: str | Path, relogio: Relogio = relogio_utc,
                 ttl: dt.timedelta = TTL_PADRAO):
        super().__init__(relogio, ttl)
        self._pasta = Path(pasta)

    def _arquivo(self, municipio: str, nome: str) -> Path:
        return self._pasta / municipio / f"{re.sub(r'[^0-9A-Za-z_.-]', '_', nome)}.json"

    def _ler(self, municipio: str, nome: str) -> tuple[dt.datetime, dict[str, Any]] | None:
        arquivo = self._arquivo(municipio, nome)
        if not arquivo.exists():
            return None
        try:
            conteudo = json.loads(arquivo.read_text("utf-8"))
            return dt.datetime.fromisoformat(conteudo["consultado_em"]), conteudo["dados"]
        except (ValueError, KeyError, TypeError):
            return None  # cache corrompido é ignorado e refeito

    def _gravar(self, municipio: str, nome: str, momento: dt.datetime,
                dados: dict[str, Any]) -> None:
        arquivo = self._arquivo(municipio, nome)
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        temporario = arquivo.with_suffix(".tmp")
        temporario.write_text(json.dumps(
            {"consultado_em": momento.isoformat(), "dados": dados}, ensure_ascii=False,
            indent=1), "utf-8")
        temporario.replace(arquivo)
