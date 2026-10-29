"""Sonda SÓ DE LEITURA na PRODUÇÃO da NFS-e Nacional, para confirmar o contrato das respostas.

Não emite, não cancela e não altera nada na Sefin. Faz no máximo seis requisições ``GET``/``HEAD``
e imprime **só a estrutura** das respostas: status HTTP, nomes de campos JSON, contagens e NSU.
Nunca imprime XML, CNPJ/CPF de terceiros nem valores fiscais. Os XMLs baixados ficam em memória.

Responde às hipóteses que só um ambiente com notas reais resolve:
    1. nomes dos campos JSON de ``GET /nfse/{chave}`` (os mesmos esperados na emissão);
    2. status de ``HEAD /dps/{id}`` para uma DPS que **existe** (hipótese: 200);
    3. formato do lote do ADN e semântica do NSU pedido (exclusivo ou inclusivo).

Variáveis de ambiente:
    SEFAZ_PFX, SEFAZ_PFX_SENHA   certificado A1 e senha (nunca por argumento de linha de comando)
    FREEDFE_CHAVE_TESTE          chave de acesso (50 posições) de uma NFS-e já emitida pelo titular
"""

from __future__ import annotations

import os

from lxml import etree

from freedfe import Ambiente, CertificadoA1
from freedfe.assinatura import verificar_assinatura
from freedfe.excecoes import FreeDFeErro
from freedfe.nfse.cliente_adn import interpretar_lote
from freedfe.nfse.resposta import interpretar_consulta_nfse, ler_json
from freedfe.servicos import configuracao_nfse
from freedfe.transporte import ClienteRest
from freedfe.validadores import exigir_chave_nfse

NS = "{http://www.sped.fazenda.gov.br/nfse}"


def estrutura(dados, nivel: int = 0) -> list[str]:
    """Nomes e tipos dos campos (com tamanho dos textos), sem os valores."""
    linhas = []
    if isinstance(dados, dict):
        for chave, valor in dados.items():
            tipo = type(valor).__name__
            extra = f", {len(valor)} caracteres" if isinstance(valor, str) else ""
            extra = f", {len(valor)} itens" if isinstance(valor, list) else extra
            linhas.append(f"{'  ' * nivel}{chave}: {tipo}{extra}")
            if isinstance(valor, dict):
                linhas += estrutura(valor, nivel + 1)
            elif isinstance(valor, list) and valor and isinstance(valor[0], dict):
                linhas.append(f"{'  ' * (nivel + 1)}[item 0]")
                linhas += estrutura(valor[0], nivel + 2)
    return linhas


def mostrar(titulo: str, resposta) -> dict | list | None:
    print(f"   {titulo}: HTTP {resposta.status}, {len(resposta.corpo)} bytes, "
          f"content-type={resposta.cabecalho('content-type')}")
    dados = None
    try:
        dados = ler_json(resposta) if resposta.corpo else None
    except FreeDFeErro as erro:
        print(f"   corpo não é JSON ({erro})")
    for linha in estrutura(dados):
        print(f"      {linha}")
    return dados


def main() -> int:
    """Executa as três sondas e imprime só estrutura e contagens."""
    chave = exigir_chave_nfse(os.environ["FREEDFE_CHAVE_TESTE"])
    http = ClienteRest(CertificadoA1.de_arquivo(os.environ["SEFAZ_PFX"],
                                                os.environ["SEFAZ_PFX_SENHA"]), timeout=60)
    cfg = configuracao_nfse(Ambiente.PRODUCAO)
    print(f"Produção, chave ...{chave[-6:]} (somente leitura)")

    print("1. GET /nfse/{chave}")
    resposta = http.requisitar("GET", cfg.url_sefin(f"/nfse/{chave}"))
    mostrar("resposta", resposta)
    id_dps = None
    try:
        retorno = interpretar_consulta_nfse(resposta)
        raiz = etree.fromstring(retorno.xml_nfse)
        inf_dps = raiz.find(f".//{NS}infDPS")
        id_dps = inf_dps.get("Id") if inf_dps is not None else None
        print(f"   biblioteca interpretou: chave confere={retorno.chave == chave}, "
              f"assinatura da Sefin confere={verificar_assinatura(raiz)}, "
              f"tipo_ambiente={retorno.tipo_ambiente}, "
              f"versao_aplicativo={retorno.versao_aplicativo}")
    except FreeDFeErro as erro:
        print(f"   biblioteca NÃO interpretou a resposta: {erro}")

    if id_dps:
        print("2. HEAD e GET /dps/{id} de uma DPS que existe")
        cabeca = http.requisitar("HEAD", cfg.url_sefin(f"/dps/{id_dps}"))
        print(f"   HEAD: HTTP {cabeca.status} (hipótese da biblioteca: 200)")
        dados = mostrar("GET", http.requisitar("GET", cfg.url_sefin(f"/dps/{id_dps}")))
        if isinstance(dados, dict):
            valores = [str(v) for v in dados.values()]
            print(f"   a chave aparece no corpo: {chave in valores}")

    print("3. ADN: GET /DFe/{nsu}")
    primeiro = http.requisitar(
        "GET", cfg.url_adn("/DFe/0?tipoNSU=DISTRIBUICAO&lote=true"))
    mostrar("NSU 0", primeiro)
    try:
        lote = interpretar_lote(primeiro)
    except FreeDFeErro as erro:
        print(f"   biblioteca NÃO interpretou o lote: {erro}")
        return 1
    nsus = [int(d.nsu) for d in lote.documentos]
    tipos = sorted({d.schema for d in lote.documentos})
    print(f"   biblioteca interpretou: status={lote.status}, documentos={len(nsus)}, "
          f"NSU mín={min(nsus, default=None)}, máx={max(nsus, default=None)}, tipos={tipos}")
    if nsus:
        maior = max(nsus)
        segundo = http.requisitar(
            "GET", cfg.url_adn(f"/DFe/{maior}?tipoNSU=DISTRIBUICAO&lote=true"))
        print(f"   pedindo NSU {maior}: HTTP {segundo.status}")
        try:
            seguinte = [int(d.nsu) for d in interpretar_lote(segundo).documentos]
        except FreeDFeErro as erro:
            print(f"   biblioteca NÃO interpretou o lote: {erro}")
            return 1
        semantica = ("INCLUSIVO (devolve o próprio NSU pedido)" if maior in seguinte
                     else "EXCLUSIVO (devolve só NSU maiores)")
        print(f"   documentos={len(seguinte)}, NSU mín={min(seguinte, default=None)}: {semantica}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
