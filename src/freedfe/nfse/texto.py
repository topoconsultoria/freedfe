"""Sanitização de texto para campos ``TSString`` do XML da DPS.

O XSD da NFS-e (v1.01) restringe ``TSString`` a Latin-1 imprimível (``[!-ÿ][ -ÿ]*[!-ÿ]``), sem
quebras de linha e sem espaços nas pontas. Aspas curvas, travessões e reticências colados de
editores (Word, Excel, ERP) fazem o schema recusar a DPS (E1235), por isso todo texto livre
passa por :func:`sanitizar`. ``TSStringComQuebraDeLinha`` (descrição do serviço) aceita
quebras de linha: use ``quebra_linha=True``.
"""

from __future__ import annotations

import re
import unicodedata

_SUBSTITUICOES = str.maketrans({
    # aspas simples
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    # aspas duplas
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    # travessões e hifens
    "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-",
    # reticências e marcador
    "\u2026": "...",
    # marcador
    "\u2022": "-",
    # espaços especiais
    "\u00a0": " ", "\u2007": " ", "\u202f": " ",
    # euro
    "\u20ac": "EUR",
    # marca registrada
    "\u2122": "(TM)",
    # seta
    "\u2192": "->",
    # hífen opcional
    "\u00ad": "",
})
_CONTROLES = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_ESPACOS = re.compile(r" {2,}")
_LINHAS_EM_BRANCO = re.compile(r"\n{3,}")


def _limpar_linha(texto: str) -> str:
    """Controles viram espaço, caracteres fora do Latin-1 são reduzidos e espaços colapsados."""
    texto = _CONTROLES.sub(" ", texto)
    texto = "".join(_para_latin1(caractere) for caractere in texto)
    return _ESPACOS.sub(" ", texto).strip()


def sanitizar(texto: str | None, maximo: int | None = None, *,
              quebra_linha: bool = False) -> str:
    """Devolve o texto compatível com ``TSString``.

    Normaliza (NFC), troca tipografia por ASCII, converte quebras e controles em espaço, reduz
    caracteres fora do Latin-1 à forma base (``ŝ`` vira ``s``; sem forma base vira ``?``),
    colapsa espaços, remove espaços das pontas e trunca em ``maximo`` caracteres.

    Com ``quebra_linha=True`` as quebras de linha são preservadas (como ``\n``), sem linhas
    em branco repetidas. Para campos fiscais prefira validar o tamanho em vez de truncar.
    """
    if not texto:
        return ""
    limpo = unicodedata.normalize("NFC", str(texto)).translate(_SUBSTITUICOES)
    if quebra_linha:
        linhas = (_limpar_linha(linha) for linha in re.split(r"\r\n|\r|\n", limpo))
        limpo = _LINHAS_EM_BRANCO.sub("\n\n", "\n".join(linhas)).strip()
    else:
        limpo = _limpar_linha(limpo)
    if maximo is not None and len(limpo) > maximo:
        limpo = limpo[:maximo].rstrip()
    return limpo


def _para_latin1(caractere: str) -> str:
    if ord(caractere) <= 0xFF:
        return caractere
    base = "".join(c for c in unicodedata.normalize("NFKD", caractere)
                   if not unicodedata.combining(c) and ord(c) <= 0xFF)
    return base or "?"
