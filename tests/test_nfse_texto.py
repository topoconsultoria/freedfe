import pytest

from freedfe.nfse.texto import sanitizar


@pytest.mark.parametrize("entrada, esperado", [
    ("“Olá” ‘mundo’", "\"Olá\" 'mundo'"),
    ("Consultoria – fase 1 — final", "Consultoria - fase 1 - final"),
    ("Reticências…", "Reticências..."),
    ("  muitos    espaços\t\ne\r\nquebras  ", "muitos espaços e quebras"),
    ("Ação coração", "Ação coração"),
    ("nbsp aqui", "nbsp aqui"),
    ("controle\x00\x07\x7f fim", "controle fim"),
    ("Valor € 10", "Valor EUR 10"),
    ("ŝ casa", "s casa"),
    ("emoji \U0001F600 fim", "emoji ? fim"),
    (None, ""),
    ("", ""),
])
def test_sanitizar(entrada, esperado):
    assert sanitizar(entrada) == esperado


def test_resultado_atende_ao_alfabeto_do_xsd():
    suja = "“Aspas”—travessão… \x01 \U0001F600 œ"
    limpo = sanitizar(suja)
    assert all(0x20 <= ord(c) <= 0xFF for c in limpo)
    assert limpo == limpo.strip() and "  " not in limpo


def test_truncamento_remove_espaco_final():
    assert sanitizar("abcde fghij", 6) == "abcde"
    assert sanitizar("abcde fghij", 5) == "abcde"
    assert sanitizar("abc", 10) == "abc"
