import re

import pytest

from freedfe.auditoria import RegistroAuditoria
from freedfe.distribuicao.cliente import ClienteDistribuicao
from freedfe.distribuicao.estado import EstadoNSU
from freedfe.excecoes import LimiteConsultaErro
from freedfe.interessado import Interessado
from freedfe.repositorio.arquivos import RepositorioArquivos
from freedfe.servicos import configuracao_distribuicao
from freedfe.tipos import Ambiente, ModeloDocumento

from conftest import TransporteFalso
from fabrica_xml import (
    CNPJ_FORNECEDOR,
    CNPJ_INTERESSADO,
    doc_zip,
    gerar_chave,
    nfe_proc,
    res_nfe,
    resposta_distribuicao,
)


def criar_cliente(tmp_path, relogio, respostas, modelo=ModeloDocumento.NFE):
    transporte = TransporteFalso(respostas)
    cliente = ClienteDistribuicao(
        configuracao=configuracao_distribuicao(modelo), ambiente=Ambiente.HOMOLOGACAO,
        interessado=Interessado(cnpj=CNPJ_INTERESSADO, uf="29"), transporte=transporte,
        estado=EstadoNSU(tmp_path / "estado.json", relogio),
        repositorio=RepositorioArquivos(tmp_path / "repo", CNPJ_INTERESSADO),
        auditoria=RegistroAuditoria(tmp_path / "chamadas.jsonl"), pausa_entre_lotes=0)
    return cliente, transporte


def ult_nsu_enviados(transporte):
    return [re.search(rb"<ultNSU>(\d+)</ultNSU>", corpo).group(1).decode()
            for _, corpo, _ in transporte.enviados]


def test_loop_continua_do_ultnsu_devolvido_e_bloqueia_ao_esgotar(tmp_path, relogio):
    ch1 = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    ch2 = gerar_chave("55", CNPJ_FORNECEDOR, 2)
    respostas = [
        resposta_distribuicao("138", 2, 3, [doc_zip(res_nfe(ch1), 1, "resNFe_v1.01.xsd"),
                                            doc_zip(res_nfe(ch2), 2, "resNFe_v1.01.xsd")]),
        resposta_distribuicao("138", 3, 3, [doc_zip(nfe_proc(ch1), 3, "procNFe_v4.00.xsd")]),
    ]
    cliente, transporte = criar_cliente(tmp_path, relogio, respostas)
    resultado = cliente.sincronizar()

    assert ult_nsu_enviados(transporte) == ["000000000000000", "000000000000002"]
    assert resultado.executado and resultado.lotes == 2 and resultado.documentos == 3
    assert resultado.ult_nsu == "000000000000003"
    assert resultado.proxima_consulta_em_s > 3600  # ultNSU == maxNSU -> bloqueio


def test_nao_consulta_durante_bloqueio(tmp_path, relogio):
    cliente, transporte = criar_cliente(tmp_path, relogio, [resposta_distribuicao("137", 0, 0)])
    assert cliente.sincronizar().executado
    segunda = cliente.sincronizar()
    assert not segunda.executado and "Bloqueio" in segunda.motivo
    assert len(transporte.enviados) == 1
    relogio.avancar(minutes=66)
    transporte.respostas.append(resposta_distribuicao("137", 0, 0))
    assert cliente.sincronizar().executado


def test_656_adota_ultnsu_devolvido_e_bloqueia(tmp_path, relogio):
    cliente, _ = criar_cliente(tmp_path, relogio, [resposta_distribuicao("656", 40, 50)])
    resultado = cliente.sincronizar()
    assert resultado.ult_nsu == "000000000000040"
    assert resultado.proxima_consulta_em_s > 3600


def test_mdfe_678_e_consumo_indevido(tmp_path, relogio):
    cliente, _ = criar_cliente(tmp_path, relogio, [resposta_distribuicao("678", 7, 9)],
                               modelo=ModeloDocumento.MDFE)
    assert cliente.sincronizar().proxima_consulta_em_s > 3600


def test_rejeicao_nao_altera_nsu_nem_insiste(tmp_path, relogio):
    cliente, transporte = criar_cliente(tmp_path, relogio, [resposta_distribuicao("593", 8, 9)])
    resultado = cliente.sincronizar()
    assert resultado.ultimo_cstat == "593" and resultado.ult_nsu == "000000000000000"
    assert len(transporte.enviados) == 1
    assert resultado.proxima_consulta_em_s == 0


def test_documentos_sao_gravados_antes_de_avancar_o_nsu(tmp_path, relogio):
    ch = gerar_chave("55", CNPJ_FORNECEDOR, 1)
    resposta = resposta_distribuicao("138", 1, 1, [doc_zip(nfe_proc(ch), 1, "procNFe_v4.00.xsd")])
    cliente, _ = criar_cliente(tmp_path, relogio, [resposta])
    cliente.sincronizar()
    repo = RepositorioArquivos(tmp_path / "repo", CNPJ_INTERESSADO)
    assert repo.obter(ModeloDocumento.NFE, ch).xml_completo
    assert (tmp_path / "chamadas.jsonl").read_text().count("distNSU") == 1


def test_consulta_pontual_respeita_limite(tmp_path, relogio):
    respostas = [resposta_distribuicao("137", 0, 0) for _ in range(20)]
    cliente, _ = criar_cliente(tmp_path, relogio, respostas)
    for nsu in range(20):
        cliente.consultar_nsu(nsu)
    with pytest.raises(LimiteConsultaErro):
        cliente.consultar_nsu(21)
