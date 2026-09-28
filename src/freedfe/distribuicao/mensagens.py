"""Montagem das mensagens ``distDFeInt`` e do envelope SOAP 1.2."""

from __future__ import annotations

from freedfe.excecoes import ValidacaoErro
from freedfe.interessado import Interessado
from freedfe.servicos import ConfiguracaoServico
from freedfe.tipos import Ambiente
from freedfe.transporte import SOAP12_NS
from freedfe.validadores import exigir_chave, formatar_nsu

TAMANHO_MAXIMO_DADOS = 10 * 1024  # rejeição 214 acima disso


class MontadorDistribuicao:
    """Monta a área de dados e o envelope de um serviço de distribuição.

    A área de dados é gerada sem declaração XML e sem espaços entre tags,
    como exigem os schemas da SEFAZ.
    """

    def __init__(self, configuracao: ConfiguracaoServico, ambiente: Ambiente):
        self._cfg = configuracao
        self._ambiente = Ambiente(ambiente)

    def distribuicao_nsu(self, interessado: Interessado, ult_nsu: str | int) -> str:
        """Modo ``distNSU``: documentos com NSU maior que ``ult_nsu`` (até 50)."""
        grupo = f"<distNSU><ultNSU>{formatar_nsu(ult_nsu)}</ultNSU></distNSU>"
        return self._dist_dfe_int(interessado, grupo)

    def consulta_nsu(self, interessado: Interessado, nsu: str | int) -> str:
        """Modo ``consNSU``: um único documento (preenchimento de lacuna)."""
        grupo = f"<consNSU><NSU>{formatar_nsu(nsu)}</NSU></consNSU>"
        return self._dist_dfe_int(interessado, grupo)

    def consulta_chave(self, interessado: Interessado, chave: str) -> str:
        """Modo ``consChNFe``: somente NF-e, pela chave de acesso."""
        if not self._cfg.aceita_consulta_chave:
            raise ValidacaoErro("Consulta por chave existe apenas na distribuição da NF-e.")
        grupo = f"<consChNFe><chNFe>{exigir_chave(chave)}</chNFe></consChNFe>"
        return self._dist_dfe_int(interessado, grupo)

    def envelope(self, dados_xml: str, uf: str | None = None) -> bytes:
        """Envolve a área de dados no envelope SOAP 1.2."""
        cabecalho = self._cabecalho(uf)
        envelope = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                    'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
                    f'xmlns:soap12="{SOAP12_NS}">'
                    f"{cabecalho}<soap12:Body>{self._corpo(dados_xml)}</soap12:Body>"
                    "</soap12:Envelope>")
        return envelope.encode("utf-8")

    def _dist_dfe_int(self, interessado: Interessado, grupo_consulta: str) -> str:
        self._validar_uf(interessado)
        partes = [f'<distDFeInt xmlns="{self._cfg.ns_dados}" versao="{self._cfg.versao}">',
                  f"<tpAmb>{int(self._ambiente)}</tpAmb>"]
        # MDF-e não possui cUFAutor no distDFeInt (a UF vai no header SOAP).
        if interessado.uf and self._cfg.aceita_cuf_autor:
            partes.append(f"<cUFAutor>{interessado.uf}</cUFAutor>")
        partes.append(interessado.elemento_xml())
        partes.append(grupo_consulta)
        partes.append("</distDFeInt>")
        xml = "".join(partes)
        if len(xml.encode("utf-8")) > TAMANHO_MAXIMO_DADOS:
            raise ValidacaoErro("Área de dados excede 10 KB (rejeição 214).")
        return xml

    def _validar_uf(self, interessado: Interessado) -> None:
        if self._cfg.exige_uf and not interessado.uf:
            raise ValidacaoErro(
                f"{self._cfg.modelo.name} exige a UF do autor (código IBGE) no interessado.")

    def _cabecalho(self, uf: str | None) -> str:
        if not self._cfg.tag_cabecalho:
            return ""
        if not uf:
            raise ValidacaoErro(f"{self._cfg.modelo.name} exige cUF no header SOAP.")
        tag, ns = self._cfg.tag_cabecalho, self._cfg.ns_wsdl
        return (f'<soap12:Header><{tag} xmlns="{ns}"><cUF>{uf}</cUF>'
                f"<versaoDados>{self._cfg.versao}</versaoDados></{tag}></soap12:Header>")

    def _corpo(self, dados_xml: str) -> str:
        tag, ns = self._cfg.tag_dados, self._cfg.ns_wsdl
        if self._cfg.tag_cabecalho:
            return f'<{tag} xmlns="{ns}">{dados_xml}</{tag}>'
        metodo = self._cfg.metodo
        return f'<{metodo} xmlns="{ns}"><{tag}>{dados_xml}</{tag}></{metodo}>'
