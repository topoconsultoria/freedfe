"""freedfe: consulta, download de XML e geração de PDF de DF-e de interesse de um CNPJ/CPF.

Uso básico::

    from freedfe import FreeDFe, FiltroConsulta, ModeloDocumento

    dfe = FreeDFe("cert.pfx", os.environ["SEFAZ_PFX_SENHA"], cnpj="...", uf="29")
    dfe.sincronizar()
    documentos = dfe.consultar(FiltroConsulta(data_inicial=date(2026, 7, 1)), formato="xlsx",
                               destino="dfe.xlsx")
"""

from freedfe.certificado import CertificadoA1, InfoCertificado
from freedfe.distribuicao.cliente import ResultadoSincronizacao
from freedfe.documentos.resumo import EventoDocumento, ResumoDocumento
from freedfe.excecoes import (
    CertificadoErro,
    ComunicacaoErro,
    DependenciaAusenteErro,
    FreeDFeErro,
    LimiteConsultaErro,
    RespostaInvalidaErro,
    SincronizacaoEmAndamentoErro,
    ValidacaoErro,
)
from freedfe.exportacao.formato import FormatoSaida
from freedfe.fachada import FreeDFe, ResultadoGeracaoPdf
from freedfe.repositorio.filtro import Direcao, FiltroConsulta
from freedfe.tipos import Ambiente, ModeloDocumento, SituacaoDocumento

__version__ = "0.1.0"

__all__ = [
    "Ambiente",
    "CertificadoA1",
    "CertificadoErro",
    "ComunicacaoErro",
    "DependenciaAusenteErro",
    "Direcao",
    "EventoDocumento",
    "FiltroConsulta",
    "FormatoSaida",
    "FreeDFe",
    "FreeDFeErro",
    "InfoCertificado",
    "LimiteConsultaErro",
    "ModeloDocumento",
    "RespostaInvalidaErro",
    "ResultadoGeracaoPdf",
    "ResultadoSincronizacao",
    "ResumoDocumento",
    "SincronizacaoEmAndamentoErro",
    "SituacaoDocumento",
    "ValidacaoErro",
]
