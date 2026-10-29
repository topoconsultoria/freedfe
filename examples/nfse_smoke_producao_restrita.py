"""Smoke test da NFS-e Nacional na PRODUÇÃO RESTRITA (único script que acessa a rede).

Valida, sem gerar nota fiscal:
    1. mTLS com o certificado A1 e a rota da Sefin (``/SefinNacional`` x ``/API/SefinNacional``);
    2. convênio do município;
    3. o perfil de assinatura (Exclusive C14N + RSA-SHA256): envia uma DPS com **defeito
       proposital** que só é detectado DEPOIS da verificação da assinatura (opção de
       ``totTrib`` incompatível com o regime, E0712).

Veredito (``classificar_rejeicao``), estrito: só **E0712** prova que codificação, schema e
assinatura passaram. Outro E07xx é inconclusivo. Qualquer outro código é falha, inclusive
E1229 (XML sem declaração UTF-8), E0714/E0715 (assinatura) e E1235 (schema). O processo sai
com código 1 em falha ou resultado inconclusivo, para servir em CI.

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

CODIGO_ESPERADO = "E0712"  # defeito proposital: avaliado só depois da assinatura
ERROS_DE_ASSINATURA = frozenset({"E0714", "E0715"})


def classificar_rejeicao(codigos) -> str:
    """``"ok"``, ``"inconclusivo"`` ou ``"falha"`` para os códigos devolvidos pela Sefin.

    Só o defeito proposital (E0712) prova que a DPS passou por codificação, schema e
    assinatura. Uma lista de "códigos ruins" deixaria passar rejeições novas como sucesso.
    """
    codigos = set(codigos)
    if codigos & ERROS_DE_ASSINATURA:
        return "falha"
    if CODIGO_ESPERADO in codigos:
        return "ok"
    if any(codigo.startswith("E07") for codigo in codigos):
        return "inconclusivo"
    return "falha"


def hoje_em_brasilia() -> dt.date:
    """Data de hoje no fuso de Brasília: a competência não pode passar da data de emissão."""
    return dt.datetime.now(FUSO_BRASILIA).date()


def sondar_rotas() -> list[str]:
    """Testa as duas rotas (``HEAD /dps/{id}`` de uma DPS inexistente).

    Returns:
        Prefixos que responderam 200 ou 404, isto é, aceitaram o certificado. Em 29/10/2026 a
        produção restrita aceitou os dois (404 para a DPS inexistente).
    """
    transporte = ClienteRest(CertificadoA1.de_arquivo(
        os.environ["SEFAZ_PFX"], os.environ["SEFAZ_PFX_SENHA"]))  # o mesmo mTLS da emissão
    id_fantasma = "DPS" + os.environ["FREEDFE_MUNICIPIO"] + "2" + os.environ["FREEDFE_CNPJ"] \
        + "99999" + "000000000000001"
    uteis = []
    for prefixo in ("", "/API"):
        base = configuracao_nfse(Ambiente.HOMOLOGACAO, prefixo).base_sefin
        try:
            resposta = transporte.requisitar("HEAD", f"{base}/dps/{id_fantasma}", timeout=30)
            print(f"  rota {base}: HTTP {resposta.status}")
            if resposta.status in (200, 404):
                uteis.append(prefixo)
        except ComunicacaoErro as erro:
            print(f"  rota {base}: falha de rede/TLS ({erro})")
    return uteis


def montar_dps_com_defeito(cnpj: str, municipio: str, inscricao_municipal: str | None) -> Dps:
    """Prestador NÃO optante do Simples com ``pTotTribSN`` (exclusivo do Simples): E0712.

    A série 900 é válida nos dois leiautes (a NT 009 só aceita até 89999) e o ``nDPS`` vem do
    relógio, para nunca colidir com notas já emitidas no ambiente de testes (E0014).
    """
    return Dps(
        serie=900, numero=int(dt.datetime.now(FUSO_BRASILIA).strftime("%y%m%d%H%M%S")),
        data_competencia=hoje_em_brasilia(), codigo_municipio_emissor=municipio,
        prestador=Prestador(cnpj=cnpj, inscricao_municipal=inscricao_municipal,
                            opcao_simples=OpcaoSimplesNacional.NAO_OPTANTE),
        servico=Servico("010101", "Teste de assinatura (DPS com defeito proposital)",
                        codigo_municipio_prestacao=municipio),
        valores=Valores(Decimal("1.00")), trib_municipal=TribMunicipal(),
        tot_trib=TotTrib.simples("6.00"))


MENSAGENS = {
    "ok": "OK: E0712 confirma que codificação, schema e assinatura foram aceitos.",
    "inconclusivo": "INCONCLUSIVO: rejeição de negócio diferente da esperada (E0712). A "
                    "assinatura provavelmente passou, mas confira os códigos acima.",
    "falha": "FALHA: a DPS foi recusada antes da regra de negócio esperada. E1229 indica XML "
             "sem declaração UTF-8; E0714/E0715 indicam assinatura; E1235 indica schema. Gere a "
             "DPS com gerar_dps_assinada() e compare com o XSD (pasta_xsd).",
}


def main() -> int:
    """Roda as três verificações, imprime o veredito e devolve o código de saída."""
    cnpj, municipio = os.environ["FREEDFE_CNPJ"], os.environ["FREEDFE_MUNICIPIO"]
    with tempfile.TemporaryDirectory(prefix="freedfe_smoke_") as pasta:
        dfe = FreeDFe(os.environ["SEFAZ_PFX"], os.environ["SEFAZ_PFX_SENHA"], cnpj=cnpj,
                      ambiente=Ambiente.HOMOLOGACAO, pasta=pasta)
        info = dfe.info_certificado()
        print(f"Certificado: {info.titular} (vence em {info.dias_para_vencer} dias)")

        print("1. Rotas da Sefin (mTLS):")
        prefixos = sondar_rotas()
        if not prefixos:
            print("   FALHA: nenhuma rota respondeu 200/404. Confira o certificado, a cadeia "
                  "(ca_bundle) e a rede.")
            return 1
        print(f"   Prefixos que aceitaram o certificado: {prefixos}. "
              f"Usando prefixo_api={prefixos[0]!r}.")
        if prefixos[0]:
            dfe = FreeDFe(os.environ["SEFAZ_PFX"], os.environ["SEFAZ_PFX_SENHA"], cnpj=cnpj,
                          ambiente=Ambiente.HOMOLOGACAO, pasta=pasta,
                          config_nfse=ConfiguracaoNfseLocal(prefixo_api=prefixos[0]))

        print("2. Convênio do município:")
        try:
            convenio = dfe.nfse.convenio(municipio)
            print(f"   aderente={convenio.aderente}, "
                  f"emissor_nacional={convenio.aderente_emissor_nacional}, "
                  f"ativação={convenio.data_ativacao}")
            print(f"   resposta bruta: {convenio.bruto}")
        except FreeDFeErro as erro:
            print(f"   indisponível ({erro}); a rota de parametrização pode ter mudado.")

        print("3. Perfil de assinatura (DPS com defeito proposital):")
        try:
            nota = dfe.nfse.emitir(montar_dps_com_defeito(
                cnpj, municipio, os.environ.get("FREEDFE_IM")))
        except RejeicaoNfseErro as erro:
            for item in erro.erros:
                print(f"   {item.codigo}: {item.descricao} ({item.complemento})")
            veredito = classificar_rejeicao(erro.codigos)
            print(f"   {MENSAGENS[veredito]}")
            return 0 if veredito == "ok" else 1
        except FreeDFeErro as erro:
            print(f"   FALHA: a emissão não chegou a uma resposta de negócio ({erro}).")
            return 1
        print(f"   ATENÇÃO: a Sefin AUTORIZOU a nota {nota.chave} (produção restrita, sem "
              "validade). O defeito proposital não foi detectado; reveja o teste.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
