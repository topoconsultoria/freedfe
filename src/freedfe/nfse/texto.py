"""Sanitização de texto para campos ``TSString`` do XML da DPS.

O XSD da NFS-e restringe ``TSString`` a Latin-1 imprimível, sem quebras de linha e sem espaços
nas pontas. Aspas curvas, travessões e reticências colados de editores (Word, Excel, ERP) fazem
o schema recusar a DPS (E1235), por isso todo texto livre passa por :func:`sanitizar`.
"""

from __future__ import annotations

import re
import unicodedata

_SUBSTITUICOES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "–": "-", "—": "-", "―": "-", "−": "-", "‐": "-", "‑": "-",
    "…": "...", "•": "-", " ": " ", " ": " ", " ": " ",
    "€": "EUR", "™": "(TM)", "→": "->", "­": "",
})
_CONTROLES = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_ESPACOS = re.compile(r" {2,}")


def sanitizar(texto: str | None, maximo: int | None = None) -> str:
    """Devolve o texto compatível com ``TSString``.

    Normaliza (NFC), troca tipografia por ASCII, converte quebras e controles em espaço, reduz
    caracteres fora do Latin-1 à forma base (``ŝ`` vira ``s``; sem forma base vira ``?``),
    colapsa espaços, remove espaços das pontas e trunca em ``maximo`` caracteres.
    """
    if not texto:
        return ""
    limpo = unicodedata.normalize("NFC", str(texto)).translate(_SUBSTITUICOES)
    limpo = _CONTROLES.sub(" ", limpo)
    limpo = "".join(_para_latin1(caractere) for caractere in limpo)
    limpo = _ESPACOS.sub(" ", limpo).strip()
    if maximo is not None and len(limpo) > maximo:
        limpo = limpo[:maximo].rstrip()
    return limpo


def _para_latin1(caractere: str) -> str:
    if ord(caractere) <= 0xFF:
        return caractere
    base = "".join(c for c in unicodedata.normalize("NFKD", caractere)
                   if not unicodedata.combining(c) and ord(c) <= 0xFF)
    return base or "?"
