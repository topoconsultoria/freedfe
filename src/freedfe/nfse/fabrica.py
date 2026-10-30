"""Fábrica pública do serviço de NFS-e sem pasta local.

``FreeDFe`` monta o ``ServicoNfse`` a partir de uma pasta de dados. Uma aplicação que guarda tudo
em outro lugar (banco, objetos, cofre) usa ``criar_servico_nfse`` e injeta cada peça da
persistência; nada é gravado em disco por conta da biblioteca (exceto o PEM temporário do mTLS em
sistemas sem ``memfd``; ver ``CertificadoA1.arquivos_pem``).
"""

from __future__ import annotations

from collections.abc import Callable

from freedfe.auditoria import Auditoria
from freedfe.certificado import CertificadoA1
from freedfe.distribuicao.estado import FabricaEstadoNsu, Relogio, relogio_utc
from freedfe.distribuicao.trava import FabricaTrava
from freedfe.interessado import Interessado
from freedfe.nfse.estado_emissao import EstadoEmissao
from freedfe.nfse.evidencias import ArmazenamentoEvidencias
from freedfe.nfse.parametros import CacheParametros
from freedfe.nfse.servico import ConfiguracaoNfseLocal, ServicoNfse
from freedfe.pdf.gerador import GeradorPdf
from freedfe.repositorio.base import RepositorioDocumentos
from freedfe.tipos import Ambiente
from freedfe.transporte import ClienteRest, TransporteRest


def criar_servico_nfse(*, ambiente: Ambiente, interessado: Interessado,
                       certificado: CertificadoA1, repositorio: RepositorioDocumentos,
                       estado_emissao: EstadoEmissao, travas: FabricaTrava,
                       estado_nsu: FabricaEstadoNsu, cache_parametros: CacheParametros,
                       evidencias: ArmazenamentoEvidencias, auditoria: Auditoria,
                       transporte: TransporteRest | None = None,
                       gerador_pdf: GeradorPdf | None = None,
                       opcoes: ConfiguracaoNfseLocal | None = None,
                       relogio: Relogio = relogio_utc,
                       pausa: Callable[[float], None] | None = None) -> ServicoNfse:
    """Cria um ``ServicoNfse`` sem pasta local, com toda a persistência injetada.

    Todos os parâmetros são nomeados e **não há parâmetro ``pasta``**: o serviço não recebe nem
    deduz um diretório, e nenhuma dependência de persistência tem padrão em arquivo. Para o PDF
    do DANFSe use ``ServicoNfse.danfse_bytes`` (``gerar_danfse`` exige um ``destino``).

    Args:
        ambiente: produção ou homologação (produção restrita).
        interessado: CNPJ/CPF do emitente (deve ser o titular do certificado, E0718).
        certificado: certificado A1, por exemplo ``CertificadoA1(conteudo_pfx, senha)`` com o
            conteúdo vindo de um cofre, sem arquivo.
        repositorio: repositório dos XML (NFS-e e eventos).
        estado_emissao: estado das emissões e numeração (contrato ``EstadoEmissao``).
        travas: fábrica de travas por recurso (``emissao_nfse``, ``nsu_nfse``,
            ``nsu_nfse_<cnpj>``).
        estado_nsu: fábrica do estado de NSU do ADN por recurso (``nsu_nfse``,
            ``nsu_nfse_<cnpj>``).
        cache_parametros: cache dos parâmetros municipais.
        evidencias: armazenamento da DPS enviada, das respostas e dos pedidos de evento.
        auditoria: trilha de auditoria (sem XML).
        transporte: transporte REST com mTLS. Padrão: ``ClienteRest(certificado, proxy=None)``,
            criado na primeira chamada de rede. Para usar proxy, ``ca_bundle`` próprio ou outros
            prazos, a aplicação passa o seu próprio transporte.
        gerador_pdf: gerador do DANFSe (padrão: ``GeradorPdfPadrao``). Para não usar disco,
            o gerador precisa sobrescrever ``gerar_bytes``.
        opcoes: leiaute, XSD, prefixo da rota, URL do QR Code, esperas etc.
        relogio: relógio UTC injetável.
        pausa: função de espera entre tentativas e lotes (padrão ``time.sleep``).

    Returns:
        O serviço, com a mesma API de ``FreeDFe.nfse``.
    """
    transporte_padrao: list[TransporteRest] = []

    def obter_transporte() -> TransporteRest:
        if transporte is not None:
            return transporte
        if not transporte_padrao:
            transporte_padrao.append(ClienteRest(certificado, proxy=None))
        return transporte_padrao[0]

    return ServicoNfse(
        ambiente=ambiente, interessado=interessado, pasta=None, repositorio=repositorio,
        auditoria=auditoria, obter_certificado=lambda: certificado,
        obter_transporte=obter_transporte, relogio=relogio, opcoes=opcoes, pausa=pausa,
        gerador_pdf=gerador_pdf, estado_emissao=estado_emissao, travas=travas,
        estado_nsu=estado_nsu, cache_parametros=cache_parametros, evidencias=evidencias)
