"""freedfe: DF-e de interesse de um CNPJ/CPF (NF-e, CT-e, MDF-e) e NFS-e Nacional.

Uso básico::

    from freedfe import FreeDFe, FiltroConsulta, ModeloDocumento

    dfe = FreeDFe("cert.pfx", os.environ["SEFAZ_PFX_SENHA"], cnpj="...", uf="29")
    dfe.sincronizar()
    documentos = dfe.consultar(FiltroConsulta(data_inicial=date(2026, 7, 1)), formato="xlsx",
                               destino="dfe.xlsx")

NFS-e Nacional (emissão, consulta, cancelamento e DANFSe)::

    retorno = dfe.nfse.emitir(Dps(...))
    dfe.nfse.gerar_danfse(retorno.chave)
"""

from freedfe._versao import __version__
from freedfe.certificado import CertificadoA1, InfoCertificado
from freedfe.distribuicao.cliente import ResultadoSincronizacao
from freedfe.distribuicao.estado import EstadoNSU, EstadoSequenciaNsu
from freedfe.distribuicao.trava import Trava, TravaArquivo
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento
from freedfe.excecoes import (
    CertificadoErro,
    ComunicacaoErro,
    DependenciaAusenteErro,
    EmissaoIncertaErro,
    ErroNfse,
    FreeDFeErro,
    LimiteConsultaErro,
    RejeicaoNfseErro,
    RespostaInvalidaErro,
    SincronizacaoEmAndamentoErro,
    ValidacaoErro,
)
from freedfe.exportacao.formato import FormatoSaida
from freedfe.fachada import FreeDFe, ResultadoGeracaoPdf
from freedfe.nfse.estado_emissao import (
    EstadoEmissao,
    EstadoEmissaoArquivo,
    RegistroEmissao,
    SituacaoEmissao,
)
from freedfe.nfse.eventos import MotivoCancelamento, TipoEventoNfse
from freedfe.nfse.leiaute import VersaoLeiaute
from freedfe.nfse.modelo import (
    DiferimentoIbsCbs,
    Dps,
    Endereco,
    IbsCbs,
    Intermediario,
    Obra,
    OpcaoSimplesNacional,
    Pessoa,
    Prestador,
    RegimeApuracaoSimples,
    RegimeEspecialTributacao,
    RetencaoIssqn,
    Servico,
    Substituicao,
    TipoEmitente,
    Tomador,
    TotTrib,
    TribFederal,
    TribMunicipal,
    TributacaoIssqn,
    Valores,
)
from freedfe.nfse.resposta import RetornoEmissao, RetornoEventoNfse
from freedfe.nfse.servico import ConfiguracaoNfseLocal, ServicoNfse
from freedfe.repositorio.filtro import Direcao, FiltroConsulta
from freedfe.tipos import Ambiente, ModeloDocumento, SituacaoDocumento

__all__ = [
    "Ambiente",
    "CertificadoA1",
    "CertificadoErro",
    "ComunicacaoErro",
    "ConfiguracaoNfseLocal",
    "DependenciaAusenteErro",
    "DiferimentoIbsCbs",
    "Direcao",
    "Dps",
    "EmissaoIncertaErro",
    "Endereco",
    "ErroNfse",
    "EstadoEmissao",
    "EstadoEmissaoArquivo",
    "EstadoNSU",
    "EstadoSequenciaNsu",
    "EventoDocumento",
    "FiltroConsulta",
    "FormatoSaida",
    "FreeDFe",
    "FreeDFeErro",
    "IbsCbs",
    "InfoCertificado",
    "Intermediario",
    "LimiteConsultaErro",
    "ModeloDocumento",
    "MotivoCancelamento",
    "Obra",
    "OpcaoSimplesNacional",
    "Pessoa",
    "Prestador",
    "RegimeApuracaoSimples",
    "RegimeEspecialTributacao",
    "RegistroEmissao",
    "RejeicaoNfseErro",
    "RespostaInvalidaErro",
    "ResultadoGeracaoPdf",
    "ResultadoSincronizacao",
    "ResumoDocumento",
    "RetencaoIssqn",
    "RetornoEmissao",
    "RetornoEventoNfse",
    "Servico",
    "ServicoNfse",
    "SincronizacaoEmAndamentoErro",
    "SituacaoDocumento",
    "SituacaoEmissao",
    "Substituicao",
    "TipoEmitente",
    "TipoEventoNfse",
    "Tomador",
    "TotTrib",
    "Trava",
    "TravaArquivo",
    "TribFederal",
    "TribMunicipal",
    "TributacaoIssqn",
    "ValidacaoErro",
    "Valores",
    "VersaoLeiaute",
    "__version__",
]
