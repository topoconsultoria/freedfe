"""Evidências de emissão e de eventos da NFS-e: contrato e implementação em arquivo.

Antes do ``POST`` da emissão, a DPS assinada é gravada como evidência de idempotência; a
resposta bruta e os pedidos de evento também. Onde isso fica (disco, bucket, banco) é decisão da
aplicação: ``ServicoNfse`` só conhece o contrato ``ArmazenamentoEvidencias``.
"""

from __future__ import annotations

import contextlib
import os
from abc import ABC, abstractmethod
from pathlib import Path


class ArmazenamentoEvidencias(ABC):
    """Contrato do armazenamento das evidências enviadas e recebidas.

    Garantias que toda implementação deve oferecer:

    - **Durabilidade:** ao retornar, o conteúdo está persistido de forma durável (em disco com
      ``fsync``, em banco confirmado, em objeto gravado...). A DPS é a evidência de idempotência
      gravada **antes** do ``POST``: se o processo cair depois do envio, é ela que permite
      reconciliar sem reenviar.
    - **Nunca sobrescrever:** tentativas diferentes da mesma DPS e eventos repetidos geram
      entradas novas, nunca substituem as anteriores. Regravar a mesma tentativa da mesma DPS
      (uma reexecução depois de uma queda) pode substituir o conteúdo idêntico.
    - **Entradas já validadas:** ``mes`` chega no formato ``AAAA-MM`` (ou ``sem-data``) e os Ids
      chegam validados; ``sufixo`` é ``<base>.<extensão>`` (``dps.xml``, ``resposta.json``,
      ``pedido.xml``).
    - **Referência opaca:** o texto devolvido é guardado em ``RegistroEmissao.caminho_dps`` e
      ``caminho_resposta`` e serve para localizar a evidência depois. Cada implementação decide o
      formato (um caminho relativo, uma chave de objeto, um Id de linha).
    """

    @abstractmethod
    def gravar_dps(self, id_dps: str, mes: str, tentativa: int, sufixo: str,
                   conteudo: bytes) -> str:
        """Grava a DPS enviada (``dps.xml``) ou a resposta bruta (``resposta.json``).

        Args:
            id_dps: Id da DPS (45 posições).
            mes: ``AAAA-MM`` da emissão, ou ``sem-data``.
            tentativa: número do envio (1 é o primeiro; reenvios após rejeição somam 1).
            sufixo: ``<base>.<extensão>`` do artefato.
            conteudo: bytes exatamente como trafegaram.

        Returns:
            A referência da evidência gravada.
        """

    @abstractmethod
    def gravar_evento(self, chave: str, tipo_evento: str, mes: str, sufixo: str,
                      conteudo: bytes) -> str:
        """Grava o pedido de evento enviado (``pedido.xml``) ou a resposta (``resposta.json``).

        Eventos repetidos para a mesma nota e o mesmo tipo geram entradas novas.

        Args:
            chave: chave de acesso da NFS-e (50 posições).
            tipo_evento: código do evento (por exemplo, ``101101``).
            mes: ``AAAA-MM`` do envio.
            sufixo: ``<base>.<extensão>`` do artefato.
            conteudo: bytes exatamente como trafegaram.

        Returns:
            A referência da evidência gravada.
        """


class ArmazenamentoEvidenciasArquivo(ArmazenamentoEvidencias):
    """Evidências em arquivos sob a pasta de dados, com ``fsync`` ao gravar.

    Caminhos (relativos à ``pasta``, devolvidos em formato POSIX)::

        nfse/dps/<mes>/<idDPS>-<base>[-<tentativa>].<ext>
        nfse/eventos_enviados/<mes>/<chave>-<tipo>-<base>[-<n>].<ext>

    Na DPS, a primeira tentativa não leva número (``...-dps.xml``) e as seguintes levam
    ``-2``, ``-3``... Nos eventos, a primeira gravação não leva número e as repetições levam
    ``-2``, ``-3``... (a criação é exclusiva, então dois processos não escolhem o mesmo nome).

    Args:
        pasta: raiz dos dados (``FreeDFe.pasta``).
    """

    def __init__(self, pasta: str | Path):
        self._pasta = Path(pasta)

    def gravar_dps(self, id_dps: str, mes: str, tentativa: int, sufixo: str,
                   conteudo: bytes) -> str:
        base, extensao = sufixo.rsplit(".", 1)
        nome = base if tentativa == 1 else f"{base}-{tentativa}"
        relativo = Path("nfse") / "dps" / mes / f"{id_dps}-{nome}.{extensao}"
        destino = self._pasta / relativo
        destino.parent.mkdir(parents=True, exist_ok=True)
        _gravar_duravel(destino, conteudo)
        return relativo.as_posix()

    def gravar_evento(self, chave: str, tipo_evento: str, mes: str, sufixo: str,
                      conteudo: bytes) -> str:
        base, extensao = sufixo.rsplit(".", 1)
        pasta = Path("nfse") / "eventos_enviados" / mes
        (self._pasta / pasta).mkdir(parents=True, exist_ok=True)
        indice = 0
        while True:
            indice += 1
            nome = base if indice == 1 else f"{base}-{indice}"
            relativo = pasta / f"{chave}-{tipo_evento}-{nome}.{extensao}"
            try:
                _gravar_duravel(self._pasta / relativo, conteudo, exclusivo=True)
            except FileExistsError:
                continue
            return relativo.as_posix()


def _gravar_duravel(destino: Path, conteudo: bytes, exclusivo: bool = False) -> None:
    """Grava e força o conteúdo (e a entrada de diretório) ao disco antes de retornar."""
    with destino.open("xb" if exclusivo else "wb") as saida:
        saida.write(conteudo)
        saida.flush()
        os.fsync(saida.fileno())
    with contextlib.suppress(OSError):  # abrir diretório não é possível em todos os sistemas
        descritor = os.open(destino.parent, os.O_RDONLY)
        try:
            os.fsync(descritor)
        finally:
            os.close(descritor)
