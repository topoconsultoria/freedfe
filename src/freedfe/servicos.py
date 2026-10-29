"""Catálogo dos Web Services de Distribuição DF-e.

Base normativa (verificada nos portais oficiais em 27/09/2026):
    - NF-e : NT 2014.002 v1.40 - NFeDistribuicaoDFe, schema PL_NFeDistDFe_104 (versao 1.01)
    - CT-e : NT 2015.002 v1.05 - CTeDistribuicaoDFe, schema PL_CTeDistDFe_100 (versao 1.00)
    - MDF-e: NT 2015.002 v1.03 - MDFeDistribuicaoDFe, schema PL_MDFeDistDFe_100 (versao 1.00)

Quando mudar a versão do leiaute, atualize apenas este módulo.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from freedfe.tipos import Ambiente, ModeloDocumento


@dataclass(frozen=True)
class ConfiguracaoServico:
    """Metadados SOAP e regras de um serviço de distribuição."""

    modelo: ModeloDocumento
    endpoints: dict[Ambiente, str]
    ns_dados: str
    ns_wsdl: str
    metodo: str
    tag_dados: str
    versao: str
    tag_cabecalho: str | None
    exige_uf: bool
    aceita_cuf_autor: bool
    aceita_consulta_chave: bool
    janela_dias: int
    cstat_consumo_indevido: frozenset[str] = field(default_factory=frozenset)

    def url(self, ambiente: Ambiente) -> str:
        """Endpoint do serviço no ambiente informado."""
        return self.endpoints[Ambiente(ambiente)]

    @property
    def acao_soap(self) -> str:
        """Valor do parâmetro ``action`` do Content-Type SOAP 1.2."""
        return f"{self.ns_wsdl}/{self.metodo}"


SERVICOS_DISTRIBUICAO: dict[ModeloDocumento, ConfiguracaoServico] = {
    # NF-e: SOAP 1.2 sem header (exemplos da NT 2014.002 item 5).
    ModeloDocumento.NFE: ConfiguracaoServico(
        modelo=ModeloDocumento.NFE,
        endpoints={
            Ambiente.PRODUCAO:
                "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
            Ambiente.HOMOLOGACAO:
                "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
        },
        ns_dados="http://www.portalfiscal.inf.br/nfe",
        ns_wsdl="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe",
        metodo="nfeDistDFeInteresse",
        tag_dados="nfeDadosMsg",
        versao="1.01",
        tag_cabecalho=None,
        exige_uf=False,
        aceita_cuf_autor=True,
        aceita_consulta_chave=True,
        janela_dias=90,
        cstat_consumo_indevido=frozenset({"656"}),
    ),
    # CT-e: padrão análogo ao da NF-e. HIPÓTESE: a NT 2015.002 não traz exemplo de
    # envelope; confirmar o namespace no ?wsdl (com certificado) antes da produção.
    ModeloDocumento.CTE: ConfiguracaoServico(
        modelo=ModeloDocumento.CTE,
        endpoints={
            Ambiente.PRODUCAO:
                "https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx",
            Ambiente.HOMOLOGACAO:
                "https://hom1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx",
        },
        ns_dados="http://www.portalfiscal.inf.br/cte",
        ns_wsdl="http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe",
        metodo="cteDistDFeInteresse",
        tag_dados="cteDadosMsg",
        versao="1.00",
        tag_cabecalho=None,
        exige_uf=True,
        aceita_cuf_autor=True,
        aceita_consulta_chave=False,
        janela_dias=90,  # "até 3 meses" na NT
        cstat_consumo_indevido=frozenset({"656"}),
    ),
    # MDF-e: SOAP 1.2 com header mdfeCabecMsg (cUF, versaoDados); sem cUFAutor no corpo.
    ModeloDocumento.MDFE: ConfiguracaoServico(
        modelo=ModeloDocumento.MDFE,
        endpoints={
            Ambiente.PRODUCAO:
                "https://mdfe.svrs.rs.gov.br/ws/MDFeDistribuicaoDFe/MDFeDistribuicaoDFe.asmx",
            Ambiente.HOMOLOGACAO:
                "https://mdfe-homologacao.svrs.rs.gov.br/ws/MDFeDistribuicaoDFe/"
                "MDFeDistribuicaoDFe.asmx",
        },
        ns_dados="http://www.portalfiscal.inf.br/mdfe",
        ns_wsdl="http://www.portalfiscal.inf.br/mdfe/wsdl/MDFeDistribuicaoDFe",
        metodo="mdfeDistDFeInteresse",
        tag_dados="mdfeDadosMsg",
        versao="1.00",
        tag_cabecalho="mdfeCabecMsg",
        exige_uf=True,
        aceita_cuf_autor=False,
        aceita_consulta_chave=False,
        janela_dias=180,  # "até 6 meses" na NT
        cstat_consumo_indevido=frozenset({"678"}),
    ),
}


def configuracao_distribuicao(modelo: ModeloDocumento | str) -> ConfiguracaoServico:
    """Configuração do serviço de distribuição para o modelo informado."""
    return SERVICOS_DISTRIBUICAO[ModeloDocumento.normalizar(modelo)]


# Manifestação do Destinatário (NT 2020.001): NFeRecepcaoEvento4 no Ambiente Nacional.
ENDPOINTS_EVENTO_AN = {
    Ambiente.PRODUCAO: "https://www.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx",
    Ambiente.HOMOLOGACAO:
        "https://hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx",
}
NS_WSDL_EVENTO = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4"


# ---------------------------------------------------------------------------
# NFS-e Nacional (Sefin Nacional / ADN) - REST/JSON com mTLS
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfiguracaoNfse:
    """URLs e prazos da API da NFS-e Nacional em um ambiente.

    Base normativa e operacional (relatório técnico de out/2026):
        - Leiaute em produção: NT 004 v2.0 (XSD 1.01) até 30/11/2026; NT 009 v1.01 a partir
          de 01/12/2026. A produção restrita já roda a NT 009 desde 20/10/2026.
        - Manual Contribuintes - Emissor Público API v1.2 e Manual ADN v1.0.
        - A API de geração do DANFSe foi suspensa (NT 008 v1.02); o PDF é gerado localmente.

    As rotas mudam sem versionamento (parâmetros e DANFSe migraram para o host ADN), por
    isso tudo fica centralizado aqui e pode ser substituído com :meth:`com`.

    Observado em 29/10/2026 sem certificado (``tests/test_rede_real.py``, opt-in):
        - Sefin de **produção** só monta ``/SefinNacional`` (403 sem ICP-Brasil); o caminho
          ``/API/SefinNacional`` devolve 404. Em produção use ``prefixo_api=""``.
        - Sefin de **produção restrita** monta ``/SefinNacional`` e ``/API/SefinNacional`` (ambos
          403 sem ICP-Brasil).
        - ADN e parametrização recusam certificado cliente não ICP-Brasil já no handshake TLS.

    Observado em 29/10/2026 **com o A1 real** na produção restrita (smoke test):
        - os dois prefixos aceitam o certificado e devolvem 404 para ``HEAD /dps/{id}`` de DPS
          inexistente; o padrão sem prefixo está correto;
        - o convênio responde em ``base_parametrizacao`` (host ADN).

    HIPÓTESE a confirmar:
        - ``modelo_url_consulta_publica``: a NT 008 v1.02 (item 2.4.3) fixa
          ``https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave=<chave>``. O endereço da
          produção restrita usa o mesmo caminho no host ``www.producaorestrita.nfse.gov.br``
          (a página existe; a NT não o cita).
    """

    ambiente: Ambiente
    host_sefin: str
    base_adn_contribuintes: str
    base_parametrizacao: str
    modelo_url_consulta_publica: str
    caminho_sefin: str = "/SefinNacional"
    prefixo_api: str = ""
    timeout_emissao: int = 60
    timeout_consulta: int = 30

    @property
    def base_sefin(self) -> str:
        """URL base da Sefin Nacional (host + prefixo opcional + ``/SefinNacional``)."""
        return f"{self.host_sefin}{self.prefixo_api}{self.caminho_sefin}"

    def url_sefin(self, caminho: str) -> str:
        """URL absoluta de uma rota da Sefin (``caminho`` começa com ``/``)."""
        return f"{self.base_sefin}{caminho}"

    def url_adn(self, caminho: str) -> str:
        """URL absoluta de uma rota do ADN Contribuintes."""
        return f"{self.base_adn_contribuintes}{caminho}"

    def url_parametrizacao(self, caminho: str) -> str:
        """URL absoluta de uma rota de parametrização municipal (host ADN)."""
        return f"{self.base_parametrizacao}{caminho}"

    def url_consulta_publica(self, chave: str) -> str:
        """URL da consulta pública da NFS-e, gravada no QR Code do DANFSe."""
        return self.modelo_url_consulta_publica.format(chave=chave)

    def com(self, **alteracoes) -> ConfiguracaoNfse:
        """Cópia com campos alterados (ex.: ``cfg.com(prefixo_api="/API")``)."""
        return replace(self, **alteracoes)


_CONFIGURACOES_NFSE = {
    Ambiente.PRODUCAO: ConfiguracaoNfse(
        ambiente=Ambiente.PRODUCAO,
        host_sefin="https://sefin.nfse.gov.br",
        base_adn_contribuintes="https://adn.nfse.gov.br/contribuintes",
        base_parametrizacao="https://adn.nfse.gov.br/parametrizacao",
        modelo_url_consulta_publica="https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}",
    ),
    Ambiente.HOMOLOGACAO: ConfiguracaoNfse(
        ambiente=Ambiente.HOMOLOGACAO,
        host_sefin="https://sefin.producaorestrita.nfse.gov.br",
        base_adn_contribuintes="https://adn.producaorestrita.nfse.gov.br/contribuintes",
        base_parametrizacao="https://adn.producaorestrita.nfse.gov.br/parametrizacao",
        modelo_url_consulta_publica=(
            "https://www.producaorestrita.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}"),
    ),
}


def configuracao_nfse(ambiente: Ambiente | int, prefixo_api: str = "") -> ConfiguracaoNfse:
    """Configuração da NFS-e Nacional para o ambiente (``HOMOLOGACAO`` = produção restrita)."""
    configuracao = _CONFIGURACOES_NFSE[Ambiente(ambiente)]
    return configuracao.com(prefixo_api=prefixo_api) if prefixo_api else configuracao
