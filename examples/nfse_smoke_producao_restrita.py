"""Smoke test da NFS-e Nacional na PRODUÇÃO RESTRITA (único script que acessa a rede).

Valida, sem gerar nota fiscal:
    1. mTLS com o certificado A1 e a rota da Sefin (``/SefinNacional`` x ``/API/SefinNacional``);
    2. convênio do município;
    3. o perfil de assinatura (Exclusive C14N + RSA-SHA256): envia uma DPS com **defeito
       proposital** que só é detectado DEPOIS da verificação da assinatura (opção de
       ``totTrib`` incompatível com o regime, E0712).

Veredito (``classificar_rejeicao``), estrito: só **E0712** prova que codificação, schema e
assinatura passaram. Outro E07xx é inconclusivo. Rejeição de **cadastro** do prestador (E0084,
E0099, E0119, E0124, E0160, E0037) é avaliada antes do E0712 e impede a prova: corrija o
município, a inscrição municipal ou o CNC NFS-e e rode de novo (código de saída 2). Nesse caso o
script ainda tenta a **prova por contraste**: envia a mesma DPS com a assinatura adulterada. Se a
adulterada volta E0714/E0715 e a correta voltou regra de cadastro, a Sefin confere a assinatura
antes do cadastro e a assinatura correta foi aceita (código de saída 0). Qualquer outro
código é falha, inclusive E1229 (XML sem declaração UTF-8), E0714/E0715 (assinatura) e E1235
(schema). O processo sai com código 1 em falha ou resultado inconclusivo, para servir em CI.

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
from dataclasses import replace
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
# Pré-requisitos cadastrais checados antes do E0712 (Anexo I): CNPJ/CPF sem estabelecimento no
# município emissor (E0084/E0099), IM não autorizada, inativa ou sem cadastro complementar
# (E0119/E0124/E0120), Simples (E0160)
# e município sem convênio (E0037).
# E0120: IM informada sem informações complementares do prestador no CNC NFS-e do município.
ERROS_DE_CADASTRO = frozenset({"E0084", "E0099", "E0119", "E0120", "E0124", "E0160", "E0037"})
SAIDAS = {"ok": 0, "cadastro": 2}


def classificar_rejeicao(codigos) -> str:
    """``"ok"``, ``"cadastro"``, ``"inconclusivo"`` ou ``"falha"`` para os códigos da Sefin.

    Só o defeito proposital (E0712) prova que a DPS passou por codificação, schema e
    assinatura. Uma lista de "códigos ruins" deixaria passar rejeições novas como sucesso.
    """
    codigos = set(codigos)
    if codigos & ERROS_DE_ASSINATURA:
        return "falha"
    if CODIGO_ESPERADO in codigos:
        return "ok"
    if codigos and codigos <= ERROS_DE_CADASTRO:
        return "cadastro"
    if any(codigo.startswith("E07") for codigo in codigos):
        return "inconclusivo"
    return "falha"


def adulterar_assinatura(xml: bytes) -> bytes:
    """Troca o 1º caractere do ``SignatureValue``: XML e schema válidos, assinatura inválida."""
    marca = b"<SignatureValue>"
    inicio = xml.index(marca) + len(marca)
    trocado = b"B" if xml[inicio:inicio + 1] == b"A" else b"A"
    return xml[:inicio] + trocado + xml[inicio + 1:]


def classificar_contraste(codigos_correta, codigos_adulterada) -> str:
    """``"assinatura_aceita"`` quando só a DPS adulterada é barrada pela assinatura.

    Prova por contraste: se a Sefin devolve E0714/E0715 para a assinatura adulterada e uma
    regra de cadastro para a correta, a assinatura é conferida antes do cadastro e a correta
    passou. Qualquer outro par de respostas é ``"inconclusivo"``.
    """
    if (classificar_rejeicao(codigos_correta) == "cadastro"
            and set(codigos_adulterada) & ERROS_DE_ASSINATURA):
        return "assinatura_aceita"
    return "inconclusivo"


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
    "cadastro": "CADASTRO: a DPS passou pela codificação (sem E1229) e parou em regra cadastral do "
                "prestador, avaliada antes do E0712. Use em FREEDFE_MUNICIPIO o código IBGE do "
                "município do estabelecimento do CNPJ, informe FREEDFE_IM e confirme o cadastro "
                "no CNC NFS-e desse município. A assinatura ainda não foi provada.",
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
        print(f"   município emissor={municipio}, inscrição municipal "
              f"{'informada' if os.environ.get('FREEDFE_IM') else 'NÃO informada'}")
        dps = montar_dps_com_defeito(cnpj, municipio, os.environ.get("FREEDFE_IM"))
        try:
            nota = dfe.nfse.emitir(dps)
        except RejeicaoNfseErro as erro:
            for item in erro.erros:
                print(f"   {item.codigo}: {item.descricao} ({item.complemento})")
            veredito = classificar_rejeicao(erro.codigos)
            print(f"   {MENSAGENS[veredito]}")
            if veredito == "cadastro":
                return provar_por_contraste(dfe, dps, erro.codigos)
            return SAIDAS.get(veredito, 1)
        except FreeDFeErro as erro:
            print(f"   FALHA: a emissão não chegou a uma resposta de negócio ({erro}).")
            return 1
        print(f"   ATENÇÃO: a Sefin AUTORIZOU a nota {nota.chave} (produção restrita, sem "
              "validade). O defeito proposital não foi detectado; reveja o teste.")
        return 1


def provar_por_contraste(dfe: FreeDFe, dps: Dps, codigos_correta) -> int:
    """Envia a mesma DPS, com outro número, de assinatura adulterada, e compara as respostas."""
    print("4. Prova por contraste (mesma DPS com a assinatura adulterada):")
    adulterada = adulterar_assinatura(
        dfe.nfse.gerar_dps_assinada(replace(dps, numero=dps.numero + 1)))
    try:
        dfe.nfse.emitir(adulterada)
    except RejeicaoNfseErro as erro:
        for item in erro.erros:
            print(f"   {item.codigo}: {item.descricao} ({item.complemento})")
        if classificar_contraste(codigos_correta, erro.codigos) == "assinatura_aceita":
            print("   ASSINATURA ACEITA: a adulterada foi barrada pela assinatura e a correta só "
                  "pelo cadastro. Regularize o cadastro do prestador para emitir de fato.")
            return 0
        print("   INCONCLUSIVO: a Sefin não barrou a adulterada pela assinatura antes do "
              "cadastro; a ordem das regras não permite concluir.")
        return 2
    except FreeDFeErro as erro:
        print(f"   INCONCLUSIVO: o contraste não chegou a uma resposta de negócio ({erro}).")
        return 2
    print("   FALHA: a Sefin AUTORIZOU uma DPS com assinatura adulterada; reveja o teste.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
