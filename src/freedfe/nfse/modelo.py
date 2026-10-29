"""Modelo tipado da DPS (Declaração de Prestação de Serviços) da NFS-e Nacional.

Cobre o subconjunto usado por prestadores de serviço em geral, com a estrutura do XSD oficial
``DPS_v1.01`` (leiaute da NT 004 v2.0). O que não couber aqui (comércio exterior, evento,
reembolso de IBS/CBS, dedução por documentos, etc.) entra pelo caminho "XML pronto" de
``ServicoNfse.emitir(bytes)``.

Princípios:
    - dataclasses imutáveis; toda validação local roda na construção e levanta ``ValidacaoErro``;
    - textos livres são sanitizados (Latin-1, sem tipografia de editor) e **nunca truncados**:
      excedeu o tamanho do schema, o erro é do chamador;
    - valores monetários e percentuais têm no máximo 2 casas (sem arredondamento silencioso);
    - só regras certas do schema e do relatório técnico são validadas aqui. As regras de
      negócio do Anexo I (ex.: opção de ``totTrib`` por regime) ficam com a Sefin, que devolve
      o código de rejeição.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum, IntEnum
from typing import Any

from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.identificadores import normalizar_numero_dps, normalizar_serie
from freedfe.nfse.texto import sanitizar
from freedfe.validadores import (
    exigir_chave_nfse,
    exigir_codigo_ibge,
    validar_cnpj,
    validar_cpf,
)

# ---------------------------------------------------------------------------
# Enumerações (valores do XSD)
# ---------------------------------------------------------------------------


class TipoEmitente(IntEnum):
    """``tpEmit``: quem emite a DPS."""

    PRESTADOR = 1
    TOMADOR = 2
    INTERMEDIARIO = 3


class OpcaoSimplesNacional(IntEnum):
    """``opSimpNac``: situação do prestador perante o Simples Nacional."""

    NAO_OPTANTE = 1
    MEI = 2
    ME_EPP = 3


class RegimeApuracaoSimples(IntEnum):
    """``regApTribSN``: apuração dos tributos do Simples (exigida para ME/EPP)."""

    FEDERAIS_E_MUNICIPAL_PELO_SIMPLES = 1
    FEDERAIS_PELO_SIMPLES_ISSQN_FORA = 2
    FEDERAIS_E_MUNICIPAL_FORA_DO_SIMPLES = 3


class RegimeEspecialTributacao(IntEnum):
    """``regEspTrib``."""

    NENHUM = 0
    ATO_COOPERADO = 1
    ESTIMATIVA = 2
    MICROEMPRESA_MUNICIPAL = 3
    NOTARIO_OU_REGISTRADOR = 4
    PROFISSIONAL_AUTONOMO = 5
    SOCIEDADE_DE_PROFISSIONAIS = 6
    OUTROS = 9


class TributacaoIssqn(IntEnum):
    """``tribISSQN``."""

    TRIBUTAVEL = 1
    IMUNIDADE = 2
    EXPORTACAO = 3
    NAO_INCIDENCIA = 4


class RetencaoIssqn(IntEnum):
    """``tpRetISSQN``."""

    NAO_RETIDO = 1
    RETIDO_PELO_TOMADOR = 2
    RETIDO_PELO_INTERMEDIARIO = 3


class TipoImunidade(IntEnum):
    """``tpImunidade``."""

    NAO_INFORMADA = 0
    PATRIMONIO_RENDA_SERVICOS_ENTES = 1
    TEMPLOS = 2
    PARTIDOS_E_ENTIDADES = 3
    LIVROS_E_PERIODICOS = 4
    FONOGRAMAS_E_VIDEOFONOGRAMAS = 5


class TipoSuspensao(IntEnum):
    """``tpSusp``: exigibilidade suspensa."""

    DECISAO_JUDICIAL = 1
    PROCESSO_ADMINISTRATIVO = 2


class TipoTotTrib(Enum):
    """Opção escolhida no ``xs:choice`` de ``totTrib``."""

    VALORES = "vTotTrib"
    PERCENTUAIS = "pTotTrib"
    NAO_INFORMADO = "indTotTrib"
    PERCENTUAL_SIMPLES = "pTotTribSN"


# ---------------------------------------------------------------------------
# Auxiliares de validação
# ---------------------------------------------------------------------------

_DOIS_DECIMAIS = Decimal("0.01")


def _fixar(objeto: Any, **campos: Any) -> None:
    """Grava campos normalizados em dataclass imutável."""
    for nome, valor in campos.items():
        object.__setattr__(objeto, nome, valor)


def _decimal(valor: Any, campo: str, *, inteiros: int = 15,
             obrigatorio: bool = False) -> Decimal | None:
    """Converte para ``Decimal`` com no máximo 2 casas e ``inteiros`` dígitos inteiros."""
    if valor is None or valor == "":
        if obrigatorio:
            raise ValidacaoErro(f"{campo} é obrigatório.")
        return None
    if isinstance(valor, float):
        raise ValidacaoErro(f"{campo}: use Decimal ou texto, não float (perda de precisão).")
    try:
        numero = Decimal(str(valor).strip())
    except InvalidOperation:
        raise ValidacaoErro(f"{campo}: valor numérico inválido: {valor!r}") from None
    if not numero.is_finite() or numero < 0:
        raise ValidacaoErro(f"{campo} deve ser um número não negativo: {valor!r}")
    if numero != numero.quantize(_DOIS_DECIMAIS):
        raise ValidacaoErro(
            f"{campo} aceita no máximo 2 casas decimais (arredonde antes): {valor!r}")
    numero = numero.quantize(_DOIS_DECIMAIS)
    if numero >= Decimal(10) ** inteiros:
        raise ValidacaoErro(f"{campo} excede {inteiros} dígitos inteiros: {valor!r}")
    return numero


def _texto(valor: str | None, campo: str, maximo: int, *, obrigatorio: bool = False,
           quebra_linha: bool = False) -> str | None:
    limpo = sanitizar(valor, quebra_linha=quebra_linha)
    if not limpo:
        if obrigatorio:
            raise ValidacaoErro(f"{campo} é obrigatório.")
        return None
    if len(limpo) > maximo:
        raise ValidacaoErro(f"{campo} excede {maximo} caracteres ({len(limpo)}).")
    return limpo


def _digitos(valor: Any, campo: str, tamanho: int | tuple[int, int], *,
             obrigatorio: bool = False) -> str | None:
    if valor is None or str(valor).strip() == "":
        if obrigatorio:
            raise ValidacaoErro(f"{campo} é obrigatório.")
        return None
    texto = re.sub(r"\D", "", str(valor))
    minimo, maximo = (tamanho, tamanho) if isinstance(tamanho, int) else tamanho
    if not minimo <= len(texto) <= maximo:
        esperado = str(minimo) if minimo == maximo else f"{minimo} a {maximo}"
        raise ValidacaoErro(f"{campo} deve ter {esperado} dígitos: {valor!r}")
    return texto


def _enum(tipo: type, valor: Any, campo: str, *, obrigatorio: bool = True):
    if valor is None:
        if obrigatorio:
            raise ValidacaoErro(f"{campo} é obrigatório.")
        return None
    try:
        return tipo(valor)
    except ValueError:
        validos = ", ".join(str(int(m)) for m in tipo)
        raise ValidacaoErro(f"{campo} inválido: {valor!r} (válidos: {validos})") from None


# ---------------------------------------------------------------------------
# Endereço e pessoas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Endereco:
    """Endereço nacional (``endNac``) ou no exterior (``endExt``).

    Use :meth:`nacional` ou :meth:`exterior`.
    """

    logradouro: str
    numero: str
    bairro: str
    complemento: str | None = None
    codigo_municipio: str | None = None
    cep: str | None = None
    pais: str | None = None
    codigo_postal: str | None = None
    cidade: str | None = None
    estado: str | None = None

    def __post_init__(self) -> None:
        nacional = bool(self.codigo_municipio or self.cep)
        exterior = bool(self.pais or self.codigo_postal or self.cidade or self.estado)
        if nacional == exterior:
            raise ValidacaoErro("Endereço: informe município/CEP (nacional) ou país/cidade "
                                "(exterior), não ambos nem nenhum.")
        _fixar(self,
               logradouro=_texto(self.logradouro, "Logradouro", 255, obrigatorio=True),
               numero=_texto(self.numero, "Número do endereço", 60, obrigatorio=True),
               bairro=_texto(self.bairro, "Bairro", 60, obrigatorio=True),
               complemento=_texto(self.complemento, "Complemento", 156))
        if nacional:
            _fixar(self,
                   codigo_municipio=exigir_codigo_ibge(self.codigo_municipio or "", "endereço"),
                   cep=_digitos(self.cep, "CEP", 8, obrigatorio=True))
        else:
            pais = (self.pais or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", pais):
                raise ValidacaoErro(f"País deve ser o código ISO de 2 letras: {self.pais!r}")
            _fixar(self, pais=pais,
                   codigo_postal=_texto(self.codigo_postal, "Código postal", 11, obrigatorio=True),
                   cidade=_texto(self.cidade, "Cidade", 60, obrigatorio=True),
                   estado=_texto(self.estado, "Estado/província", 60, obrigatorio=True))

    @property
    def exterior(self) -> bool:
        """``True`` para endereço no exterior."""
        return self.pais is not None

    @classmethod
    def nacional(cls, logradouro: str, numero: str, bairro: str, codigo_municipio: str,
                 cep: str, complemento: str | None = None) -> Endereco:
        """Endereço no Brasil (``cMun`` com 7 dígitos IBGE e CEP de 8 dígitos)."""
        return cls(logradouro, numero, bairro, complemento, codigo_municipio=codigo_municipio,
                   cep=cep)

    @classmethod
    def no_exterior(cls, logradouro: str, numero: str, bairro: str, pais: str,
                    codigo_postal: str, cidade: str, estado: str,
                    complemento: str | None = None) -> Endereco:
        """Endereço no exterior (``cPais`` ISO alfa-2)."""
        return cls(logradouro, numero, bairro, complemento, pais=pais,
                   codigo_postal=codigo_postal, cidade=cidade, estado=estado)


@dataclass(frozen=True)
class Pessoa:
    """Identificação de uma parte: exatamente um entre CNPJ, CPF, NIF ou motivo sem NIF."""

    nome: str | None = None
    cnpj: str | None = None
    cpf: str | None = None
    nif: str | None = None
    motivo_sem_nif: int | None = None
    inscricao_municipal: str | None = None
    endereco: Endereco | None = None
    telefone: str | None = None
    email: str | None = None

    #: Subclasses que exigem ``xNome`` (tomador, intermediário, destinatário).
    _nome_obrigatorio = False

    def __post_init__(self) -> None:
        informados = [c for c in (self.cnpj, self.cpf, self.nif, self.motivo_sem_nif)
                      if c not in (None, "")]
        if len(informados) != 1:
            raise ValidacaoErro("Informe exatamente um entre CNPJ, CPF, NIF ou motivo sem NIF.")
        cnpj = (self.cnpj or "").strip().upper() or None
        cpf = (self.cpf or "").strip() or None
        if cnpj and not validar_cnpj(cnpj):
            raise ValidacaoErro(f"CNPJ inválido: {self.cnpj!r} (E0188)")
        if cpf and not validar_cpf(cpf):
            raise ValidacaoErro(f"CPF inválido: {self.cpf!r} (E0188)")
        if self.motivo_sem_nif not in (None, 0, 1, 2):
            raise ValidacaoErro(f"Motivo sem NIF inválido: {self.motivo_sem_nif!r} (0, 1 ou 2)")
        telefone = _digitos(self.telefone, "Telefone", (6, 20))
        _fixar(self, cnpj=cnpj, cpf=cpf, telefone=telefone,
               nif=_texto(self.nif, "NIF", 40),
               nome=_texto(self.nome, "Nome/razão social", 300, obrigatorio=self._nome_obrigatorio),
               inscricao_municipal=_texto(self.inscricao_municipal, "Inscrição municipal", 15),
               email=_texto(self.email, "E-mail", 80))

    @property
    def documento(self) -> str | None:
        """CNPJ ou CPF informado (``None`` para NIF/motivo sem NIF)."""
        return self.cnpj or self.cpf


@dataclass(frozen=True)
class Prestador(Pessoa):
    """Prestador do serviço (``prest``), com o regime tributário (``regTrib``, obrigatório).

    Quando o prestador é o emitente, nome e endereço vêm do cadastro nacional: não informe o
    endereço (E0128). A situação perante o Simples deve conferir com o cadastro (E0160).
    """

    opcao_simples: OpcaoSimplesNacional | None = None
    regime_apuracao_simples: RegimeApuracaoSimples | None = None
    regime_especial: RegimeEspecialTributacao = RegimeEspecialTributacao.NENHUM

    def __post_init__(self) -> None:
        Pessoa.__post_init__(self)
        if self.cnpj is None and self.cpf is None:
            raise ValidacaoErro("O prestador deve ser identificado por CNPJ ou CPF.")
        opcao = _enum(OpcaoSimplesNacional, self.opcao_simples, "Opção pelo Simples Nacional")
        apuracao = _enum(RegimeApuracaoSimples, self.regime_apuracao_simples,
                         "Regime de apuração do Simples", obrigatorio=False)
        if opcao is OpcaoSimplesNacional.ME_EPP and apuracao is None:
            raise ValidacaoErro("ME/EPP optante exige o regime de apuração do Simples Nacional.")
        _fixar(self, opcao_simples=opcao, regime_apuracao_simples=apuracao,
               regime_especial=_enum(RegimeEspecialTributacao, self.regime_especial,
                                     "Regime especial de tributação"))


@dataclass(frozen=True)
class Tomador(Pessoa):
    """Tomador do serviço (``toma``). O nome é obrigatório."""

    _nome_obrigatorio = True


@dataclass(frozen=True)
class Intermediario(Pessoa):
    """Intermediário do serviço (``interm``). O nome é obrigatório."""

    _nome_obrigatorio = True


@dataclass(frozen=True)
class Destinatario(Pessoa):
    """Destinatário do serviço para IBS/CBS (``dest``). O nome é obrigatório (até 150)."""

    _nome_obrigatorio = True

    def __post_init__(self) -> None:
        Pessoa.__post_init__(self)
        if len(self.nome) > 150:
            raise ValidacaoErro("Nome do destinatário excede 150 caracteres.")


# ---------------------------------------------------------------------------
# Serviço e valores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Obra:
    """Dados de obra (``obra``): código da obra ou CIB, com inscrição imobiliária opcional."""

    codigo_obra: str | None = None
    cib: str | None = None
    inscricao_imobiliaria: str | None = None

    def __post_init__(self) -> None:
        if bool(self.codigo_obra) == bool(self.cib):
            raise ValidacaoErro("Obra: informe o código da obra ou o CIB (exatamente um).")
        _fixar(self,
               codigo_obra=_texto(self.codigo_obra, "Código da obra", 30),
               cib=_digitos(self.cib, "CIB", 8) if self.cib else None,
               inscricao_imobiliaria=_texto(
                   self.inscricao_imobiliaria, "Inscrição imobiliária fiscal", 30))


@dataclass(frozen=True)
class Servico:
    """Serviço prestado (``serv``).

    Args:
        codigo_tributacao_nacional: ``cTribNac`` (6 dígitos da lista nacional).
        descricao: ``xDescServ`` (até 2000 caracteres; quebras de linha permitidas).
        codigo_municipio_prestacao: ``cLocPrestacao`` (IBGE) ou ``pais_prestacao`` (ISO).
        codigo_tributacao_municipal: ``cTribMun`` (3 dígitos, nunca ``000``, E0315).
        codigo_nbs: ``cNBS`` (9 dígitos); obrigatório se houver IBS/CBS (E0322).
    """

    codigo_tributacao_nacional: str
    descricao: str
    codigo_municipio_prestacao: str | None = None
    pais_prestacao: str | None = None
    codigo_tributacao_municipal: str | None = None
    codigo_nbs: str | None = None
    codigo_interno: str | None = None
    obra: Obra | None = None
    informacoes_complementares: str | None = None
    pedido: str | None = None
    documento_referencia: str | None = None
    documento_tecnico: str | None = None

    def __post_init__(self) -> None:
        if bool(self.codigo_municipio_prestacao) == bool(self.pais_prestacao):
            raise ValidacaoErro("Local da prestação: informe o município (IBGE) ou o país "
                                "(ISO), exatamente um.")
        municipal = _digitos(self.codigo_tributacao_municipal, "cTribMun", 3)
        if municipal == "000":
            raise ValidacaoErro("cTribMun não pode ser 000 (E0315).")
        pais = (self.pais_prestacao or "").strip().upper() or None
        if pais and not re.fullmatch(r"[A-Z]{2}", pais):
            raise ValidacaoErro(f"País da prestação deve ser ISO alfa-2: {self.pais_prestacao!r}")
        interno = _texto(self.codigo_interno, "Código interno", 20)
        if interno and not re.fullmatch(r"[a-zA-Z0-9]{1,20}", interno):
            raise ValidacaoErro("Código interno do contribuinte aceita só letras e números.")
        _fixar(self,
               codigo_tributacao_nacional=_digitos(
                   self.codigo_tributacao_nacional, "cTribNac", 6, obrigatorio=True),
               descricao=_texto(self.descricao, "Descrição do serviço", 2000,
                                obrigatorio=True, quebra_linha=True),
               codigo_municipio_prestacao=(
                   exigir_codigo_ibge(self.codigo_municipio_prestacao, "local da prestação")
                   if self.codigo_municipio_prestacao else None),
               pais_prestacao=pais, codigo_tributacao_municipal=municipal,
               codigo_nbs=_digitos(self.codigo_nbs, "cNBS", 9), codigo_interno=interno,
               informacoes_complementares=_texto(self.informacoes_complementares,
                                                 "Informações complementares", 2000,
                                                 quebra_linha=True),
               pedido=_texto(self.pedido, "Número do pedido", 60),
               documento_referencia=_texto(self.documento_referencia,
                                           "Documento de referência", 255),
               documento_tecnico=_texto(self.documento_tecnico, "Documento técnico (DRT)", 40))


@dataclass(frozen=True)
class Valores:
    """Valores do serviço (``vServPrest``, ``vDescCondIncond``, ``vDedRed``).

    ``deducao_percentual`` (``pDR``, até 999,99) e ``deducao_valor`` (``vDR``) são excludentes.
    Dedução por documentos (``documentos``) só pelo caminho "XML pronto".
    """

    servico: Decimal
    recebido: Decimal | None = None
    desconto_incondicionado: Decimal | None = None
    desconto_condicionado: Decimal | None = None
    deducao_percentual: Decimal | None = None
    deducao_valor: Decimal | None = None

    def __post_init__(self) -> None:
        if self.deducao_percentual is not None and self.deducao_valor is not None:
            raise ValidacaoErro("Dedução: informe percentual ou valor, não ambos.")
        _fixar(self,
               servico=_decimal(self.servico, "Valor do serviço", obrigatorio=True),
               recebido=_decimal(self.recebido, "Valor recebido"),
               desconto_incondicionado=_decimal(self.desconto_incondicionado,
                                                "Desconto incondicionado"),
               desconto_condicionado=_decimal(self.desconto_condicionado, "Desconto condicionado"),
               deducao_percentual=_decimal(self.deducao_percentual, "Percentual de dedução",
                                           inteiros=3),
               deducao_valor=_decimal(self.deducao_valor, "Valor da dedução"))


# ---------------------------------------------------------------------------
# Tributação
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExigibilidadeSuspensa:
    """``exigSusp``: tipo e número do processo (30 dígitos)."""

    tipo: TipoSuspensao
    numero_processo: str

    def __post_init__(self) -> None:
        _fixar(self, tipo=_enum(TipoSuspensao, self.tipo, "Tipo de suspensão"),
               numero_processo=_digitos(self.numero_processo, "Número do processo", 30,
                                        obrigatorio=True))


@dataclass(frozen=True)
class BeneficioMunicipal:
    """``BM``: benefício municipal (14 dígitos) com redução da base por valor ou percentual."""

    numero: str
    reducao_valor: Decimal | None = None
    reducao_percentual: Decimal | None = None

    def __post_init__(self) -> None:
        if self.reducao_valor is not None and self.reducao_percentual is not None:
            raise ValidacaoErro("Benefício municipal: redução por valor ou percentual, não ambos.")
        _fixar(self, numero=_digitos(self.numero, "nBM", 14, obrigatorio=True),
               reducao_valor=_decimal(self.reducao_valor, "Redução da BC (valor)"),
               reducao_percentual=_decimal(self.reducao_percentual, "Redução da BC (%)",
                                           inteiros=3))


@dataclass(frozen=True)
class TribMunicipal:
    """Tributação municipal do ISSQN (``tribMun``).

    ``aliquota`` é ``pAliq`` em percentual (0 a 9,99), opcional quando o município a define.
    O tomador só retém se o município de incidência for o dele (E0031/E0032).
    """

    tributacao: TributacaoIssqn = TributacaoIssqn.TRIBUTAVEL
    retencao: RetencaoIssqn = RetencaoIssqn.NAO_RETIDO
    aliquota: Decimal | None = None
    pais_resultado: str | None = None
    tipo_imunidade: TipoImunidade | None = None
    suspensao: ExigibilidadeSuspensa | None = None
    beneficio: BeneficioMunicipal | None = None

    def __post_init__(self) -> None:
        pais = (self.pais_resultado or "").strip().upper() or None
        if pais and not re.fullmatch(r"[A-Z]{2}", pais):
            raise ValidacaoErro(f"País do resultado deve ser ISO alfa-2: {self.pais_resultado!r}")
        _fixar(self,
               tributacao=_enum(TributacaoIssqn, self.tributacao, "Tributação do ISSQN"),
               retencao=_enum(RetencaoIssqn, self.retencao, "Retenção do ISSQN"),
               aliquota=_decimal(self.aliquota, "Alíquota do ISSQN", inteiros=1),
               pais_resultado=pais,
               tipo_imunidade=_enum(TipoImunidade, self.tipo_imunidade, "Tipo de imunidade",
                                    obrigatorio=False))


@dataclass(frozen=True)
class PisCofins:
    """``piscofins``: situação tributária e, se houver, base, alíquotas e valores."""

    cst: str
    base_calculo: Decimal | None = None
    aliquota_pis: Decimal | None = None
    aliquota_cofins: Decimal | None = None
    valor_pis: Decimal | None = None
    valor_cofins: Decimal | None = None
    tipo_retencao: int | None = None

    def __post_init__(self) -> None:
        if self.tipo_retencao is not None and not 0 <= int(self.tipo_retencao) <= 9:
            raise ValidacaoErro(f"tpRetPisCofins deve ir de 0 a 9: {self.tipo_retencao!r}")
        _fixar(self, cst=_digitos(self.cst, "CST do PIS/COFINS", 2, obrigatorio=True),
               base_calculo=_decimal(self.base_calculo, "Base do PIS/COFINS"),
               aliquota_pis=_decimal(self.aliquota_pis, "Alíquota do PIS", inteiros=2),
               aliquota_cofins=_decimal(self.aliquota_cofins, "Alíquota da COFINS", inteiros=2),
               valor_pis=_decimal(self.valor_pis, "Valor do PIS"),
               valor_cofins=_decimal(self.valor_cofins, "Valor da COFINS"))


@dataclass(frozen=True)
class TribFederal:
    """``tribFed``: PIS/COFINS e retenções de contribuição previdenciária, IRRF e CSLL."""

    piscofins: PisCofins | None = None
    retencao_cp: Decimal | None = None
    retencao_irrf: Decimal | None = None
    retencao_csll: Decimal | None = None

    def __post_init__(self) -> None:
        _fixar(self,
               retencao_cp=_decimal(self.retencao_cp, "Retenção CP"),
               retencao_irrf=_decimal(self.retencao_irrf, "Retenção IRRF"),
               retencao_csll=_decimal(self.retencao_csll, "Retenção CSLL"))


@dataclass(frozen=True)
class TotTrib:
    """``totTrib`` (obrigatório): um único ramo do ``xs:choice``.

    A opção permitida depende do regime do Simples (E0710, E0712, E0713; regra do Anexo I,
    aplicada pela Sefin). Use as fábricas :meth:`valores`, :meth:`percentuais`,
    :meth:`nao_informado` e :meth:`simples`.
    """

    tipo: TipoTotTrib
    federal: Decimal | None = None
    estadual: Decimal | None = None
    municipal: Decimal | None = None
    percentual_simples: Decimal | None = None

    def __post_init__(self) -> None:
        tipo = self.tipo
        if tipo is TipoTotTrib.VALORES:
            partes = {"federal": 15, "estadual": 15, "municipal": 15}
        elif tipo is TipoTotTrib.PERCENTUAIS:
            partes = {"federal": 3, "estadual": 3, "municipal": 3}
        else:
            partes = {}
        normalizados = {nome: _decimal(getattr(self, nome), f"Tributos {nome}", inteiros=inteiros,
                                       obrigatorio=True)
                        for nome, inteiros in partes.items()}
        if tipo is TipoTotTrib.PERCENTUAL_SIMPLES:
            normalizados["percentual_simples"] = _decimal(
                self.percentual_simples, "Percentual total do Simples", inteiros=2,
                obrigatorio=True)
        _fixar(self, **normalizados)

    @classmethod
    def valores(cls, federal: Any, estadual: Any, municipal: Any) -> TotTrib:
        """``vTotTrib``: valor aproximado dos tributos por esfera."""
        return cls(TipoTotTrib.VALORES, federal, estadual, municipal)

    @classmethod
    def percentuais(cls, federal: Any, estadual: Any, municipal: Any) -> TotTrib:
        """``pTotTrib``: percentual aproximado dos tributos por esfera."""
        return cls(TipoTotTrib.PERCENTUAIS, federal, estadual, municipal)

    @classmethod
    def nao_informado(cls) -> TotTrib:
        """``indTotTrib=0``: não informa o total de tributos."""
        return cls(TipoTotTrib.NAO_INFORMADO)

    @classmethod
    def simples(cls, percentual: Any) -> TotTrib:
        """``pTotTribSN``: percentual do Simples Nacional (usado por ME/EPP)."""
        return cls(TipoTotTrib.PERCENTUAL_SIMPLES, percentual_simples=percentual)


# ---------------------------------------------------------------------------
# IBS/CBS (reforma tributária)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TributacaoRegularIbsCbs:
    """``gTribRegular``: situação tributária e classificação da tributação regular."""

    cst: str
    classificacao: str

    def __post_init__(self) -> None:
        _fixar(self, cst=_digitos(self.cst, "CSTReg", 3, obrigatorio=True),
               classificacao=_digitos(self.classificacao, "cClassTribReg", 6, obrigatorio=True))


@dataclass(frozen=True)
class DiferimentoIbsCbs:
    """``gDif``: percentuais de diferimento (UF, município e CBS), até 999,99."""

    percentual_uf: Decimal
    percentual_municipio: Decimal
    percentual_cbs: Decimal

    def __post_init__(self) -> None:
        _fixar(self,
               percentual_uf=_decimal(self.percentual_uf, "pDifUF", inteiros=3, obrigatorio=True),
               percentual_municipio=_decimal(self.percentual_municipio, "pDifMun", inteiros=3,
                                             obrigatorio=True),
               percentual_cbs=_decimal(self.percentual_cbs, "pDifCBS", inteiros=3,
                                       obrigatorio=True))


@dataclass(frozen=True)
class IbsCbs:
    """Grupo ``IBSCBS`` da DPS (subconjunto).

    Os valores calculados de IBS/CBS não são informados pelo emissor: a Sefin os devolve na
    NFS-e. A NBS do serviço é obrigatória quando este grupo existe (E0322). Reembolso/repasse
    (``gReeRepRes``) e imóvel só pelo caminho "XML pronto".

    HIPÓTESE (NT 009 v1.01): ``ajuste_base_calculo`` (``vAjusteBC``) só existe no leiaute da
    NT 009 e sua posição no XML foi inferida do relatório técnico, sem o XSD do pacote
    ``PRODREST``. Valide com ``pasta_xsd`` antes de enviar. O ``ind_gIBSCBS`` do relatório é
    uma coluna da tabela de CST (indica se o detalhamento é exigido), não um elemento do XML.
    """

    codigo_indicador_operacao: str
    indicador_destinatario: int
    cst: str
    codigo_classificacao: str
    destinatario: Destinatario | None = None
    indicador_consumidor_final: int | None = None
    tipo_operacao: int | None = None
    ente_governamental: int | None = None
    notas_referenciadas: tuple[str, ...] = ()
    codigo_credito_presumido: str | None = None
    tributacao_regular: TributacaoRegularIbsCbs | None = None
    diferimento: DiferimentoIbsCbs | None = None
    ajuste_base_calculo: Decimal | None = None
    finalidade: str = field(default="0", init=False)

    def __post_init__(self) -> None:
        if self.indicador_destinatario not in (0, 1):
            raise ValidacaoErro("indDest deve ser 0 ou 1.")
        if self.indicador_consumidor_final not in (None, 0, 1):
            raise ValidacaoErro("indFinal deve ser 0 ou 1.")
        if self.tipo_operacao is not None and self.tipo_operacao not in range(1, 6):
            raise ValidacaoErro("tpOper deve ir de 1 a 5.")
        if self.ente_governamental is not None and self.ente_governamental not in range(1, 5):
            raise ValidacaoErro("tpEnteGov deve ir de 1 a 4.")
        if len(self.notas_referenciadas) > 99:
            raise ValidacaoErro("gRefNFSe aceita no máximo 99 chaves.")
        _fixar(self,
               codigo_indicador_operacao=_digitos(self.codigo_indicador_operacao, "cIndOp", 6,
                                                  obrigatorio=True),
               cst=_digitos(self.cst, "CST do IBS/CBS", 3, obrigatorio=True),
               codigo_classificacao=_digitos(self.codigo_classificacao, "cClassTrib", 6,
                                             obrigatorio=True),
               notas_referenciadas=tuple(exigir_chave_nfse(c) for c in self.notas_referenciadas),
               codigo_credito_presumido=_digitos(self.codigo_credito_presumido, "cCredPres", 2),
               ajuste_base_calculo=_decimal(self.ajuste_base_calculo, "vAjusteBC"))


# ---------------------------------------------------------------------------
# DPS
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Substituicao:
    """``subst``: a DPS substitui uma NFS-e já emitida (a Sefin gera o evento 105102).

    Motivos (``cMotivo``): ``01`` desenquadramento do Simples, ``02`` enquadramento no Simples,
    ``03`` inclusão retroativa de imunidade/isenção, ``04`` exclusão retroativa de
    imunidade/isenção, ``05`` rejeição pelo tomador/intermediário, ``99`` outros (exige texto).
    """

    chave_substituida: str
    codigo_motivo: str
    motivo: str | None = None

    def __post_init__(self) -> None:
        codigo = str(self.codigo_motivo).strip().zfill(2)
        if codigo not in {"01", "02", "03", "04", "05", "99"}:
            raise ValidacaoErro(f"Motivo de substituição inválido: {self.codigo_motivo!r}")
        motivo = _texto(self.motivo, "Motivo da substituição", 255)
        if codigo == "99" and (motivo is None or len(motivo) < 15):
            raise ValidacaoErro("Substituição por 'outros' (99) exige motivo de 15 a 255 "
                                "caracteres.")
        if motivo is not None and len(motivo) < 15:
            raise ValidacaoErro("O motivo da substituição deve ter de 15 a 255 caracteres.")
        _fixar(self, chave_substituida=exigir_chave_nfse(self.chave_substituida),
               codigo_motivo=codigo, motivo=motivo)


@dataclass(frozen=True)
class Dps:
    """Declaração de Prestação de Serviços.

    ``tpAmb`` **não** é um campo: vem do ambiente da fachada, o que evita a rejeição E0006.
    ``numero`` ausente usa a numeração local (``EstadoEmissao.proximo_numero``); o ERP continua
    sendo a fonte oficial da numeração, e reutilizar série/número gera E0014.
    ``data_emissao`` ausente usa o horário de Brasília com folga (``dhEmi`` não pode ser
    futura, E0008).
    """

    serie: int | str
    data_competencia: dt.date
    codigo_municipio_emissor: str
    prestador: Prestador
    servico: Servico
    valores: Valores
    trib_municipal: TribMunicipal
    tot_trib: TotTrib
    numero: int | str | None = None
    data_emissao: dt.datetime | None = None
    tipo_emitente: TipoEmitente = TipoEmitente.PRESTADOR
    tomador: Tomador | None = None
    intermediario: Intermediario | None = None
    trib_federal: TribFederal | None = None
    ibs_cbs: IbsCbs | None = None
    substituicao: Substituicao | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data_competencia, dt.date) or isinstance(
                self.data_competencia, dt.datetime):
            raise ValidacaoErro("data_competencia deve ser uma data (datetime.date).")
        if self.data_emissao is not None and self.data_emissao.tzinfo is None:
            raise ValidacaoErro("data_emissao deve ter fuso horário (use o de Brasília, -03:00).")
        emitente = _enum(TipoEmitente, self.tipo_emitente, "Tipo de emitente")
        if emitente is TipoEmitente.PRESTADOR and self.prestador.endereco is not None:
            raise ValidacaoErro("Prestador emitente não informa endereço: ele vem do cadastro "
                                "(E0128).")
        if self.ibs_cbs is not None and not self.servico.codigo_nbs:
            raise ValidacaoErro("A NBS do serviço é obrigatória quando há IBS/CBS (E0322).")
        if emitente is TipoEmitente.TOMADOR and self.tomador is None:
            raise ValidacaoErro("Emitente tomador exige o grupo do tomador.")
        if emitente is TipoEmitente.INTERMEDIARIO and self.intermediario is None:
            raise ValidacaoErro("Emitente intermediário exige o grupo do intermediário.")
        if self.documento_emitente is None:
            raise ValidacaoErro("O emitente da DPS deve ter CNPJ ou CPF (compõe o Id da DPS).")
        _fixar(self,
               serie=normalizar_serie(self.serie),
               numero=normalizar_numero_dps(self.numero) if self.numero is not None else None,
               codigo_municipio_emissor=exigir_codigo_ibge(
                   self.codigo_municipio_emissor, "município emissor"),
               tipo_emitente=emitente)
        if self.data_emissao is not None and self.data_competencia > self.data_emissao.date():
            raise ValidacaoErro("A competência não pode ser posterior à data de emissão.")

    @property
    def documento_emitente(self) -> str | None:
        """CNPJ/CPF de quem emite a DPS (prestador, tomador ou intermediário, por ``tpEmit``).

        Compõe o Id da DPS e deve coincidir com o titular do certificado (E0718).
        """
        emitente = {TipoEmitente.PRESTADOR: self.prestador, TipoEmitente.TOMADOR: self.tomador,
                    TipoEmitente.INTERMEDIARIO: self.intermediario}[self.tipo_emitente]
        return emitente.documento if emitente is not None else None
