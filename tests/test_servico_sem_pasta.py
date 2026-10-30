"""Serviço de NFS-e sem pasta local: ``criar_servico_nfse`` com toda a persistência em memória.

O fluxo completo roda contra o ``ServidorSefinFalso`` (HTTPS com mTLS em sockets reais) e prova
que a biblioteca não escreve nada em disco: nem dados, nem PEM temporário (memfd), nem PDF.
"""

from __future__ import annotations

import datetime as dt
import inspect
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import freedfe.certificado as modulo_certificado
from freedfe import Ambiente, SituacaoDocumento, SituacaoEmissao, criar_servico_nfse
from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.nfse.envelope import descomprimir_b64
from freedfe.nfse.servico import ConfiguracaoNfseLocal, ServicoNfse
from freedfe.tipos import ModeloDocumento
from freedfe.transporte import ClienteRest
from servidor_sefin_falso import CAMINHO_SEFIN, CONVENIO_ADERENTE, ServidorSefinFalso

from conftest import RelogioFalso
from fabrica_nfse import CNPJ_PRESTADOR, DATA_EMISSAO, MUNICIPIO, dps_minima, nfse_xml
from memoria_nfse import (
    AuditoriaMemoria,
    CacheParametrosMemoria,
    EstadoEmissaoMemoria,
    EstadoNsuMemoria,
    EvidenciasMemoria,
    RepositorioMemoria,
    TravasMemoria,
)

JUSTIFICATIVA = "Servico nao prestado conforme contrato"
DEPENDENCIAS = ("repositorio", "auditoria", "estado_emissao", "travas", "estado_nsu",
                "cache_parametros", "evidencias")


@pytest.fixture
def relogio():
    return RelogioFalso(DATA_EMISSAO.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5))


@pytest.fixture
def servidor(certificado):
    with certificado.arquivos_pem() as (caminho_cert, _):
        pem = Path(caminho_cert).read_bytes()
    with ServidorSefinFalso([pem]) as instancia:
        yield instancia


def componentes(relogio) -> dict:
    """Toda a persistência em memória, nos nomes de parâmetro de ``criar_servico_nfse``."""
    return dict(
        repositorio=RepositorioMemoria(CNPJ_PRESTADOR),
        estado_emissao=EstadoEmissaoMemoria(relogio), travas=TravasMemoria(),
        estado_nsu=EstadoNsuMemoria.fabrica({}, relogio, 0),
        cache_parametros=CacheParametrosMemoria(relogio), evidencias=EvidenciasMemoria(),
        auditoria=AuditoriaMemoria())


def criar(servidor, certificado, relogio, partes, **extra) -> ServicoNfse:
    opcoes = ConfiguracaoNfseLocal(
        configuracao_api=servidor.configuracao, esperas_consulta=(0,),
        pausa=lambda segundos: None, pausa_entre_lotes_adn=0)
    return criar_servico_nfse(
        ambiente=Ambiente.HOMOLOGACAO, interessado=Interessado(cnpj=CNPJ_PRESTADOR),
        certificado=certificado, opcoes=opcoes, relogio=relogio, **partes, **extra)


def percorrer_fluxo(servico: ServicoNfse, servidor: ServidorSefinFalso, partes: dict) -> str:
    """Emite, consulta, cancela, lista eventos, sincroniza, lê o convênio e gera o DANFSe."""
    servidor.convenios[MUNICIPIO] = CONVENIO_ADERENTE
    outra_chave, outro_xml = nfse_xml(dps_minima(numero=77), numero_nfse=99)
    repositorio = partes["repositorio"]

    # 1. emitir, com numeração automática
    emitida = servico.emitir(dps_minima(numero=None))
    chave = emitida.chave
    assert emitida.id_dps.endswith("000000000000001")
    [registro] = servico.emissoes()
    assert registro.situacao is SituacaoEmissao.AUTORIZADA and registro.chave == chave
    postado = descomprimir_b64(servidor.chamadas("POST", f"{CAMINHO_SEFIN}/nfse")[0]
                               .json["dpsXmlGZipB64"])
    assert partes["evidencias"].conteudos[registro.caminho_dps] == postado  # exatamente o enviado
    assert registro.caminho_resposta in partes["evidencias"].conteudos
    assert repositorio.obter(ModeloDocumento.NFSE, chave).situacao is SituacaoDocumento.AUTORIZADO

    # 2. consultar a nota na Sefin
    assert servico.consultar_chave(chave).xml_nfse == emitida.xml_nfse

    # 3. cancelar
    assert servico.cancelar(chave, 2, JUSTIFICATIVA) is not None
    assert repositorio.obter_atual(ModeloDocumento.NFSE, chave).situacao \
        is SituacaoDocumento.CANCELADO
    assert [(e[2], e[4]) for e in partes["evidencias"].eventos] == [
        ("101101", "pedido.xml"), ("101101", "resposta.json")]

    # 4. listar eventos
    assert [e.tipo_evento for e in servico.listar_eventos(chave)] == ["101101"]

    # 5. sincronizar pelo ADN (a emissão e o cancelamento já estão lá; publica mais uma nota)
    assert servidor.publicar_no_adn(outro_xml) == 3
    sincronizado = servico.sincronizar()
    assert sincronizado.documentos == 3 and sincronizado.ult_nsu == "000000000000003"
    assert repositorio.obter(ModeloDocumento.NFSE, outra_chave) is not None

    # 6. convênio da parametrização, com cache
    assert servico.convenio(MUNICIPIO).aderente is True
    assert servico.convenio(MUNICIPIO).aderente_emissor_nacional is True
    assert len(servidor.chamadas("GET", "/adn/parametrizacao")) == 1
    assert (MUNICIPIO, "convenio") in partes["cache_parametros"].itens

    # 7. DANFSe em memória (nota cancelada: marca d'água)
    pdf = servico.danfse_bytes(chave)
    assert pdf.startswith(b"%PDF")
    return chave


def conferir_persistencia_em_memoria(partes: dict) -> None:
    """A trilha, as travas e os recursos usados no fluxo ficaram nos componentes injetados."""
    registros = partes["auditoria"].registros
    assert {r["servico"] for r in registros} >= {"nfse", "nfse-sefin", "nfse-adn",
                                                 "nfse-parametros"}
    assert all("<" not in str(r) for r in registros)  # a auditoria nunca guarda XML
    adquiridas = partes["travas"].recursos("adquirida")
    assert adquiridas.count("emissao_nfse") == 2  # emitir e cancelar
    assert adquiridas.count("nsu_nfse") == 1  # sincronizar
    assert sorted(adquiridas) == sorted(partes["travas"].recursos("liberada"))


# ---------------------------------------------------------------------------
# Fluxo completo e prova de que nada vai ao disco
# ---------------------------------------------------------------------------

def test_fluxo_completo_sem_pasta_nao_escreve_nada_em_disco(
        tmp_path, certificado, servidor, relogio, monkeypatch):
    pytest.importorskip("reportlab")
    monkeypatch.setenv("SSL_CERT_FILE", servidor.ca_bundle)  # o ClienteRest padrão confia nele
    partes = componentes(relogio)
    servico = criar(servidor, certificado, relogio, partes)  # sem transporte: ClienteRest padrão

    # O servidor e o serviço já existem; a partir daqui qualquer arquivo temporário aparece.
    monkeypatch.chdir(tmp_path)
    temporario = tmp_path / "tmp"
    temporario.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporario))

    percorrer_fluxo(servico, servidor, partes)
    conferir_persistencia_em_memoria(partes)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["tmp"]
    assert list(temporario.iterdir()) == []


@pytest.mark.skipif(not hasattr(os, "memfd_create") or not os.path.isdir("/proc/self/fd"),
                    reason="o PEM em memória exige memfd_create e /proc/self/fd (Linux)")
def test_fluxo_completo_usa_o_memfd_e_nunca_cria_diretorio_temporario(
        tmp_path, certificado, servidor, relogio, monkeypatch):
    pytest.importorskip("reportlab")
    partes = componentes(relogio)
    servico = criar(servidor, certificado, relogio, partes,
                    transporte=ClienteRest(certificado, ca_bundle=servidor.ca_bundle, proxy=None,
                                           timeout=5))

    def sem_mkdtemp(*args, **kwargs):
        raise AssertionError("o PEM do mTLS não pode ir para um diretório temporário")

    # Só o módulo do certificado perde o mkdtemp; o servidor de testes já foi criado.
    monkeypatch.setattr(modulo_certificado, "tempfile", SimpleNamespace(mkdtemp=sem_mkdtemp))
    percorrer_fluxo(servico, servidor, partes)
    assert servidor.requisicoes and all(r.cn_cliente for r in servidor.requisicoes)


def test_transporte_padrao_e_criado_sob_demanda_e_reaproveitado(
        certificado, servidor, relogio, monkeypatch):
    criados = []

    class ClienteEspiao(ClienteRest):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, ca_bundle=servidor.ca_bundle, **kwargs)
            criados.append(kwargs)

    monkeypatch.setattr("freedfe.nfse.fabrica.ClienteRest", ClienteEspiao)
    servidor.convenios[MUNICIPIO] = CONVENIO_ADERENTE
    servico = criar(servidor, certificado, relogio, componentes(relogio))
    assert criados == []  # nada criado antes da primeira chamada de rede
    servico.convenio(MUNICIPIO, atualizar=True)
    servico.convenio(MUNICIPIO, atualizar=True)
    assert criados == [{"proxy": None}]  # criado uma vez, sem proxy


# ---------------------------------------------------------------------------
# Contrato da fábrica e do serviço sem pasta
# ---------------------------------------------------------------------------

def test_fabrica_so_aceita_argumentos_nomeados_e_nao_tem_pasta():
    parametros = inspect.signature(criar_servico_nfse).parameters
    assert "pasta" not in parametros
    assert {p.kind for p in parametros.values()} == {inspect.Parameter.KEYWORD_ONLY}
    obrigatorios = {nome for nome, p in parametros.items() if p.default is inspect.Parameter.empty}
    assert obrigatorios == {"ambiente", "interessado", "certificado", *DEPENDENCIAS}


def servico_incompleto(certificado, relogio, omitir: tuple[str, ...]) -> ServicoNfse:
    completos = dict(componentes(relogio))
    for nome in omitir:
        completos.pop(nome)
    return ServicoNfse(
        ambiente=Ambiente.HOMOLOGACAO, interessado=Interessado(cnpj=CNPJ_PRESTADOR),
        obter_certificado=lambda: certificado, obter_transporte=lambda: None, relogio=relogio,
        **completos)


def test_sem_pasta_e_sem_nenhuma_dependencia_lista_todas_as_faltantes(certificado, relogio):
    with pytest.raises(ValidacaoErro) as erro:
        servico_incompleto(certificado, relogio, DEPENDENCIAS)
    assert str(erro.value).endswith(", ".join(DEPENDENCIAS) + ".")


@pytest.mark.parametrize("faltante", DEPENDENCIAS)
def test_sem_pasta_a_dependencia_faltante_e_nomeada(certificado, relogio, faltante):
    with pytest.raises(ValidacaoErro) as erro:
        servico_incompleto(certificado, relogio, (faltante,))
    assert str(erro.value).endswith(f"persistência: {faltante}.")


def test_sem_pasta_lista_so_as_que_faltam_na_ordem_do_contrato(certificado, relogio):
    with pytest.raises(ValidacaoErro) as erro:
        servico_incompleto(certificado, relogio, ("evidencias", "travas", "repositorio"))
    assert str(erro.value).endswith("repositorio, travas, evidencias.")
    assert servico_incompleto(certificado, relogio, ()) is not None


def test_com_pasta_cada_dependencia_ausente_recebe_o_padrao_em_arquivo(
        tmp_path, certificado, relogio):
    servico = ServicoNfse(
        ambiente=Ambiente.HOMOLOGACAO, interessado=Interessado(cnpj=CNPJ_PRESTADOR),
        pasta=tmp_path, obter_certificado=lambda: certificado, obter_transporte=lambda: None,
        relogio=relogio)
    estado = servico._novo_estado()
    assert type(estado).__name__ == "EstadoEmissaoArquivo"
    assert servico._novo_estado() is not estado  # recriado e relido a cada operação
    assert type(servico._evidencias).__name__ == "ArmazenamentoEvidenciasArquivo"
    assert type(servico._cache_parametros).__name__ == "CacheParametrosArquivo"
    assert type(servico._repositorio).__name__ == "RepositorioArquivos"
    assert type(servico._auditoria).__name__ == "RegistroAuditoria"
    assert servico._travas("emissao_nfse").arquivo == tmp_path / "_estado" / "emissao_nfse.lock"
    assert servico._estado_nsu("nsu_nfse").arquivo == tmp_path / "_estado" / "nsu_nfse.json"


def test_gerar_danfse_sem_pasta_e_sem_destino_sugere_danfse_bytes(certificado, relogio):
    servico = servico_incompleto(certificado, relogio, ())
    with pytest.raises(ValidacaoErro, match="danfse_bytes"):
        servico.gerar_danfse("1" * 50)


def test_gerar_danfse_sem_pasta_com_destino_explicito_funciona(
        tmp_path, certificado, servidor, relogio):
    pytest.importorskip("reportlab")
    partes = componentes(relogio)
    servico = criar(servidor, certificado, relogio, partes,
                    transporte=ClienteRest(certificado, ca_bundle=servidor.ca_bundle, proxy=None,
                                           timeout=5))
    chave = servico.emitir(dps_minima(numero=None)).chave
    pdf = servico.gerar_danfse(chave, tmp_path / "saida" / "nota.pdf")
    assert pdf.read_bytes().startswith(b"%PDF")


def test_danfse_bytes_de_nota_fora_do_repositorio_levanta_validacao_erro(
        certificado, relogio):
    servico = servico_incompleto(certificado, relogio, ())
    with pytest.raises(ValidacaoErro, match="não está no repositório"):
        servico.danfse_bytes("1" * 50)

