"""API pública da biblioteca: a classe ``FreeDFe``."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from freedfe.auditoria import RegistroAuditoria
from freedfe.certificado import CertificadoA1, InfoCertificado
from freedfe.distribuicao.cliente import ClienteDistribuicao, ResultadoSincronizacao
from freedfe.distribuicao.estado import EstadoNSU, Relogio, relogio_utc
from freedfe.distribuicao.retorno import RetornoDistribuicao
from freedfe.distribuicao.trava import TravaArquivo
from freedfe.documentos.resumo import ResumoDocumento
from freedfe.excecoes import CertificadoErro, FreeDFeErro, ValidacaoErro
from freedfe.exportacao.fabrica import FabricaExportador
from freedfe.exportacao.formato import FormatoSaida
from freedfe.interessado import Interessado
from freedfe.manifestacao.assinatura import AssinadorXml
from freedfe.manifestacao.cliente import ClienteManifestacao, RetornoManifestacao
from freedfe.pdf.gerador import GeradorPdf, GeradorPdfBrazilFiscalReport
from freedfe.repositorio.arquivos import RepositorioArquivos
from freedfe.repositorio.base import RepositorioDocumentos, ResultadoArmazenamento
from freedfe.repositorio.filtro import Direcao, FiltroConsulta
from freedfe.repositorio.importador import ImportadorXml
from freedfe.servicos import configuracao_distribuicao
from freedfe.tipos import Ambiente, ModeloDocumento, SituacaoDocumento
from freedfe.transporte import ClienteSoap, Transporte

logger = logging.getLogger(__name__)


@dataclass
class ResultadoGeracaoPdf:
    """PDFs gerados e falhas por chave (a geração continua após uma falha)."""

    gerados: list[Path] = field(default_factory=list)
    falhas: dict[str, str] = field(default_factory=dict)


class FreeDFe:
    """Consulta, download de XML e geração de PDF de DF-e de interesse de um CNPJ/CPF.

    Fluxo:
        1. ``sincronizar()`` consome o Web Service de Distribuição DF-e (por NSU) e
           grava os XMLs no repositório local. É a única operação de distribuição
           que acessa a SEFAZ; agende-a a cada 60-65 minutos.
        2. ``consultar()``, ``baixar_xmls()`` e ``gerar_pdfs()`` trabalham sobre o
           repositório local, com filtro por período, modelo e direção.

    O certificado só é exigido nas operações que acessam a SEFAZ; consultas ao
    repositório local funcionam sem ele.

    Args:
        certificado: ``CertificadoA1`` já carregado ou caminho do arquivo .pfx/.p12.
        senha: senha do .pfx (quando ``certificado`` é caminho). Use variável de ambiente.
        cnpj: CNPJ de interesse (14 posições, sem máscara).
        cpf: CPF de interesse (11 dígitos), alternativo ao CNPJ.
        uf: código IBGE da UF do autor (ex.: ``"29"`` BA). Obrigatório para CT-e e MDF-e.
        ambiente: produção ou homologação.
        pasta: pasta raiz dos dados; os arquivos ficam em ``<pasta>/<ambiente>/<cnpj_cpf>/``.
        ca_bundle: cadeia ICP-Brasil em PEM, se o TLS do servidor não validar.
        transporte, repositorio, gerador_pdf, relogio: dependências substituíveis.
    """

    def __init__(self, certificado: CertificadoA1 | str | Path | None = None,
                 senha: str | None = None, *, cnpj: str | None = None, cpf: str | None = None,
                 uf: str | None = None, ambiente: Ambiente = Ambiente.PRODUCAO,
                 pasta: str | Path = "./freedfe_dados", ca_bundle: str | None = None,
                 transporte: Transporte | None = None,
                 repositorio: RepositorioDocumentos | None = None,
                 gerador_pdf: GeradorPdf | None = None, relogio: Relogio = relogio_utc,
                 pausa_entre_lotes: float = 1.0):
        self.interessado = Interessado(cnpj=cnpj, cpf=cpf, uf=uf)
        self.ambiente = Ambiente(ambiente)
        self.pasta = Path(pasta) / self.ambiente.nome_pasta / self.interessado.documento
        self._certificado = self._carregar_certificado(certificado, senha)
        self._ca_bundle = ca_bundle
        self._transporte = transporte
        self.repositorio = repositorio or RepositorioArquivos(
            self.pasta, self.interessado.documento)
        self._gerador_pdf = gerador_pdf or GeradorPdfBrazilFiscalReport()
        self._relogio = relogio
        self._pausa = pausa_entre_lotes
        self._auditoria = RegistroAuditoria(self.pasta / "_estado" / "chamadas.jsonl")
        self._fabrica_exportador = FabricaExportador()

    # ------------------------------------------------------------------
    # Distribuição (acessa a SEFAZ)
    # ------------------------------------------------------------------

    def sincronizar(self, modelos: Iterable[ModeloDocumento | str] | None = None,
                    max_lotes: int = 200) -> list[ResultadoSincronizacao]:
        """Baixa os documentos novos de cada modelo (``distNSU``), respeitando bloqueios.

        Args:
            modelos: modelos a sincronizar. Padrão: NF-e, CT-e e MDF-e.
            max_lotes: máximo de chamadas por modelo (cada uma traz até 50 documentos).
        """
        modelos = self._modelos(modelos)
        self._validar_uf(modelos)
        resultados = []
        for modelo in modelos:
            with TravaArquivo(self._arquivo_estado(modelo).with_suffix(".lock")):
                resultados.append(self._cliente_distribuicao(modelo).sincronizar(max_lotes))
        return resultados

    def consultar_nsu(self, modelo: ModeloDocumento | str, nsu: str | int) -> RetornoDistribuicao:
        """Busca um NSU específico (lacuna). Limite de 20 consultas pontuais por hora."""
        modelo = ModeloDocumento.normalizar(modelo)
        with TravaArquivo(self._arquivo_estado(modelo).with_suffix(".lock")):
            return self._cliente_distribuicao(modelo).consultar_nsu(nsu)

    def consultar_chave(self, chave: str) -> RetornoDistribuicao:
        """Busca uma NF-e pela chave (somente NF-e). Limite de 20 consultas pontuais por hora."""
        modelo = ModeloDocumento.NFE
        with TravaArquivo(self._arquivo_estado(modelo).with_suffix(".lock")):
            return self._cliente_distribuicao(modelo).consultar_chave(chave)

    def estado_nsu(self, modelo: ModeloDocumento | str) -> dict:
        """Último NSU, bloqueio e última execução da sequência de um modelo."""
        return self._estado(ModeloDocumento.normalizar(modelo)).como_dict()

    def lacunas_nsu(self, modelo: ModeloDocumento | str) -> list[str]:
        """NSU faltantes entre os recebidos (candidatos a ``consultar_nsu``)."""
        return self.repositorio.lacunas_nsu(ModeloDocumento.normalizar(modelo))

    # ------------------------------------------------------------------
    # Consulta, XML e PDF (repositório local)
    # ------------------------------------------------------------------

    def consultar(self, filtro: FiltroConsulta | None = None,
                  formato: FormatoSaida | str | None = FormatoSaida.LISTA,
                  destino: str | Path | None = None):
        """Lista os documentos armazenados que atendem ao filtro.

        Args:
            filtro: período, modelos, CNPJ/CPF e direção. ``None`` = todos.
            formato: ``lista`` (padrão, objetos ``ResumoDocumento``), ``csv``,
                ``json``, ``xml`` ou ``xlsx``.
            destino: se informado (e formato diferente de ``lista``), grava o arquivo.

        Returns:
            ``list[ResumoDocumento]`` (lista), ``str`` (csv/json/xml), ``bytes`` (xlsx)
            ou ``Path`` quando ``destino`` é informado.
        """
        resumos = self.repositorio.listar(self._preparar_filtro(filtro))
        formato = FormatoSaida.normalizar(formato)
        if formato is FormatoSaida.LISTA:
            return resumos
        exportador = self._fabrica_exportador.obter(formato)
        if destino is not None:
            return exportador.gravar(resumos, destino)
        return exportador.exportar(resumos)

    def baixar_xmls(self, filtro: FiltroConsulta | None = None,
                    destino: str | Path = "./xml", incluir_resumos: bool = False) -> list[Path]:
        """Copia os XMLs dos documentos filtrados para ``destino`` como ``<chave>.xml``.

        Resumos (``resNFe``) só são copiados com ``incluir_resumos=True``, como
        ``<chave>-resumo.xml``.
        """
        destino = Path(destino)
        destino.mkdir(parents=True, exist_ok=True)
        copiados = []
        for resumo in self.repositorio.listar(self._preparar_filtro(filtro)):
            if not resumo.xml_completo and not incluir_resumos:
                continue
            nome = f"{resumo.chave}.xml" if resumo.xml_completo else f"{resumo.chave}-resumo.xml"
            (destino / nome).write_bytes(self.repositorio.ler_xml(resumo))
            copiados.append(destino / nome)
        return copiados

    def gerar_pdfs(self, filtro: FiltroConsulta | None = None,
                   destino: str | Path = "./pdf") -> ResultadoGeracaoPdf:
        """Gera DANFE/DACTE/DAMDFE dos documentos com XML completo, como ``<chave>.pdf``."""
        filtro = replace(self._preparar_filtro(filtro), somente_xml_completo=True)
        resultado = ResultadoGeracaoPdf()
        for resumo in self.repositorio.listar(filtro):
            caminho = Path(destino) / f"{resumo.chave}.pdf"
            try:
                xml = self.repositorio.ler_xml(resumo)
                resultado.gerados.append(self._gerador_pdf.gerar(xml, resumo.modelo, caminho))
            except FreeDFeErro:
                raise  # dependência ausente etc.: não adianta continuar
            except Exception as erro:  # noqa: BLE001 - layout inesperado em um XML não interrompe o lote
                logger.warning("PDF não gerado para %s: %s", resumo.chave, erro)
                resultado.falhas[resumo.chave] = str(erro)
        return resultado

    def importar_xmls(self, pasta: str | Path) -> ResultadoArmazenamento:
        """Importa XMLs externos (ex.: emitidos pelo próprio CNPJ, vindos do ERP)."""
        return ImportadorXml(self.repositorio).importar_pasta(pasta)

    # ------------------------------------------------------------------
    # Manifestação do Destinatário (acessa a SEFAZ)
    # ------------------------------------------------------------------

    def pendentes_manifestacao(self, filtro: FiltroConsulta | None = None) -> list[ResumoDocumento]:
        """NF-e autorizadas recebidas apenas como resumo (sem XML completo)."""
        filtro = replace(self._preparar_filtro(filtro), modelos=[ModeloDocumento.NFE],
                         situacoes=[SituacaoDocumento.AUTORIZADO])
        return [r for r in self.repositorio.listar(filtro) if not r.xml_completo]

    def manifestar_ciencia(self, chaves: Iterable[str]) -> list[RetornoManifestacao]:
        """Ciência da Operação (210210). Libera o XML completo na próxima sincronização."""
        return self._cliente_manifestacao().ciencia(chaves)

    def manifestar_confirmacao(self, chaves: Iterable[str]) -> list[RetornoManifestacao]:
        """Confirmação da Operação (210200)."""
        return self._cliente_manifestacao().confirmacao(chaves)

    def manifestar_desconhecimento(self, chaves: Iterable[str]) -> list[RetornoManifestacao]:
        """Desconhecimento da Operação (210220)."""
        return self._cliente_manifestacao().desconhecimento(chaves)

    def manifestar_nao_realizada(self, chaves: Iterable[str],
                                 justificativa: str) -> list[RetornoManifestacao]:
        """Operação não Realizada (210240), com justificativa de 15 a 255 caracteres."""
        return self._cliente_manifestacao().nao_realizada(chaves, justificativa)

    # ------------------------------------------------------------------
    # Certificado
    # ------------------------------------------------------------------

    def info_certificado(self) -> InfoCertificado:
        """Titular, CNPJ/CPF e validade do certificado."""
        return self._exigir_certificado().info()

    # ------------------------------------------------------------------
    # Montagem das dependências
    # ------------------------------------------------------------------

    @staticmethod
    def _carregar_certificado(certificado, senha: str | None) -> CertificadoA1 | None:
        if certificado is None or isinstance(certificado, CertificadoA1):
            return certificado
        if senha is None:
            raise CertificadoErro("Informe a senha do certificado.")
        return CertificadoA1.de_arquivo(certificado, senha)

    def _exigir_certificado(self) -> CertificadoA1:
        if self._certificado is None:
            raise CertificadoErro("Operação exige certificado A1 (parâmetro 'certificado').")
        return self._certificado

    def _obter_transporte(self) -> Transporte:
        if self._transporte is None:
            self._transporte = ClienteSoap(self._exigir_certificado(), self._ca_bundle)
        return self._transporte

    def _arquivo_estado(self, modelo: ModeloDocumento) -> Path:
        return self.pasta / "_estado" / f"nsu_{modelo.value}.json"

    def _estado(self, modelo: ModeloDocumento) -> EstadoNSU:
        return EstadoNSU(self._arquivo_estado(modelo), self._relogio)

    def _cliente_distribuicao(self, modelo: ModeloDocumento) -> ClienteDistribuicao:
        return ClienteDistribuicao(
            configuracao=configuracao_distribuicao(modelo), ambiente=self.ambiente,
            interessado=self.interessado, transporte=self._obter_transporte(),
            estado=self._estado(modelo), repositorio=self.repositorio,
            auditoria=self._auditoria, pausa_entre_lotes=self._pausa)

    def _cliente_manifestacao(self) -> ClienteManifestacao:
        return ClienteManifestacao(
            ambiente=self.ambiente, autor=self.interessado, transporte=self._obter_transporte(),
            assinador=AssinadorXml(self._exigir_certificado()), auditoria=self._auditoria)

    def _preparar_filtro(self, filtro: FiltroConsulta | None) -> FiltroConsulta:
        """Usa o CNPJ/CPF do interessado quando a direção é informada sem documento."""
        filtro = filtro or FiltroConsulta()
        if filtro.direcao is not Direcao.TODOS and not filtro.cnpj_cpf:
            filtro = replace(filtro, cnpj_cpf=self.interessado.documento)
        return filtro

    def _validar_uf(self, modelos: list[ModeloDocumento]) -> None:
        """Falha antes de qualquer chamada se algum modelo exigir UF não informada."""
        sem_uf = [m.name for m in modelos if configuracao_distribuicao(m).exige_uf]
        if sem_uf and not self.interessado.uf:
            raise ValidacaoErro(f"Informe 'uf' (código IBGE): exigida para {', '.join(sem_uf)}.")

    @staticmethod
    def _modelos(modelos) -> list[ModeloDocumento]:
        if modelos is None:
            return list(ModeloDocumento)
        if isinstance(modelos, (str, ModeloDocumento)):
            modelos = [modelos]
        return [ModeloDocumento.normalizar(m) for m in modelos]
