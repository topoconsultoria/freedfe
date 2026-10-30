"""``ServicoNfse``: emissão, consulta e reconciliação de NFS-e Nacional.

Exposto como ``FreeDFe.nfse``. Compartilha certificado, ambiente, repositório, auditoria e
pasta com a fachada. Princípios:

    - **idempotência**: a DPS é gravada e marcada ``ENVIADA`` antes do ``POST``; diante de
      timeout ou 5xx a Sefin é consultada por ``HEAD/GET /dps/{id}`` e o ``POST`` nunca é
      repetido sem essa confirmação;
    - **um envio por vez** por emitente (trava exclusiva por recurso), para preservar a
      numeração;
    - **XML exato**: a DPS assinada e a NFS-e devolvida são gravadas como trafegaram;
    - **auditoria sem XML**: a trilha registra códigos, status e Ids, nunca o conteúdo.

Toda a persistência é injetável (repositório, auditoria, estado de emissão, travas, NSU do ADN,
cache de parâmetros e evidências). Com ``pasta`` os padrões gravam arquivos; sem ``pasta`` o
serviço não toca o disco e todas as dependências são obrigatórias (ver ``criar_servico_nfse``).
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from lxml import etree

from freedfe.assinatura import PERFIL_NFSE, AssinadorXml, verificar_assinatura
from freedfe.auditoria import Auditoria, RegistroAuditoria
from freedfe.certificado import CertificadoA1
from freedfe.distribuicao.cliente import ResultadoSincronizacao
from freedfe.distribuicao.estado import EstadoNSU, FabricaEstadoNsu, Relogio, relogio_utc
from freedfe.distribuicao.retorno import DocumentoRecebido
from freedfe.distribuicao.trava import FabricaTrava, Trava, TravaArquivo
from freedfe.documentos.resumo import EventoDocumento, evento_cancela, evento_substitui
from freedfe.excecoes import (
    ComunicacaoErro,
    EmissaoIncertaErro,
    FreeDFeErro,
    RejeicaoNfseErro,
    RespostaInvalidaErro,
    ValidacaoErro,
)
from freedfe.interessado import Interessado
from freedfe.nfse.cliente_adn import BLOQUEIO_PADRAO_SEGUNDOS, ClienteAdn, SincronizadorAdn
from freedfe.nfse.cliente_sefin import ESPERAS_PADRAO, ClienteSefin
from freedfe.nfse.envelope import com_declaracao_utf8
from freedfe.nfse.estado_emissao import (
    SITUACOES_A_RECONCILIAR,
    EstadoEmissao,
    EstadoEmissaoArquivo,
    RegistroEmissao,
    SituacaoEmissao,
)
from freedfe.nfse.eventos import MontadorPedidoEvento, MotivoCancelamento, TipoEventoNfse
from freedfe.nfse.evidencias import ArmazenamentoEvidencias, ArmazenamentoEvidenciasArquivo
from freedfe.nfse.identificadores import id_dps as montar_id_dps
from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.nfse.modelo import Dps
from freedfe.nfse.montador_dps import FOLGA_EMISSAO_SEGUNDOS, montador_para
from freedfe.nfse.parametros import (
    CacheParametros,
    CacheParametrosArquivo,
    ClienteParametros,
    Convenio,
)
from freedfe.nfse.resposta import (
    RetornoEmissao,
    RetornoEventoNfse,
    interpretar_consulta_nfse,
    interpretar_emissao,
    interpretar_evento,
    interpretar_lista_eventos,
    ler_chave_do_xml,
)
from freedfe.nfse.validador_xsd import ValidadorXsd
from freedfe.pdf.gerador import GeradorPdf, GeradorPdfPadrao
from freedfe.repositorio.arquivos import RepositorioArquivos
from freedfe.repositorio.base import RepositorioDocumentos
from freedfe.servicos import ConfiguracaoNfse, configuracao_nfse
from freedfe.tipos import FUSO_BRASILIA, Ambiente, ModeloDocumento, SituacaoDocumento
from freedfe.transporte import TransporteRest
from freedfe.validadores import exigir_chave_nfse, exigir_codigo_ibge, exigir_id_dps

logger = logging.getLogger(__name__)

NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"
_NS = f"{{{NAMESPACE_NFSE}}}"


def _serializar(raiz: etree._Element) -> bytes:
    """XML que vai à Sefin: UTF-8 com declaração (sem ela, E1229) e sem formatação."""
    return com_declaracao_utf8(etree.tostring(raiz, encoding="utf-8"))


@dataclass(frozen=True)
class ConfiguracaoNfseLocal:
    """Opções da NFS-e na fachada.

    Attributes:
        leiaute: fixa o leiaute da DPS; ``None`` escolhe por ambiente e data
            (``VersaoLeiaute.selecionar``): produção restrita sempre NT 009, produção NT 004
            até 30/11/2026.
        pasta_xsd: pasta dos XSD oficiais por leiaute, para validar a DPS antes de enviar.
        prefixo_api: ``"/API"`` se a rota ``/API/SefinNacional`` for a correta na produção
            restrita (divergência entre a página oficial e relatos de desenvolvedores).
        url_consulta_publica: modelo da URL do QR Code do DANFSe, com ``{chave}``.
        verificar_assinatura_retorno: confere a assinatura da NFS-e devolvida (aviso em log).
        folga_emissao_segundos: subtraída do relógio quando ``dhEmi`` não é informada (E0008).
        pausa_entre_lotes_adn: espera entre chamadas da sincronização pelo ADN.
        bloqueio_adn_segundos: intervalo mínimo entre sincronizações que esgotaram o ADN
            (a NFS-e não tem a regra de 1 h da NF-e; padrão 15 min).
        esperas_consulta: segundos de espera antes de cada nova tentativa de ``GET``/``HEAD``
            (o ``POST`` de emissão nunca é repetido). ``()`` desliga as repetições.
        pausa: função de espera entre tentativas e lotes (padrão ``time.sleep``).
        configuracao_api: substitui por completo as URLs do ambiente (gateway corporativo,
            servidor de testes). ``prefixo_api`` e ``url_consulta_publica`` ainda se aplicam.
    """

    leiaute: VersaoLeiaute | None = None
    pasta_xsd: dict[VersaoLeiaute, str | Path] = field(default_factory=dict)
    prefixo_api: str = ""
    url_consulta_publica: str | None = None
    verificar_assinatura_retorno: bool = False
    folga_emissao_segundos: int = FOLGA_EMISSAO_SEGUNDOS
    pausa_entre_lotes_adn: float = 1.0
    bloqueio_adn_segundos: int = BLOQUEIO_PADRAO_SEGUNDOS
    esperas_consulta: tuple[float, ...] = ESPERAS_PADRAO
    pausa: Callable[[float], None] | None = None
    configuracao_api: ConfiguracaoNfse | None = None


@dataclass
class _DpsPreparada:
    """DPS pronta para envio (assinada) e dados para o estado."""

    id_dps: str
    serie: int
    numero: int
    xml: bytes
    mes: str
    municipio: str
    competencia: dt.date | None


class ServicoNfse:
    """Operações da NFS-e Nacional sobre o repositório e o estado locais.

    Não instancie diretamente: use ``FreeDFe.nfse`` (que monta tudo a partir de uma ``pasta``) ou
    ``criar_servico_nfse`` (sem pasta local, com a persistência injetada pela aplicação).

    **Com ``pasta``**, cada dependência de persistência ausente recebe a implementação em arquivo:
    ``<pasta>/_estado`` para auditoria, estado de emissão, travas, NSU e cache de parâmetros;
    ``<pasta>/nfse`` para as evidências; ``<pasta>`` para o repositório.

    **Sem ``pasta``**, o serviço não toca o disco e todas as dependências de persistência são
    obrigatórias: ``repositorio``, ``auditoria``, ``estado_emissao``, ``travas``, ``estado_nsu``,
    ``cache_parametros`` e ``evidencias``. As que faltarem são listadas em um ``ValidacaoErro``.

    Args:
        pasta: raiz dos dados em arquivo, ou ``None`` para um serviço sem disco.
        repositorio: repositório de documentos (padrão com ``pasta``: ``RepositorioArquivos``).
        auditoria: trilha de auditoria (padrão: ``RegistroAuditoria`` em ``chamadas.jsonl``).
        estado_emissao: estado das emissões (padrão: ``EstadoEmissaoArquivo`` em
            ``emissoes_nfse.json``, criado a cada operação). Uma instância injetada é usada
            como está e deve reler a fonte a cada chamada.
        travas: fábrica de travas por recurso (``emissao_nfse``, ``nsu_nfse`` e
            ``nsu_nfse_<cnpj>``). Padrão: ``TravaArquivo`` em ``<recurso>.lock``.
        estado_nsu: fábrica do estado de NSU do ADN por recurso (``nsu_nfse`` e
            ``nsu_nfse_<cnpj>``). Padrão: ``EstadoNSU`` em ``<recurso>.json``, com o
            ``bloqueio_adn_segundos`` das opções; uma fábrica injetada define o seu próprio
            bloqueio.
        cache_parametros: cache dos parâmetros municipais (padrão: ``CacheParametrosArquivo``
            em ``parametros_nfse``).
        evidencias: armazenamento da DPS enviada, das respostas e dos pedidos de evento (padrão:
            ``ArmazenamentoEvidenciasArquivo``).
    """

    def __init__(self, *, ambiente: Ambiente, interessado: Interessado,
                 obter_certificado: Callable[[], CertificadoA1],
                 obter_transporte: Callable[[], TransporteRest],
                 pasta: Path | None = None,
                 repositorio: RepositorioDocumentos | None = None,
                 auditoria: Auditoria | None = None,
                 relogio: Relogio = relogio_utc,
                 opcoes: ConfiguracaoNfseLocal | None = None,
                 pausa: Callable[[float], None] | None = None,
                 gerador_pdf: GeradorPdf | None = None,
                 estado_emissao: EstadoEmissao | None = None,
                 travas: FabricaTrava | None = None,
                 estado_nsu: FabricaEstadoNsu | None = None,
                 cache_parametros: CacheParametros | None = None,
                 evidencias: ArmazenamentoEvidencias | None = None):
        self._ambiente = Ambiente(ambiente)
        self._interessado = interessado
        self._obter_certificado = obter_certificado
        self._obter_transporte = obter_transporte
        self._relogio = relogio
        self._opcoes = opcoes or ConfiguracaoNfseLocal()
        self._pausa = pausa or self._opcoes.pausa or time.sleep
        self._gerador_pdf = gerador_pdf
        self._validadores: dict[VersaoLeiaute, ValidadorXsd] = {}
        configuracao = self._opcoes.configuracao_api or configuracao_nfse(
            self._ambiente, self._opcoes.prefixo_api)
        if self._opcoes.url_consulta_publica:
            configuracao = configuracao.com(
                modelo_url_consulta_publica=self._opcoes.url_consulta_publica)
        self.configuracao: ConfiguracaoNfse = configuracao

        # A pasta só é usada aqui (padrões em arquivo) e em gerar_danfse com destino=None.
        self._pasta = Path(pasta) if pasta is not None else None
        obrigatorias = {"repositorio": repositorio, "auditoria": auditoria,
                        "estado_emissao": estado_emissao, "travas": travas,
                        "estado_nsu": estado_nsu, "cache_parametros": cache_parametros,
                        "evidencias": evidencias}
        fabrica_estado: Callable[[], EstadoEmissao] | None = (
            (lambda: estado_emissao) if estado_emissao is not None else None)
        if self._pasta is None:
            faltantes = [nome for nome, valor in obrigatorias.items() if valor is None]
            if faltantes:
                raise ValidacaoErro(
                    "Sem pasta local, informe as dependências de persistência: "
                    f"{', '.join(faltantes)}.")
        else:
            raiz, estado = self._pasta, self._pasta / "_estado"
            repositorio = repositorio or RepositorioArquivos(raiz, interessado.documento)
            auditoria = auditoria or RegistroAuditoria(estado / "chamadas.jsonl")
            travas = travas or (lambda recurso: TravaArquivo(estado / f"{recurso}.lock"))
            estado_nsu = estado_nsu or (lambda recurso: EstadoNSU(
                estado / f"{recurso}.json", relogio, self._opcoes.bloqueio_adn_segundos))
            cache_parametros = cache_parametros or CacheParametrosArquivo(
                estado / "parametros_nfse", relogio)
            evidencias = evidencias or ArmazenamentoEvidenciasArquivo(raiz)
            if fabrica_estado is None:
                def fabrica_estado() -> EstadoEmissao:
                    return EstadoEmissaoArquivo(estado / "emissoes_nfse.json", relogio)
        self._repositorio: RepositorioDocumentos = repositorio
        self._auditoria: Auditoria = auditoria
        self._travas: FabricaTrava = travas
        self._estado_nsu: FabricaEstadoNsu = estado_nsu
        self._cache_parametros: CacheParametros = cache_parametros
        self._evidencias: ArmazenamentoEvidencias = evidencias
        self._fabrica_estado_emissao: Callable[[], EstadoEmissao] = fabrica_estado

    # ------------------------------------------------------------------
    # Emissão
    # ------------------------------------------------------------------

    def emitir(self, dps: Dps | bytes, *, leiaute: VersaoLeiaute | None = None,
               verificar_parametros: bool = False) -> RetornoEmissao:
        """Assina (se necessário), envia a DPS e grava a NFS-e autorizada.

        Args:
            dps: modelo tipado ``Dps`` ou XML pronto (``bytes``). XML que já traz ``Signature``
                é enviado como está, sem reassinar, acrescido só da declaração UTF-8 se ausente
                (sem ela a Sefin rejeita com E1229; a assinatura não é afetada).
            leiaute: força o leiaute do montador (padrão: pelo ambiente e pela data).
            verificar_parametros: antes de enviar, confere o convênio do município (com cache);
                município não aderente levanta ``ValidacaoErro`` (evita E0037).

        Returns:
            ``RetornoEmissao`` com a chave e o XML da NFS-e, também armazenado no repositório.

        Raises:
            ValidacaoErro: DPS inválida (nada foi enviado).
            RejeicaoNfseErro: a Sefin rejeitou a DPS. Corrija antes de emitir de novo.
            EmissaoIncertaErro: timeout ou 5xx. Nunca reenvie manualmente: use ``reconciliar``.
            RespostaInvalidaErro: resposta sem os dados esperados.
        """
        certificado = self._obter_certificado()
        leiaute = self._leiaute(leiaute)
        with self._trava():
            estado = self._novo_estado()
            reservada = None
            try:
                if isinstance(dps, Dps) and dps.numero is None:
                    reservada = self._reservar_numero(dps, estado)
                    dps = replace(dps, numero=reservada.numero)
                preparada = self._preparar(dps, leiaute, estado, certificado)
                if reservada is not None and preparada.id_dps != reservada.id_dps:
                    raise FreeDFeErro(
                        f"O Id da DPS montada ({preparada.id_dps}) difere do Id reservado "
                        f"({reservada.id_dps}).")
                return self._enviar(preparada, estado, verificar_parametros)
            except BaseException:
                if reservada is not None:
                    self._devolver_reserva(estado, reservada.id_dps)
                raise

    def gerar_dps_assinada(self, dps: Dps | bytes, leiaute: VersaoLeiaute | None = None) -> bytes:
        """DPS assinada, sem enviar nada nem reservar número (conferência antes de emitir)."""
        certificado = self._obter_certificado()
        return self._preparar(dps, self._leiaute(leiaute), self._novo_estado(), certificado).xml

    def reconciliar(self, id_dps: str,
                    descartar_nao_localizada: bool = False) -> RetornoEmissao | None:
        """Consulta a Sefin sobre uma DPS de resultado desconhecido.

        Returns:
            A NFS-e (gravada no repositório, estado ``AUTORIZADA``) ou ``None`` se a Sefin não
            conhece a DPS. Com ``descartar_nao_localizada=True``, ``None`` marca o registro como
            ``REJEITADA`` (motivo ``nao_localizada``), liberando a numeração automática.
        """
        id_dps = exigir_id_dps(id_dps)
        with self._trava():
            return self._reconciliar_registro(self._novo_estado(), id_dps, descartar_nao_localizada)

    def reconciliar_pendentes(
            self, descartar_nao_localizadas: bool = False) -> dict[str, RetornoEmissao | None]:
        """Reconcilia as DPS ``ENVIADA``/``INCERTA``. Devolve Id da DPS -> NFS-e ou ``None``."""
        resultados: dict[str, RetornoEmissao | None] = {}
        with self._trava():
            estado = self._novo_estado()
            for situacao in SITUACOES_A_RECONCILIAR:
                for registro in estado.listar(situacao):
                    resultados[registro.id_dps] = self._reconciliar_registro(
                        estado, registro.id_dps, descartar_nao_localizadas)
        return resultados

    def emissoes(self, situacao: SituacaoEmissao | str | None = None) -> list[RegistroEmissao]:
        """Estado local das emissões (opcionalmente filtrado por situação)."""
        return self._novo_estado().listar(situacao)

    def consultar_chave(self, chave: str, armazenar: bool = True) -> RetornoEmissao:
        """``GET /nfse/{chave}``. Com ``armazenar``, grava a NFS-e no repositório.

        Requer que o certificado seja de um ator da nota (prestador, tomador ou intermediário).
        """
        retorno = interpretar_consulta_nfse(self._cliente().consultar_nfse(chave))
        if armazenar:
            self._repositorio.armazenar([DocumentoRecebido(xml=retorno.xml_nfse)])
        return retorno

    # ------------------------------------------------------------------
    # DANFSe
    # ------------------------------------------------------------------

    def gerar_danfse(self, chave: str, destino: str | Path | None = None) -> Path:
        """Gera o DANFSe (PDF com QR Code) de uma NFS-e armazenada no repositório.

        Cancelada ou substituída (pelos eventos armazenados) recebe marca d'água.
        Requer o extra ``nfse``: ``pip install 'freedfe[nfse]'``.

        Args:
            chave: chave de acesso (50 posições). Se a nota não estiver no repositório, use
                antes ``consultar_chave`` ou ``sincronizar``.
            destino: arquivo PDF; padrão ``nfse/danfse/<chave>.pdf`` na pasta de dados.

        Raises:
            ValidacaoErro: serviço sem pasta e sem ``destino`` (use ``danfse_bytes``), ou nota
                fora do repositório.
        """
        chave = exigir_chave_nfse(chave)
        if destino is None and self._pasta is None:
            raise ValidacaoErro("Serviço sem pasta local: informe 'destino' ou use "
                                "danfse_bytes(chave), que devolve o PDF em memória.")
        xml, situacao = self._xml_para_danfse(chave)
        destino = Path(destino) if destino else self._pasta / "nfse" / "danfse" / f"{chave}.pdf"
        return self._gerador().gerar(xml, ModeloDocumento.NFSE, destino, situacao=situacao)

    def danfse_bytes(self, chave: str) -> bytes:
        """DANFSe (PDF com QR Code) de uma NFS-e do repositório, em memória e sem tocar o disco.

        Mesma busca e mesma marca d'água (cancelada/substituída) de ``gerar_danfse``. Requer o
        extra ``nfse``. Se um ``gerador_pdf`` próprio foi injetado, ele precisa sobrescrever
        ``gerar_bytes`` para não usar o disco (o padrão de ``GeradorPdf`` usa um diretório
        temporário).
        """
        chave = exigir_chave_nfse(chave)
        xml, situacao = self._xml_para_danfse(chave)
        return self._gerador().gerar_bytes(xml, ModeloDocumento.NFSE, situacao=situacao)

    def _xml_para_danfse(self, chave: str) -> tuple[bytes, SituacaoDocumento]:
        """XML e situação (pelos eventos armazenados) da NFS-e, para o DANFSe."""
        resumo = self._repositorio.obter_atual(ModeloDocumento.NFSE, chave)
        if resumo is None:
            raise ValidacaoErro(f"A NFS-e {chave} não está no repositório; use consultar_chave().")
        return self._repositorio.ler_xml(resumo), resumo.situacao

    def _gerador(self) -> GeradorPdf:
        return self._gerador_pdf or GeradorPdfPadrao(self.configuracao.url_consulta_publica)

    # ------------------------------------------------------------------
    # Sincronização pelo ADN (notas tomadas e eventos fora do ERP)
    # ------------------------------------------------------------------

    def sincronizar(self, max_lotes: int = 200,
                    cnpj_consulta: str | None = None) -> ResultadoSincronizacao:
        """Baixa do ADN, por NSU, as NFS-e e os eventos do contribuinte.

        Captura notas tomadas e eventos feitos fora do ERP (cancelamento pelo portal ou por
        ofício, análise fiscal); a situação das notas locais é atualizada pelos eventos.

        Args:
            max_lotes: máximo de chamadas ao ADN nesta execução.
            cnpj_consulta: CNPJ da mesma raiz do certificado (matriz/filiais). Cada CNPJ tem
                a sua própria sequência de NSU.
        """
        self._obter_certificado()
        recurso = f"nsu_nfse_{cnpj_consulta}" if cnpj_consulta else "nsu_nfse"
        with self._travas(recurso):
            estado = self._estado_nsu(recurso)
            cliente = ClienteAdn(self.configuracao, self._obter_transporte(), self._auditoria,
                                 pausa=self._pausa, esperas=self._opcoes.esperas_consulta)
            return SincronizadorAdn(
                cliente, estado, self._repositorio, self._pausa,
                self._opcoes.pausa_entre_lotes_adn, cnpj_consulta=cnpj_consulta
            ).sincronizar(max_lotes)

    # ------------------------------------------------------------------
    # Parâmetros municipais
    # ------------------------------------------------------------------

    def convenio(self, codigo_municipio: str, atualizar: bool = False) -> Convenio:
        """Convênio do município com a NFS-e Nacional (cache de 24 h; ``atualizar`` ignora-o)."""
        municipio = exigir_codigo_ibge(codigo_municipio)
        dados = self._cache_parametros.obter_ou_buscar(
            municipio, "convenio", lambda: self._parametros().convenio(municipio), atualizar)
        return Convenio.de_dict(municipio, dados)

    def parametros_servico(self, codigo_municipio: str, codigo_servico: str,
                           atualizar: bool = False) -> dict:
        """Parâmetros do serviço (``cTribNac``) no município: alíquota, deduções, regimes."""
        municipio = exigir_codigo_ibge(codigo_municipio)
        return self._cache_parametros.obter_ou_buscar(
            municipio, f"servico-{codigo_servico}",
            lambda: self._parametros().servico_municipal(municipio, codigo_servico), atualizar)

    def parametros_contribuinte(self, codigo_municipio: str, documento: str | None = None,
                                atualizar: bool = False) -> dict:
        """Retenções e benefícios do contribuinte (padrão: o CNPJ/CPF da fachada)."""
        municipio = exigir_codigo_ibge(codigo_municipio)
        documento = documento or self._interessado.documento
        return self._cache_parametros.obter_ou_buscar(
            municipio, f"contribuinte-{documento}",
            lambda: self._parametros().contribuinte(municipio, documento), atualizar)

    def verificar_pre_emissao(self, dps: Dps) -> list[str]:
        """Confere o convênio do município emissor. Devolve avisos; bloqueia se não aderente.

        Raises:
            ValidacaoErro: município sem convênio com a NFS-e Nacional (E0037).
        """
        return self._verificar_convenio(dps.codigo_municipio_emissor, dps.data_competencia)

    def _verificar_convenio(self, municipio: str, competencia: dt.date | None) -> list[str]:
        convenio = self.convenio(municipio)
        if convenio.aderente is False:
            raise ValidacaoErro(f"O município {municipio} não tem convênio com a NFS-e "
                                "Nacional neste ambiente (a Sefin rejeitaria com E0037).")
        avisos = []
        if convenio.aderente_emissor_nacional is False:
            # Semântica sem documentação oficial: avisa, mas deixa a decisão para a Sefin.
            avisos.append(f"O município {municipio} consta como não aderente ao Emissor "
                          "Nacional (aderenteEmissorNacional=0): a Sefin pode rejeitar a DPS.")
        if (competencia is not None and convenio.data_ativacao is not None
                and competencia < convenio.data_ativacao):
            avisos.append(f"Competência {competencia:%d/%m/%Y} anterior à ativação do convênio "
                          f"({convenio.data_ativacao:%d/%m/%Y}): a Sefin pode rejeitar (E0016).")
        for aviso in avisos:
            logger.warning(aviso)
        return avisos

    def _parametros(self) -> ClienteParametros:
        return ClienteParametros(self.configuracao, self._obter_transporte(), self._auditoria,
                                 pausa=self._pausa, esperas=self._opcoes.esperas_consulta)

    # ------------------------------------------------------------------
    # Eventos: cancelamento
    # ------------------------------------------------------------------

    def cancelar(self, chave: str, codigo_motivo: MotivoCancelamento | int,
                 justificativa: str) -> RetornoEventoNfse:
        """Registra o cancelamento (evento 101101) e atualiza a situação local.

        Args:
            chave: chave de acesso da NFS-e (50 posições).
            codigo_motivo: 1 erro na emissão, 2 serviço não prestado, 9 outros.
            justificativa: 15 a 255 caracteres.

        Raises:
            ValidacaoErro: a nota já consta como cancelada/substituída localmente (E0840).
            RejeicaoNfseErro: regra da Sefin ou do município (prazo, E0824, E0840...).
            ComunicacaoErro: timeout/5xx sem confirmação; use ``listar_eventos`` antes de
                tentar de novo.
        """
        chave = exigir_chave_nfse(chave)
        certificado = self._obter_certificado()
        self._exigir_nao_encerrada(chave)
        montador = MontadorPedidoEvento(
            self._ambiente, self._interessado, self._leiaute(None), self._relogio,
            self._opcoes.folga_emissao_segundos)
        raiz = montador.cancelamento(chave, codigo_motivo, justificativa)
        AssinadorXml(certificado, PERFIL_NFSE).assinar(raiz)
        pedido = _serializar(raiz)
        tipo = TipoEventoNfse.CANCELAMENTO.value
        with self._trava():
            self._gravar_evento_enviado(chave, tipo, "pedido.xml", pedido)
            try:
                resposta = self._cliente().registrar_evento(chave, pedido)
                self._gravar_evento_enviado(chave, tipo, "resposta.json", resposta.corpo)
                retorno = interpretar_evento(resposta, chave, tipo)
            except ComunicacaoErro as erro:
                return self._cancelamento_incerto(chave, tipo, erro)
            except RejeicaoNfseErro as rejeicao:
                self._auditoria.registrar(servico="nfse", operacao="cancelar", chave=chave,
                                          situacao="rejeitado", codigos=rejeicao.codigos)
                if "E0840" in rejeicao.codigos:
                    self._sincronizar_eventos_se_possivel(chave)  # nota já cancelada na Sefin
                raise
            return self._concluir_cancelamento(chave, retorno)

    def listar_eventos(self, chave: str) -> list[EventoDocumento]:
        """Consulta os eventos da NFS-e na Sefin, armazena os que faltam e devolve todos."""
        chave = exigir_chave_nfse(chave)
        xmls = interpretar_lista_eventos(self._cliente().eventos(chave))
        if xmls:
            resultado = self._repositorio.armazenar([DocumentoRecebido(xml=x) for x in xmls])
            if resultado.nao_reconhecidos:
                logger.warning("%s evento(s) da NFS-e %s em leiaute não reconhecido foram "
                               "preservados sem interpretação.", resultado.nao_reconhecidos, chave)
        return self._repositorio.eventos(ModeloDocumento.NFSE, chave)

    def _exigir_nao_encerrada(self, chave: str) -> None:
        eventos = self._repositorio.eventos(ModeloDocumento.NFSE, chave)
        if any(evento_cancela(e) for e in eventos):
            raise ValidacaoErro(f"A NFS-e {chave} já consta como cancelada (E0840).")
        if any(evento_substitui(e) for e in eventos):
            raise ValidacaoErro(f"A NFS-e {chave} já foi substituída (E0840).")

    def _concluir_cancelamento(self, chave: str, retorno: RetornoEventoNfse) -> RetornoEventoNfse:
        if retorno.xml_evento is not None:
            self._repositorio.armazenar([DocumentoRecebido(xml=retorno.xml_evento)])
        if not any(evento_cancela(e)
                   for e in self._repositorio.eventos(ModeloDocumento.NFSE, chave)):
            self._sincronizar_eventos_se_possivel(chave)  # a resposta não trouxe o evento
        self._auditoria.registrar(servico="nfse", operacao="cancelar", chave=chave,
                                  situacao="cancelado")
        return retorno

    def _cancelamento_incerto(self, chave: str, tipo: str, erro: ComunicacaoErro):
        """Falha após o envio: confere se o evento foi registrado antes de desistir."""
        self._sincronizar_eventos_se_possivel(chave)
        if any(evento_cancela(e) for e in self._repositorio.eventos(ModeloDocumento.NFSE, chave)):
            self._auditoria.registrar(servico="nfse", operacao="cancelar", chave=chave,
                                      situacao="cancelado")
            return RetornoEventoNfse(chave_nfse=chave, tipo_evento=tipo, xml_evento=None,
                                     status_http=200)
        self._auditoria.registrar(servico="nfse", operacao="cancelar", chave=chave,
                                  situacao="incerto")
        raise ComunicacaoErro(
            f"{erro} Não foi possível confirmar o cancelamento da NFS-e {chave}: consulte "
            "listar_eventos() antes de tentar de novo.") from erro

    def _sincronizar_eventos_se_possivel(self, chave: str) -> None:
        try:
            self.listar_eventos(chave)
        except (ComunicacaoErro, RespostaInvalidaErro, RejeicaoNfseErro) as erro:
            logger.warning("Eventos da NFS-e %s não sincronizados: %s", chave, erro)

    def _gravar_evento_enviado(self, chave: str, tipo: str, sufixo: str, conteudo: bytes) -> None:
        """Evidência do pedido enviado e da resposta bruta, pelo armazenamento de evidências."""
        mes = self._relogio().astimezone(FUSO_BRASILIA).strftime("%Y-%m")
        self._evidencias.gravar_evento(chave, tipo, mes, sufixo, conteudo)

    # ------------------------------------------------------------------
    # Preparação da DPS
    # ------------------------------------------------------------------

    def _preparar(self, dps: Dps | bytes, leiaute: VersaoLeiaute, estado: EstadoEmissao,
                  certificado: CertificadoA1) -> _DpsPreparada:
        if isinstance(dps, Dps):
            raiz = self._montar(dps, leiaute, estado)
        elif isinstance(dps, (bytes, bytearray)):
            raiz = self._ler_dps_pronta(bytes(dps))
        else:
            raise ValidacaoErro("emitir() aceita um Dps ou o XML da DPS em bytes.")
        inf = raiz.find(f"{_NS}infDPS")
        identificador = inf.get("Id")
        validador = self._validador(leiaute)
        if validador is not None:
            validador.validar(raiz)
        assinada = raiz.find(f"{{{NS_DSIG}}}Signature") is not None
        if assinada:
            # XML pronto já assinado: não reassina; só garante a declaração UTF-8 (E1229).
            xml = com_declaracao_utf8(bytes(dps)) if not isinstance(dps, Dps) \
                else _serializar(raiz)
            if not verificar_assinatura(raiz):
                logger.warning("A assinatura da DPS %s não confere com os perfis conhecidos.",
                               identificador)
        else:
            AssinadorXml(certificado, PERFIL_NFSE).assinar(raiz)
            xml = _serializar(raiz)
        return _DpsPreparada(
            id_dps=identificador, serie=int(identificador[25:30]), numero=int(identificador[30:45]),
            xml=xml, mes=self._mes(inf), municipio=identificador[3:10],
            competencia=self._competencia(inf))

    def _montar(self, dps: Dps, leiaute: VersaoLeiaute, estado: EstadoEmissao) -> etree._Element:
        if dps.numero is None:
            self._exigir_sem_pendencias(estado, int(dps.serie))
            dps = replace(dps, numero=estado.proximo_numero(int(dps.serie)))
        montador = montador_para(
            leiaute, self._ambiente, relogio=self._relogio,
            folga_emissao_segundos=self._opcoes.folga_emissao_segundos)
        return montador.montar(dps)

    def _reservar_numero(self, dps: Dps, estado: EstadoEmissao) -> RegistroEmissao:
        """Numeração automática de ``emitir``: reserva o próximo número da série, atomicamente.

        O Id da reserva é composto pela mesma função que o montador usa (``id_dps``).
        """
        serie = int(dps.serie)
        self._exigir_sem_pendencias(estado, serie)
        return estado.reservar_proximo(
            serie, lambda numero: montar_id_dps(
                dps.codigo_municipio_emissor, dps.documento_emitente, serie, numero))

    @staticmethod
    def _devolver_reserva(estado: EstadoEmissao, id_dps: str) -> None:
        """Descarta a reserva se a emissão falhou antes do envio (o número não é consumido).

        Só remove ``PENDENTE`` com ``tentativas == 0``: depois do ``marcar(ENVIADA)`` o registro
        é evidência e fica. Uma falha aqui é registrada em log e não substitui o erro original.
        """
        try:
            registro = estado.obter(id_dps)
            if (registro is not None and registro.situacao is SituacaoEmissao.PENDENTE
                    and registro.tentativas == 0):
                estado.descartar(id_dps)
        except Exception:
            logger.exception("A reserva %s não pôde ser descartada após a falha da emissão; "
                             "o número ficará sem uso.", id_dps)

    @staticmethod
    def _exigir_sem_pendencias(estado: EstadoEmissao, serie: int) -> None:
        """Numeração automática com emissão incerta pendente geraria uma segunda nota."""
        pendentes = [r.id_dps for situacao in SITUACOES_A_RECONCILIAR
                     for r in estado.listar(situacao) if r.serie == serie]
        if pendentes:
            raise ValidacaoErro(
                f"Há emissões da série {serie} sem resultado confirmado ({', '.join(pendentes)}). "
                "Chame reconciliar_pendentes() antes de numerar uma nova DPS automaticamente, "
                "ou informe o número explicitamente.")

    def _ler_dps_pronta(self, xml: bytes) -> etree._Element:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        try:
            raiz = etree.fromstring(xml, parser)
        except etree.XMLSyntaxError as erro:
            raise ValidacaoErro(f"XML da DPS malformado: {erro}") from erro
        if raiz.tag != f"{_NS}DPS":
            raise ValidacaoErro("O XML deve ter a raiz DPS no namespace da NFS-e Nacional.")
        inf = raiz.find(f"{_NS}infDPS")
        if inf is None:
            raise ValidacaoErro("DPS sem o grupo infDPS.")
        identificador = exigir_id_dps(inf.get("Id") or "")
        ambiente = (inf.findtext(f"{_NS}tpAmb") or "").strip()
        if ambiente != str(int(self._ambiente)):
            raise ValidacaoErro(
                f"tpAmb da DPS ({ambiente or 'ausente'}) difere do ambiente configurado "
                f"({int(self._ambiente)}); a Sefin rejeitaria com E0006.")
        esperado = self._id_esperado(inf)
        if esperado is not None and esperado != identificador:
            raise ValidacaoErro(f"O Id da DPS ({identificador}) difere da composição dos campos "
                                f"({esperado}); a Sefin rejeitaria com E0004.")
        return raiz

    @staticmethod
    def _mes(inf: etree._Element) -> str:
        """``AAAA-MM`` da emissão para nomear a pasta; qualquer outro texto vira ``sem-data``.

        O XML pode vir de fora: o texto nunca entra no caminho sem passar por esta validação.
        """
        mes = (inf.findtext(f"{_NS}dhEmi") or "")[:7]
        return mes if re.fullmatch(r"\d{4}-\d{2}", mes) else "sem-data"

    @staticmethod
    def _competencia(inf: etree._Element) -> dt.date | None:
        try:
            return dt.date.fromisoformat((inf.findtext(f"{_NS}dCompet") or "").strip())
        except ValueError:
            return None

    @staticmethod
    def _id_esperado(inf: etree._Element) -> str | None:
        """Id recomposto a partir dos campos, quando todos estão presentes."""
        papel = {"1": "prest", "2": "toma", "3": "interm"}.get(
            (inf.findtext(f"{_NS}tpEmit") or "").strip())
        parte = inf.find(f"{_NS}{papel}") if papel else None
        documento = None
        if parte is not None:
            documento = parte.findtext(f"{_NS}CNPJ") or parte.findtext(f"{_NS}CPF")
        campos = (inf.findtext(f"{_NS}cLocEmi"), documento, inf.findtext(f"{_NS}serie"),
                  inf.findtext(f"{_NS}nDPS"))
        if not all(campos):
            return None
        try:
            return montar_id_dps(*campos)
        except ValidacaoErro:
            return None

    def _validador(self, leiaute: VersaoLeiaute) -> ValidadorXsd | None:
        pasta = self._opcoes.pasta_xsd.get(leiaute)
        if pasta is None:
            return None
        if leiaute not in self._validadores:
            self._validadores[leiaute] = ValidadorXsd(pasta)
        return self._validadores[leiaute]

    # ------------------------------------------------------------------
    # Envio e máquina de estados
    # ------------------------------------------------------------------

    def _enviar(self, preparada: _DpsPreparada, estado: EstadoEmissao,
                verificar_parametros: bool = False) -> RetornoEmissao:
        id_dps = preparada.id_dps
        registro = estado.obter(id_dps)
        if registro is not None:
            if registro.situacao is SituacaoEmissao.AUTORIZADA:
                return self._retorno_gravado(registro)
            if registro.situacao in SITUACOES_A_RECONCILIAR:
                try:
                    retorno = self._reconciliar(estado, id_dps)
                except (ComunicacaoErro, RespostaInvalidaErro) as erro:
                    raise EmissaoIncertaErro(
                        f"A emissão anterior segue sem resultado e a consulta à Sefin falhou "
                        f"({erro}).", id_dps) from erro
                if retorno is not None:
                    return retorno
                estado.marcar(id_dps, SituacaoEmissao.PENDENTE, motivo="nao_localizada_na_sefin")
        if verificar_parametros:
            self._verificar_convenio(preparada.municipio, preparada.competencia)
        estado.reservar(id_dps, preparada.serie, preparada.numero)
        tentativa = estado.obter(id_dps).tentativas + 1
        caminho_dps = self._gravar_arquivo(preparada, tentativa, "dps.xml", preparada.xml)
        estado.marcar(id_dps, SituacaoEmissao.ENVIADA, caminho_dps=caminho_dps)

        try:
            resposta = self._cliente().emitir(preparada.xml, id_dps)
        except ComunicacaoErro as erro:
            return self._resolver_incerta(estado, id_dps, str(erro))
        caminho_resposta = self._gravar_arquivo(preparada, tentativa, "resposta.json",
                                                resposta.corpo)
        try:
            retorno = interpretar_emissao(resposta, id_dps)
        except RejeicaoNfseErro as rejeicao:
            return self._tratar_rejeicao(estado, id_dps, rejeicao, caminho_resposta)
        except (EmissaoIncertaErro, RespostaInvalidaErro) as erro:
            return self._resolver_incerta(estado, id_dps, str(erro), caminho_resposta)
        return self._acatar(estado, id_dps, retorno, caminho_resposta)

    def _tratar_rejeicao(self, estado: EstadoEmissao, id_dps: str, rejeicao: RejeicaoNfseErro,
                         caminho_resposta: str) -> RetornoEmissao:
        """E0014 significa que a DPS já gerou NFS-e: reconcilia em vez de falhar."""
        if "E0014" in rejeicao.codigos:
            retorno = self._reconciliar(estado, id_dps)
            if retorno is not None:
                return retorno
        estado.marcar(id_dps, SituacaoEmissao.REJEITADA, caminho_resposta=caminho_resposta,
                      erros=[asdict(erro) for erro in rejeicao.erros])
        self._auditar("emitir", id_dps, SituacaoEmissao.REJEITADA, codigos=rejeicao.codigos)
        raise rejeicao

    def _resolver_incerta(self, estado: EstadoEmissao, id_dps: str, causa: str,
                          caminho_resposta: str | None = None) -> RetornoEmissao:
        """Timeout/5xx: consulta a Sefin uma vez; nunca reenvia por conta própria."""
        estado.marcar(id_dps, SituacaoEmissao.INCERTA, motivo=causa,
                      caminho_resposta=caminho_resposta)
        try:
            retorno = self._reconciliar(estado, id_dps)
        except (ComunicacaoErro, RespostaInvalidaErro) as erro:
            self._auditar("emitir", id_dps, SituacaoEmissao.INCERTA)
            raise EmissaoIncertaErro(f"{causa} A consulta à Sefin também falhou ({erro}).",
                                     id_dps) from erro
        if retorno is not None:
            return retorno
        self._auditar("emitir", id_dps, SituacaoEmissao.INCERTA)
        raise EmissaoIncertaErro(causa, id_dps, nao_localizada=True)

    def _reconciliar(self, estado: EstadoEmissao, id_dps: str) -> RetornoEmissao | None:
        """``HEAD`` -> ``GET /dps`` -> ``GET /nfse``. ``None`` se a Sefin não conhece a DPS."""
        cliente = self._cliente()
        if not cliente.dps_processada(id_dps):
            return None
        chave = cliente.chave_da_dps(id_dps)
        if chave is None:
            raise RespostaInvalidaErro(
                f"A Sefin confirma a DPS {id_dps} mas não devolveu a chave "
                "(o certificado precisa ser de um ator da nota).")
        retorno = replace(interpretar_consulta_nfse(cliente.consultar_nfse(chave)), id_dps=id_dps)
        estado.garantir(id_dps, int(id_dps[25:30]), int(id_dps[30:45]))
        return self._acatar(estado, id_dps, retorno, None)

    def _reconciliar_registro(self, estado: EstadoEmissao, id_dps: str,
                              descartar: bool) -> RetornoEmissao | None:
        registro = estado.garantir(id_dps, int(id_dps[25:30]), int(id_dps[30:45]))
        if registro.situacao is SituacaoEmissao.AUTORIZADA:
            return self._retorno_gravado(registro)
        retorno = self._reconciliar(estado, id_dps)
        if retorno is None and descartar:
            estado.marcar(id_dps, SituacaoEmissao.REJEITADA, motivo="nao_localizada")
        return retorno

    def _acatar(self, estado: EstadoEmissao, id_dps: str, retorno: RetornoEmissao,
                caminho_resposta: str | None) -> RetornoEmissao:
        """Confere, armazena a NFS-e (XML original) e marca a DPS como autorizada."""
        if ler_chave_do_xml(retorno.xml_nfse) != retorno.chave:
            estado.marcar(id_dps, SituacaoEmissao.INCERTA, caminho_resposta=caminho_resposta,
                          motivo="O Id da NFS-e devolvida difere da chave de acesso.")
            raise RespostaInvalidaErro(
                f"O Id da NFS-e devolvida não confere com a chave {retorno.chave}.")
        if self._opcoes.verificar_assinatura_retorno:
            raiz = etree.fromstring(retorno.xml_nfse, etree.XMLParser(resolve_entities=False))
            if not verificar_assinatura(raiz):
                logger.warning("A assinatura da NFS-e %s não pôde ser conferida.", retorno.chave)
        self._repositorio.armazenar([DocumentoRecebido(xml=retorno.xml_nfse)])
        dados = {"chave": retorno.chave}
        if caminho_resposta:
            dados["caminho_resposta"] = caminho_resposta
        estado.marcar(id_dps, SituacaoEmissao.AUTORIZADA, **dados)
        self._auditar("emitir", id_dps, SituacaoEmissao.AUTORIZADA, chave=retorno.chave)
        return retorno

    def _retorno_gravado(self, registro: RegistroEmissao) -> RetornoEmissao:
        """NFS-e já autorizada: devolve a do repositório (ou a busca na Sefin se sumiu)."""
        resumo = self._repositorio.obter(ModeloDocumento.NFSE, registro.chave)
        if resumo is None:
            return replace(self.consultar_chave(registro.chave), id_dps=registro.id_dps)
        return RetornoEmissao(chave=registro.chave, xml_nfse=self._repositorio.ler_xml(resumo),
                              status_http=200, id_dps=registro.id_dps)

    # ------------------------------------------------------------------
    # Infraestrutura
    # ------------------------------------------------------------------

    def _leiaute(self, explicito: VersaoLeiaute | None) -> VersaoLeiaute:
        if explicito is not None:
            return VersaoLeiaute(explicito)
        if self._opcoes.leiaute is not None:
            return self._opcoes.leiaute
        hoje = self._relogio().astimezone(FUSO_BRASILIA).date()
        return VersaoLeiaute.selecionar(self._ambiente, hoje)

    def _cliente(self) -> ClienteSefin:
        return ClienteSefin(self.configuracao, self._obter_transporte(), self._auditoria,
                            pausa=self._pausa, esperas=self._opcoes.esperas_consulta)

    def _trava(self) -> Trava:
        return self._travas("emissao_nfse")

    def _novo_estado(self) -> EstadoEmissao:
        """Estado das emissões; o padrão em arquivo é recriado e relido a cada operação."""
        return self._fabrica_estado_emissao()

    def _gravar_arquivo(self, preparada: _DpsPreparada, tentativa: int, sufixo: str,
                        conteudo: bytes) -> str:
        """Evidência da DPS enviada ou da resposta; reenvios ganham a numeração da tentativa."""
        return self._evidencias.gravar_dps(
            preparada.id_dps, preparada.mes, tentativa, sufixo, conteudo)

    def _auditar(self, operacao: str, id_dps: str, situacao: SituacaoEmissao,
                 chave: str | None = None, codigos: list[str] | None = None) -> None:
        self._auditoria.registrar(servico="nfse", operacao=operacao, id_dps=id_dps,
                                  situacao=situacao.value, chave=chave, codigos=codigos or [])
