"""Validação de CNPJ (numérico e alfanumérico), CPF, chave de acesso e NSU."""

from __future__ import annotations

import re

from freedfe.excecoes import ValidacaoErro

UFS_IBGE = frozenset({
    "11", "12", "13", "14", "15", "16", "17", "21", "22", "23", "24", "25", "26", "27",
    "28", "29", "31", "32", "33", "35", "41", "42", "43", "50", "51", "52", "53",
})

NSU_MAXIMO = 999_999_999_999_999


def _valor_caractere(caractere: str) -> int:
    """Valor do caractere no cálculo do DV (NT Conjunta 2025.001: código ASCII - 48).

    ``'0'..'9'`` valem 0..9 e ``'A'..'Z'`` valem 17..42.
    """
    return ord(caractere) - 48


def _digito_modulo_11(corpo: str, peso_maximo: int) -> int:
    """Calcula o DV módulo 11 com pesos de 2 até ``peso_maximo``, da direita para a esquerda."""
    pesos = range(2, peso_maximo + 1)
    soma = 0
    for indice, caractere in enumerate(reversed(corpo)):
        soma += _valor_caractere(caractere) * pesos[indice % len(pesos)]
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto


def validar_cnpj(cnpj: str) -> bool:
    """Valida CNPJ numérico ou alfanumérico (12 posições ``[0-9A-Z]`` + 2 DV numéricos)."""
    cnpj = (cnpj or "").strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", cnpj):
        return False
    if cnpj.isdigit() and len(set(cnpj)) == 1:
        return False
    for posicao_dv in (12, 13):
        if int(cnpj[posicao_dv]) != _digito_modulo_11(cnpj[:posicao_dv], 9):
            return False
    return True


def validar_cpf(cpf: str) -> bool:
    """Valida CPF (11 dígitos, DV módulo 11)."""
    cpf = (cpf or "").strip()
    if not re.fullmatch(r"[0-9]{11}", cpf) or len(set(cpf)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        if int(cpf[tamanho]) != (soma * 10) % 11 % 10:
            return False
    return True


def validar_chave(chave: str) -> bool:
    """Valida chave de acesso de 44 posições (aceita CNPJ alfanumérico nas posições 7-18)."""
    chave = (chave or "").strip().upper()
    if not re.fullmatch(r"[0-9]{6}[0-9A-Z]{12}[0-9]{26}", chave):
        return False
    return int(chave[43]) == _digito_modulo_11(chave[:43], 9)


def validar_uf(uf: str) -> bool:
    """Valida o código IBGE da UF (ex.: ``29`` = BA, ``21`` = MA, ``17`` = TO, ``22`` = PI)."""
    return uf in UFS_IBGE


def formatar_nsu(nsu: int | str) -> str:
    """Formata o NSU como exige o schema (TNSU = exatamente 15 dígitos)."""
    texto = str(nsu).strip() or "0"
    if not texto.isdigit():
        raise ValidacaoErro(f"NSU inválido: {nsu}")
    valor = int(texto)
    if valor > NSU_MAXIMO:
        raise ValidacaoErro(f"NSU fora da faixa: {nsu}")
    return f"{valor:015d}"


def exigir_chave(chave: str) -> str:
    """Valida e devolve a chave normalizada; levanta ``ValidacaoErro`` se inválida."""
    normalizada = (chave or "").strip().upper()
    if not validar_chave(normalizada):
        raise ValidacaoErro(f"Chave de acesso inválida: {chave}")
    return normalizada
