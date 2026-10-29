"""Identificadores da NFS-e Nacional: Id da DPS, chave de acesso, Id da NFS-e e do evento.

Formação (relatório técnico out/2026, Anexo I do leiaute):

========================  =====  =================================================================
Identificador             Posic. Composição
========================  =====  =================================================================
Id da DPS                 45     ``DPS`` + IBGE(7) + tipo(1) + inscrição(14) + série(5) + nDPS(15)
Chave da NFS-e            50     IBGE(7) + ambiente gerador(1) + tipo(1) + inscrição(14) +
                                 nº NFS-e(13) + AAMM(4) + código(9) + DV(1)
Id da NFS-e               53     ``NFS`` + chave
Id do pedido de evento    59     ``PRE`` + chave + tipo do evento(6)
========================  =====  =================================================================

Tipo de inscrição: 1 = CPF (completado com ``000`` à esquerda), 2 = CNPJ. A série tem zeros à
esquerda **somente no Id**; no elemento ``serie`` vai sem zeros. Divergência gera E0004.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.leiaute import VersaoLeiaute
from freedfe.validadores import (
    exigir_chave_nfse,
    exigir_codigo_ibge,
    validar_cnpj,
    validar_cpf,
)

TIPO_INSCRICAO_CPF = "1"
TIPO_INSCRICAO_CNPJ = "2"
MAXIMO_SERIE = 99_999
MAXIMO_NUMERO_DPS = 999_999_999_999_999


def tipo_inscricao(documento: str) -> str:
    """``"1"`` para CPF (11 dígitos válidos) ou ``"2"`` para CNPJ (14 posições válidas)."""
    documento = (documento or "").strip().upper()
    if len(documento) == 11 and validar_cpf(documento):
        return TIPO_INSCRICAO_CPF
    if len(documento) == 14 and validar_cnpj(documento):
        return TIPO_INSCRICAO_CNPJ
    raise ValidacaoErro(f"CPF/CNPJ inválido para a NFS-e: {documento!r}")


def inscricao_14(documento: str) -> str:
    """Inscrição federal em 14 posições (CPF completado com ``000`` à esquerda)."""
    documento = documento.strip().upper()
    tipo_inscricao(documento)  # valida
    return documento.rjust(14, "0")


def normalizar_serie(serie: int | str) -> int:
    """Série da DPS como inteiro de 1 a 99999."""
    try:
        valor = int(str(serie).strip())
    except ValueError:
        raise ValidacaoErro(f"Série da DPS inválida: {serie!r}") from None
    if not 0 < valor <= MAXIMO_SERIE:
        raise ValidacaoErro(f"Série da DPS fora da faixa 1-{MAXIMO_SERIE}: {serie}")
    return valor


def normalizar_numero_dps(numero: int | str) -> int:
    """Número da DPS (``nDPS``) como inteiro de 1 a 15 dígitos."""
    try:
        valor = int(str(numero).strip())
    except ValueError:
        raise ValidacaoErro(f"Número da DPS inválido: {numero!r}") from None
    if not 0 < valor <= MAXIMO_NUMERO_DPS:
        raise ValidacaoErro(f"Número da DPS fora da faixa 1-{MAXIMO_NUMERO_DPS}: {numero}")
    return valor


def id_dps(codigo_municipio: str, documento: str, serie: int | str,
           numero: int | str) -> str:
    """Id da DPS (45 posições) a partir do município emissor, do prestador, da série e do nDPS."""
    municipio = exigir_codigo_ibge(codigo_municipio, "município emissor")
    return (f"DPS{municipio}{tipo_inscricao(documento)}{inscricao_14(documento)}"
            f"{normalizar_serie(serie):05d}{normalizar_numero_dps(numero):015d}")


def id_nfse(chave: str) -> str:
    """Id do elemento ``infNFSe`` (53 posições): ``NFS`` + chave."""
    return f"NFS{exigir_chave_nfse(chave)}"


def chave_de_id_nfse(identificador: str) -> str:
    """Chave de 50 posições a partir do Id ``NFS...`` (53 posições)."""
    identificador = (identificador or "").strip().upper()
    if not identificador.startswith("NFS"):
        raise ValidacaoErro(f"Id de NFS-e inválido: {identificador!r}")
    return exigir_chave_nfse(identificador[3:])


def id_pedido_evento(chave: str, tipo_evento: str, leiaute: VersaoLeiaute | None = None,
                     n_pedido: int | None = None) -> str:
    """Id do pedido de evento: ``PRE`` + chave + tipo (59 posições).

    ``n_pedido`` acrescenta o ``nPedRegEvento`` (3 dígitos) do leiaute antigo, com 62
    posições, que o XSD 1.01 recusa. Só use se a Sefin exigir.
    """
    chave = exigir_chave_nfse(chave)
    tipo_evento = str(tipo_evento).strip()
    if not re.fullmatch(r"[0-9]{6}", tipo_evento):
        raise ValidacaoErro(f"Tipo de evento inválido (6 dígitos): {tipo_evento!r}")
    identificador = f"PRE{chave}{tipo_evento}"
    if n_pedido is not None:
        if not 0 < n_pedido <= 999:
            raise ValidacaoErro(f"nPedRegEvento fora da faixa 1-999: {n_pedido}")
        identificador += f"{n_pedido:03d}"
    esperado = (leiaute or VersaoLeiaute.NT009_V101).tamanho_id_evento
    if n_pedido is None and len(identificador) != esperado:
        raise ValidacaoErro(f"Id de evento com {len(identificador)} posições; esperado {esperado}.")
    return identificador


@dataclass(frozen=True)
class ChaveNfse:
    """Partes da chave de acesso de 50 posições."""

    municipio: str
    ambiente_gerador: str
    tipo_inscricao: str
    inscricao: str
    numero: int
    aamm: str
    codigo: str
    dv: str

    @property
    def documento(self) -> str:
        """CPF (11) ou CNPJ (14) do emitente, sem o preenchimento de zeros do CPF."""
        if self.tipo_inscricao == TIPO_INSCRICAO_CPF:
            return self.inscricao[-11:]
        return self.inscricao

    @property
    def gerada_pela_sefin_nacional(self) -> bool:
        """``True`` quando o ambiente gerador é a Sefin Nacional (2); 1 = sistema municipal."""
        return self.ambiente_gerador == "2"

    @property
    def mes_emissao(self) -> dt.date:
        """Primeiro dia do mês de emissão (AAMM, ano com dois dígitos)."""
        return dt.date(2000 + int(self.aamm[:2]), int(self.aamm[2:]), 1)


def decompor_chave(chave: str) -> ChaveNfse:
    """Separa a chave de acesso da NFS-e em suas partes."""
    chave = exigir_chave_nfse(chave)
    return ChaveNfse(
        municipio=chave[0:7], ambiente_gerador=chave[7], tipo_inscricao=chave[8],
        inscricao=chave[9:23], numero=int(chave[23:36]), aamm=chave[36:40],
        codigo=chave[40:49], dv=chave[49])


def formatar_chave(chave: str) -> str:
    """Chave em grupos de 4 posições (leitura humana no DANFSe)."""
    chave = exigir_chave_nfse(chave)
    return " ".join(chave[i:i + 4] for i in range(0, len(chave), 4))
