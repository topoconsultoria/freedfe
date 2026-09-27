"""Catálogo dos Web Services de Distribuição DF-e.

Base normativa (verificada nos portais oficiais em 27/09/2026):
    - NF-e : NT 2014.002 v1.40 - NFeDistribuicaoDFe, schema PL_NFeDistDFe_104 (versao 1.01)
    - CT-e : NT 2015.002 v1.05 - CTeDistribuicaoDFe, schema PL_CTeDistDFe_100 (versao 1.00)
    - MDF-e: NT 2015.002 v1.03 - MDFeDistribuicaoDFe, schema PL_MDFeDistDFe_100 (versao 1.00)

Quando mudar a versão do leiaute, atualize apenas este módulo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
