"""Smoke test da NFS-e Nacional na PRODUÇÃO RESTRITA (único script que acessa a rede).

Valida, sem gerar nota fiscal:
    1. mTLS com o certificado A1 e a rota da Sefin (``/SefinNacional`` x ``/API/SefinNacional``);
    2. convênio do município;
    3. o perfil de assinatura (Exclusive C14N + RSA-SHA256): envia uma DPS com **defeito
       proposital** que só é detectado DEPOIS da verificação da assinatura (opção de
       ``totTrib`` incompatível com o regime, E0712). Se a Sefin devolver uma rejeição de
       negócio (E07xx), a assinatura foi aceita. E0714, E0715, E1228, E1235, E4007 e E999 indicam
       problema de assinatura, de schema ou de rota.

Variáveis de ambiente:
    SEFAZ_PFX, SEFAZ_PFX_SENHA   certificado A1 e senha (nunca por argumento de linha de comando)
    FREEDFE_CNPJ                 CNPJ do prestador (titular do certificado)
    FREEDFE_MUNICIPIO            código IBGE do município emissor (7 dígitos)
    FREEDFE_IM                   inscrição municipal (opcional)

Recusa-se a rodar em produção: usa sempre ``tpAmb=2``.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from decimal import Decimal

from freedfe import (
    Ambiente,
    CertificadoA1,
    ConfiguracaoNfseLocal,
    Dps,
    FreeDFe,
    OpcaoSimplesNacional,
    Prestador,
    RejeicaoNfseErro,
    Servico,
    TotTrib,
    TribMunicipal,
    Valores,
)
from freedfe.excecoes import ComunicacaoErro, FreeDFeErro
from freedfe.servicos import configuracao_nfse
from freedfe.tipos import FUSO_BRASILIA
from freedfe.transporte import ClienteRest

ERROS_DE_ASSINATURA_OU_ROTA = {"E0714", "E0715", "E1228", "E1235", "E4007", "E999"}


def hoje_em_brasilia() -> dt.date:
    """Data de hoje no fuso de Brasília: a competência não pode passar da data de emissão."""
    return dt.datetime.now(FUSO_BRASILIA).date()


def sondar_rotas() -> str | None:
    """Testa as duas rotas (``HEAD /dps/{id}`` de uma DPS inexistente). Devolve o prefixo útil."""
    transporte = ClienteRest(CertificadoA1.de_arquivo(
        os.environ["SEFAZ_PFX"], os.environ["SEFAZ_PFX_SENHA"]))  # o mesmo mTLS da emissão
    id_fantasma = "DPS" + os.environ["FREEDFE_MUNICIPIO"] + "2" + os.environ["FREEDFE_CNPJ"] \
        + "99999" + "000000000000001"
    util = None
    for prefixo in ("", "/API"):
        base = configuracao_nfse(Ambiente.HOMOLOGACAO, prefixo).base_sefin
        try:
            resposta = transporte.requisitar("HEAD", f"{base}/dps/{id_fantasma}", timeout=30)
            print(f"  rota {base}: HTTP {resposta.status}")
            if resposta.status in (200, 404) and util is None:
                util = prefixo
        except ComunicacaoErro as erro:
            print(f"  rota {base}: falha de rede/TLS ({erro})")
    return util


def montar_dps_com_defeito(cnpj: str, municipio: str, inscricao_municipal: str | None) -> Dps:
    """Prestador NÃO optante do Simples com ``pTotTribSN`` (exclusivo do Simples): E0712."""
    return Dps(
        serie=99999, data_competencia=hoje_em_brasilia(), codigo_municipio_emissor=municipio,
        prestador=Prestador(cnpj=cnpj, inscricao_municipal=inscricao_municipal,
                            opcao_simples=OpcaoSimplesNacional.NAO_OPTANTE),
        servico=Servico("010101", "Teste de assinatura (DPS com defeito proposital)",
                        codigo_municipio_prestacao=municipio),
        valores=Valores(Decimal("1.00")), trib_municipal=TribMunicipal(),
        tot_trib=TotTrib.simples("6.00"))


def main() -> None:
    """Roda as três verificações e imprime o veredito."""
    cnpj, municipio = os.environ["FREEDFE_CNPJ"], os.environ["FREEDFE_MUNICIPIO"]
    with tempfile.TemporaryDirectory(prefix="freedfe_smoke_") as pasta:
        dfe = FreeDFe(os.environ["SEFAZ_PFX"], os.environ["SEFAZ_PFX_SENHA"], cnpj=cnpj,
                      ambiente=Ambiente.HOMOLOGACAO, pasta=pasta)
        info = dfe.info_certificado()
        print(f"Certificado: {info.titular} (vence em {info.dias_para_vencer} dias)")

        print("1. Rotas da Sefin (mTLS):")
        prefixo = sondar_rotas()
        if prefixo is None:
            print("   Nenhuma rota respondeu 200/404: confira o certificado, a cadeia (ca_bundle) "
                  "e a rede.")
            return
        print(f"   Use prefixo_api={prefixo!r} em ConfiguracaoNfseLocal.")
        if prefixo:
            dfe = FreeDFe(os.environ["SEFAZ_PFX"], os.environ["SEFAZ_PFX_SENHA"], cnpj=cnpj,
                          ambiente=Ambiente.HOMOLOGACAO, pasta=pasta,
                          config_nfse=ConfiguracaoNfseLocal(prefixo_api=prefixo))

        print("2. Convênio do município:")
        try:
            convenio = dfe.nfse.convenio(municipio)
            print(f"   aderente={convenio.aderente}, ativação={convenio.data_ativacao}")
            print(f"   resposta bruta: {convenio.bruto}")
        except FreeDFeErro as erro:
            print(f"   indisponível ({erro}); a rota de parametrização pode ter mudado.")

        print("3. Perfil de assinatura (DPS com defeito proposital):")
        try:
            nota = dfe.nfse.emitir(montar_dps_com_defeito(
                cnpj, municipio, os.environ.get("FREEDFE_IM")))
            print(f"   ATENÇÃO: a Sefin AUTORIZOU a nota {nota.chave} (produção restrita, sem "
                  "validade). O defeito não foi detectado; a assinatura foi aceita.")
        except RejeicaoNfseErro as erro:
            ruins = ERROS_DE_ASSINATURA_OU_ROTA.intersection(erro.codigos)
            for item in erro.erros:
                print(f"   {item.codigo}: {item.descricao} ({item.complemento})")
            if ruins:
                print(f"   FALHA: {sorted(ruins)} indica assinatura, schema ou rota. Gere a DPS "
                      "com gerar_dps_assinada() e compare com o XSD (pasta_xsd).")
            else:
                print("   OK: rejeição de negócio depois da verificação da assinatura.")


if __name__ == "__main__":
    main()
