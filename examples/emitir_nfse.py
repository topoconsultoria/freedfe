"""Exemplo: emitir NFS-e Nacional, gerar o DANFSe e (opcionalmente) cancelar.

Variáveis de ambiente:
    SEFAZ_PFX            caminho do certificado A1 (.pfx) do prestador
    SEFAZ_PFX_SENHA      senha do certificado (nunca por argumento de linha de comando)
    FREEDFE_CNPJ         CNPJ do prestador
    FREEDFE_MUNICIPIO    código IBGE do município emissor (7 dígitos)
    FREEDFE_IM           inscrição municipal do prestador (opcional)
    FREEDFE_AMB          1 = produção, 2 = produção restrita (padrão: 2)
    FREEDFE_SERIE        série da DPS, exclusiva do ERP (padrão: 900)
    FREEDFE_ENVIAR       "1" para enviar de fato; sem isso só confere a DPS assinada
    FREEDFE_CANCELAR     "1" para cancelar a nota emitida logo em seguida (teste)

Execute primeiro em produção restrita, que não tem validade fiscal.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from decimal import Decimal

from freedfe import (
    Ambiente,
    Dps,
    EmissaoIncertaErro,
    FreeDFe,
    MotivoCancelamento,
    OpcaoSimplesNacional,
    Prestador,
    RegimeApuracaoSimples,
    RejeicaoNfseErro,
    Servico,
    Tomador,
    TotTrib,
    TribMunicipal,
    Valores,
)
from freedfe.tipos import FUSO_BRASILIA


def hoje_em_brasilia() -> dt.date:
    """Data de hoje no fuso de Brasília: a competência não pode passar da data de emissão."""
    return dt.datetime.now(FUSO_BRASILIA).date()


def criar_cliente() -> FreeDFe:
    """Monta o cliente a partir das variáveis de ambiente."""
    return FreeDFe(
        os.environ["SEFAZ_PFX"],
        os.environ["SEFAZ_PFX_SENHA"],
        cnpj=os.environ["FREEDFE_CNPJ"],
        ambiente=Ambiente(int(os.environ.get("FREEDFE_AMB", "2"))),
        pasta="./dados_dfe",
    )


def montar_dps(cnpj: str, municipio: str, inscricao_municipal: str | None = None,
               serie: int = 900) -> Dps:
    """DPS de exemplo: ME/EPP optante do Simples, serviço de TI, tomador pessoa jurídica.

    Ajuste ``cTribNac``, NBS, alíquotas e o regime ao cadastro real do prestador: o regime
    do Simples define qual opção de ``totTrib`` a Sefin aceita (E0710, E0712, E0713).
    O número (``nDPS``) é omitido: a biblioteca numera por série e protege contra duplicidade.
    """
    return Dps(
        serie=serie,
        data_competencia=hoje_em_brasilia(),
        codigo_municipio_emissor=municipio,
        prestador=Prestador(
            cnpj=cnpj, inscricao_municipal=inscricao_municipal,
            opcao_simples=OpcaoSimplesNacional.ME_EPP,
            regime_apuracao_simples=RegimeApuracaoSimples.FEDERAIS_E_MUNICIPAL_PELO_SIMPLES),
        tomador=Tomador(cnpj="11444777000161", nome="EMPRESA TOMADORA DE TESTE LTDA"),
        servico=Servico("010101", "Desenvolvimento de sistema sob encomenda",
                        codigo_municipio_prestacao=municipio),
        valores=Valores(Decimal("100.00")),
        trib_municipal=TribMunicipal(),
        tot_trib=TotTrib.simples("6.00"),
    )


def main() -> None:
    """Confere a DPS e, com ``FREEDFE_ENVIAR=1``, emite, gera o DANFSe e cancela."""
    logging.basicConfig(level=logging.INFO)
    dfe = criar_cliente()
    print(f"Certificado: {dfe.info_certificado().titular}")

    dps = montar_dps(os.environ["FREEDFE_CNPJ"], os.environ["FREEDFE_MUNICIPIO"],
                     os.environ.get("FREEDFE_IM"), int(os.environ.get("FREEDFE_SERIE", "900")))

    # Convênio do município (cache de 24 h): evita a rejeição E0037.
    convenio = dfe.nfse.convenio(dps.codigo_municipio_emissor)
    print(f"Município {convenio.codigo_municipio}: aderente={convenio.aderente}, "
          f"ativação={convenio.data_ativacao}")

    assinada = dfe.nfse.gerar_dps_assinada(dps)
    print(f"DPS assinada ({len(assinada)} bytes) conferida; nada foi enviado.")
    if os.environ.get("FREEDFE_ENVIAR") != "1":
        print("Defina FREEDFE_ENVIAR=1 para emitir.")
        return

    try:
        nota = dfe.nfse.emitir(dps, verificar_parametros=True)
    except RejeicaoNfseErro as erro:
        # Rejeição de negócio: não repita sem corrigir. O complemento aponta o campo do XML.
        for item in erro.erros:
            print(f"Rejeitada: {item.codigo} - {item.descricao} ({item.complemento})")
        return
    except EmissaoIncertaErro as erro:
        # Timeout/5xx: a Sefin pode ter processado a DPS. Nunca reenvie à mão.
        print(f"Resultado incerto para {erro.id_dps}; reconciliando na Sefin...")
        nota = dfe.nfse.reconciliar(erro.id_dps)
        if nota is None:
            print("A Sefin não recebeu a DPS; chame emitir() de novo com o mesmo número.")
            return

    print(f"NFS-e autorizada: {nota.chave}")
    print(f"DANFSe: {dfe.nfse.gerar_danfse(nota.chave)}")

    if os.environ.get("FREEDFE_CANCELAR") == "1":
        evento = dfe.nfse.cancelar(nota.chave, MotivoCancelamento.ERRO_NA_EMISSAO,
                                   "Cancelamento de teste em produção restrita")
        print(f"Cancelamento registrado (HTTP {evento.status_http}).")

    # Notas e eventos feitos fora do ERP (portal, ofício), pelo ADN:
    for resultado in dfe.sincronizar(["nfse"]):
        print(f"ADN: {resultado.documentos} documentos, último NSU {resultado.ult_nsu}")


if __name__ == "__main__":
    main()
