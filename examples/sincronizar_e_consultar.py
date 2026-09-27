"""Exemplo ponta a ponta: sincroniza, lista, exporta, baixa XML e gera PDF.

Variáveis de ambiente:
    SEFAZ_PFX        caminho do certificado A1 (.pfx)
    SEFAZ_PFX_SENHA  senha do certificado (nunca passe por argumento de linha de comando)
    FREEDFE_CNPJ     CNPJ de interesse
    FREEDFE_UF       código IBGE da UF do autor (ex.: 29 = BA)
    FREEDFE_AMB      1 = produção, 2 = homologação (padrão: 2)

Execute primeiro em homologação.
"""

from __future__ import annotations

import datetime as dt
import logging
import os

from freedfe import Ambiente, Direcao, FiltroConsulta, FreeDFe, ModeloDocumento


def criar_cliente() -> FreeDFe:
    """Monta o cliente a partir das variáveis de ambiente."""
    return FreeDFe(
        os.environ["SEFAZ_PFX"],
        os.environ["SEFAZ_PFX_SENHA"],
        cnpj=os.environ["FREEDFE_CNPJ"],
        uf=os.environ["FREEDFE_UF"],
        ambiente=Ambiente(int(os.environ.get("FREEDFE_AMB", "2"))),
        pasta="./dados_dfe",
    )


def main() -> None:
    """Executa o fluxo completo para os últimos 30 dias."""
    logging.basicConfig(level=logging.INFO)
    dfe = criar_cliente()

    info = dfe.info_certificado()
    print(f"Certificado: {info.titular} (vence em {info.dias_para_vencer} dias)")

    for resultado in dfe.sincronizar():
        print(f"{resultado.modelo.name}: {resultado.documentos} docs, cStat "
              f"{resultado.ultimo_cstat}, próxima consulta em {resultado.proxima_consulta_em_s}s")

    hoje = dt.date.today()
    filtro = FiltroConsulta(data_inicial=hoje - dt.timedelta(days=30), data_final=hoje,
                            modelos=[ModeloDocumento.NFE, ModeloDocumento.CTE],
                            direcao=Direcao.RECEBIDOS)

    for documento in dfe.consultar(filtro):
        print(documento.chave, documento.emitente_nome, documento.valor_total,
              "XML completo" if documento.xml_completo else "somente resumo")

    dfe.consultar(filtro, formato="xlsx", destino="./saida/dfe_30_dias.xlsx")
    dfe.baixar_xmls(filtro, destino="./saida/xml")
    resultado_pdf = dfe.gerar_pdfs(filtro, destino="./saida/pdf")
    print(f"PDFs: {len(resultado_pdf.gerados)} gerados, {len(resultado_pdf.falhas)} falhas")

    pendentes = dfe.pendentes_manifestacao()
    print(f"{len(pendentes)} NF-e aguardando Ciência para liberar o XML completo.")
    # Manifestação é ato do contribuinte: só execute com autorização formal do cliente.
    # dfe.manifestar_ciencia([p.chave for p in pendentes])


if __name__ == "__main__":
    main()
