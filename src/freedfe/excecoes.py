"""Exceções da biblioteca freedfe.

Todas herdam de ``FreeDFeErro``, o que permite ao chamador capturar qualquer
falha da biblioteca com um único ``except``.
"""


class FreeDFeErro(Exception):
    """Erro base da biblioteca."""


class ValidacaoErro(FreeDFeErro, ValueError):
    """Parâmetro inválido (CNPJ, CPF, chave, NSU, UF, justificativa etc.)."""


class CertificadoErro(FreeDFeErro):
    """Certificado A1 ilegível, sem chave privada, com senha errada ou vencido."""


class ComunicacaoErro(FreeDFeErro):
    """Falha de transporte (TLS, HTTP, timeout) ao falar com o Web Service."""


class RespostaInvalidaErro(FreeDFeErro):
    """Resposta do Web Service sem o elemento esperado (ex.: SOAP Fault)."""


class LimiteConsultaErro(FreeDFeErro):
    """Limite local de consultas pontuais por hora atingido (evita rejeição 656)."""


class SincronizacaoEmAndamentoErro(FreeDFeErro):
    """Outro processo já está consumindo a mesma sequência de NSU."""


class DependenciaAusenteErro(FreeDFeErro, ImportError):
    """Dependência opcional não instalada (ex.: extra ``pdf`` ou ``xlsx``)."""
