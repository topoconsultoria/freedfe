"""PEM do mTLS: em memória (memfd) no Linux, com o arquivo temporário 0600 como alternativa."""

from __future__ import annotations

import os
import ssl
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import freedfe.certificado as modulo_certificado
from freedfe.transporte import criar_contexto_tls

TEM_MEMFD = hasattr(os, "memfd_create") and os.path.isdir("/proc/self/fd")
so_linux = pytest.mark.skipif(not TEM_MEMFD, reason="exige os.memfd_create e /proc/self/fd")


def descritores_abertos() -> int:
    return len(os.listdir("/proc/self/fd"))


def sem_mkdtemp(*args, **kwargs):
    raise AssertionError("o PEM não pode ir para um diretório temporário")


@pytest.fixture
def forcar_alternativa(monkeypatch):
    """Faz o sistema parecer sem ``memfd_create`` (como macOS e Windows)."""
    monkeypatch.delattr(os, "memfd_create", raising=False)


# ---------------------------------------------------------------------------
# memfd
# ---------------------------------------------------------------------------

@so_linux
def test_memfd_devolve_caminhos_do_proc_com_modo_0600_e_o_pem_correto(certificado, monkeypatch):
    monkeypatch.setattr(modulo_certificado, "tempfile", SimpleNamespace(mkdtemp=sem_mkdtemp))
    with certificado.arquivos_pem() as (cert_pem, chave_pem):
        assert cert_pem.startswith("/proc/self/fd/") and chave_pem.startswith("/proc/self/fd/")
        assert cert_pem != chave_pem
        for caminho in (cert_pem, chave_pem):
            assert stat.S_IMODE(os.stat(caminho).st_mode) == 0o600
        assert Path(cert_pem).read_bytes().startswith(b"-----BEGIN CERTIFICATE-----")
        assert Path(chave_pem).read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
        # ler duas vezes devolve o conteúdo inteiro: cada abertura começa do início
        assert Path(cert_pem).read_bytes() == Path(cert_pem).read_bytes()


@so_linux
def test_memfd_aceito_pelo_load_cert_chain(certificado):
    contexto = ssl.create_default_context()
    with certificado.arquivos_pem() as (cert_pem, chave_pem):
        contexto.load_cert_chain(cert_pem, chave_pem)  # levanta ssl.SSLError se não ler o PEM


@so_linux
def test_memfd_e_fechado_ao_sair_do_bloco(certificado):
    antes = descritores_abertos()
    with certificado.arquivos_pem() as (cert_pem, chave_pem):
        assert descritores_abertos() == antes + 2
    assert descritores_abertos() == antes
    assert not os.path.exists(cert_pem) and not os.path.exists(chave_pem)


@so_linux
def test_memfd_e_fechado_mesmo_com_excecao_no_bloco(certificado):
    antes = descritores_abertos()
    with pytest.raises(RuntimeError, match="dentro do bloco"):
        with certificado.arquivos_pem():
            raise RuntimeError("falha dentro do bloco")
    assert descritores_abertos() == antes


@so_linux
def test_memfd_nao_vaza_para_processos_filhos(certificado):
    with certificado.arquivos_pem() as (cert_pem, _):
        descritor = int(cert_pem.rsplit("/", 1)[1])
        assert os.get_inheritable(descritor) is False  # MFD_CLOEXEC


@so_linux
def test_contextos_simultaneos_recebem_descritores_distintos(certificado):
    with certificado.arquivos_pem() as primeiro, certificado.arquivos_pem() as segundo:
        assert not set(primeiro) & set(segundo)
        assert Path(primeiro[0]).read_bytes() == Path(segundo[0]).read_bytes()


@so_linux
def test_contextos_em_threads_nao_se_misturam(certificado):
    resultados, falhas = [], []

    def usar():
        try:
            with certificado.arquivos_pem() as (cert_pem, chave_pem):
                contexto = ssl.create_default_context()
                contexto.load_cert_chain(cert_pem, chave_pem)
                resultados.append(cert_pem)
        except Exception as erro:  # noqa: BLE001 - reporta ao teste principal
            falhas.append(erro)

    threads = [threading.Thread(target=usar) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not falhas and len(resultados) == 8


@so_linux
def test_contexto_tls_do_transporte_nao_usa_diretorio_temporario(certificado, monkeypatch):
    monkeypatch.setattr(modulo_certificado, "tempfile", SimpleNamespace(mkdtemp=sem_mkdtemp))
    contexto = criar_contexto_tls(certificado)
    assert contexto.minimum_version == ssl.TLSVersion.TLSv1_2


# ---------------------------------------------------------------------------
# Alternativa: arquivo temporário 0600
# ---------------------------------------------------------------------------

def conferir_alternativa(certificado):
    antes = descritores_abertos() if TEM_MEMFD else None
    with certificado.arquivos_pem() as (cert_pem, chave_pem):
        assert not cert_pem.startswith("/proc/")
        assert os.path.dirname(cert_pem) == os.path.dirname(chave_pem)
        pasta = os.path.dirname(cert_pem)
        for caminho in (cert_pem, chave_pem):
            assert stat.S_IMODE(os.stat(caminho).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(pasta).st_mode) == 0o700  # mkdtemp
        assert Path(chave_pem).read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
        ssl.create_default_context().load_cert_chain(cert_pem, chave_pem)
    assert not os.path.exists(cert_pem) and not os.path.exists(chave_pem)
    assert not os.path.exists(pasta)  # nem o diretório fica para trás
    if antes is not None:
        assert descritores_abertos() == antes


def test_sem_memfd_grava_pem_temporario_0600_e_remove(certificado, forcar_alternativa):
    conferir_alternativa(certificado)


def test_sem_proc_self_fd_usa_a_alternativa(certificado, monkeypatch):
    real = os.path.isdir
    monkeypatch.setattr(os.path, "isdir",
                        lambda caminho: False if caminho == "/proc/self/fd" else real(caminho))
    conferir_alternativa(certificado)


@so_linux
def test_falha_ao_criar_o_memfd_cai_na_alternativa(certificado, monkeypatch):
    def falha(*args, **kwargs):
        raise OSError("memfd_create indisponível")

    monkeypatch.setattr(os, "memfd_create", falha)
    conferir_alternativa(certificado)


@so_linux
def test_falha_no_segundo_memfd_nao_vaza_o_primeiro(certificado, monkeypatch):
    criar = os.memfd_create
    chamadas = []

    def so_o_primeiro(*args, **kwargs):
        chamadas.append(1)
        if len(chamadas) == 2:
            raise OSError("sem descritores")
        return criar(*args, **kwargs)

    monkeypatch.setattr(os, "memfd_create", so_o_primeiro)
    conferir_alternativa(certificado)  # confere também que os descritores voltaram ao que eram
    assert len(chamadas) == 2


@so_linux
def test_falha_no_fchmod_cai_na_alternativa_sem_vazar(certificado, monkeypatch):
    def falha(*args, **kwargs):
        raise OSError("fchmod negado")

    monkeypatch.setattr(os, "fchmod", falha)
    conferir_alternativa(certificado)


def test_alternativa_remove_os_arquivos_mesmo_com_excecao(certificado, forcar_alternativa):
    caminhos = []
    with pytest.raises(RuntimeError, match="dentro do bloco"):
        with certificado.arquivos_pem() as (cert_pem, chave_pem):
            caminhos.extend([cert_pem, chave_pem])
            raise RuntimeError("falha dentro do bloco")
    assert not any(os.path.exists(c) for c in caminhos)
    assert not os.path.exists(os.path.dirname(caminhos[0]))


def test_contexto_tls_funciona_pela_alternativa(certificado, forcar_alternativa):
    assert criar_contexto_tls(certificado).minimum_version == ssl.TLSVersion.TLSv1_2
