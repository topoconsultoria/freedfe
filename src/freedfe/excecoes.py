"""Exceções da biblioteca freedfe.

Todas herdam de ``FreeDFeErro``, o que permite ao chamador capturar qualquer
falha da biblioteca com um único ``except``.
"""

from __future__ import annotations

from dataclasses import dataclass


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


@dataclass(frozen=True)
class ErroNfse:
    """Erro ou alerta de negócio devolvido pela API da NFS-e Nacional (ex.: ``E0014``)."""

    codigo: str
    descricao: str = ""
    complemento: str = ""

    def __str__(self) -> str:
        texto = f"{self.codigo}: {self.descricao}" if self.descricao else self.codigo
        return f"{texto} ({self.complemento})" if self.complemento else texto


class RejeicaoNfseErro(FreeDFeErro):
    """A API da NFS-e Nacional rejeitou a requisição (HTTP 4xx com ``erros``).

    ``erros`` traz os códigos e descrições devolvidos pela Sefin; ``id_dps`` identifica a
    DPS rejeitada quando a rejeição ocorreu na emissão.
    """

    def __init__(self, erros: list[ErroNfse], status_http: int | None = None,
                 id_dps: str | None = None):
        self.erros = list(erros)
        self.status_http = status_http
        self.id_dps = id_dps
        detalhe = "; ".join(str(erro) for erro in self.erros) or "sem detalhe"
        super().__init__(f"NFS-e rejeitada pela Sefin (HTTP {status_http}): {detalhe}")

    @property
    def codigos(self) -> list[str]:
        """Códigos de erro devolvidos (ex.: ``["E0712"]``)."""
        return [erro.codigo for erro in self.erros]


class EmissaoIncertaErro(ComunicacaoErro):
    """Timeout ou erro 5xx na emissão, sem confirmação de que a Sefin recebeu a DPS.

    A DPS pode ou não ter sido processada. **Nunca reenvie a requisição** sem antes chamar
    ``ServicoNfse.reconciliar(id_dps)``, que consulta a Sefin pelo Id da DPS.

    Com ``nao_localizada=True`` a reconciliação já foi feita e a Sefin confirmou que não possui
    a DPS: chamar ``emitir`` de novo, com o mesmo número, é seguro.
    """

    def __init__(self, mensagem: str, id_dps: str, *, nao_localizada: bool = False):
        self.id_dps = id_dps
        self.nao_localizada = nao_localizada
        if nao_localizada:
            orientacao = (f"A Sefin confirmou que não possui a DPS {id_dps}: é seguro chamar "
                          "emitir() novamente com o mesmo número.")
        else:
            orientacao = (f"Emissão incerta para a DPS {id_dps}: chame reconciliar() "
                          "antes de qualquer novo envio.")
        super().__init__(f"{mensagem} {orientacao}")
