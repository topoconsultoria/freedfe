"""Montagem do XML da DPS (sem assinatura) para cada leiaute da NFS-e Nacional.

A estrutura segue os XSD oficiais, conferidos elemento a elemento:

``NT004_V2``
    Pacote ``NFSe-ESQUEMAS_XSD-v1.01-20260209`` (produção até 30/11/2026).
``NT009_V101``
    Pacote ``esquemas_prod_restrita_20261020`` (release 1 da NT 009, produção restrita desde
    20/10/2026). Diferenças em relação à NT 004: ``finNFSe`` obrigatório na raiz de ``infDPS``
    (entre ``tpEmit`` e ``cLocEmi``, **mesmo sem IBS/CBS**); ``cServ`` com ``xDescServ`` por
    último; ``vDedRed`` substituído por ``vAjusteBC`` (``pAjusteBCISSQN`` ou
    ``vAjusteBCISSQN``); ``IBSCBS`` com ``CST``/``cClassTrib`` diretamente em ``trib`` e
    ``gIBSCBS`` opcional; série limitada a 89999.

A NT 009 prevê ainda o reposicionamento de ``indDest``/``dest`` para a raiz de ``infDPS``
(seção 2.11), as notas de ajuste (``finNFSe`` 1 e 2) e ``vAjusteBCIBSCBSComExt``. Esses itens
**não estão no XSD do release 1** e por isso não são gerados; quando o pacote da produção sair,
acrescente um novo membro em ``VersaoLeiaute`` em vez de alterar este.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import Enum

from lxml import etree

from freedfe._versao import __version__
from freedfe.distribuicao.estado import Relogio, relogio_utc
from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.identificadores import id_dps
from freedfe.nfse.leiaute import NAMESPACE_NFSE, VersaoLeiaute
from freedfe.nfse.modelo import (
    Dps,
    Endereco,
    IbsCbs,
    Pessoa,
    Prestador,
    Servico,
    TipoTotTrib,
    Valores,
)
from freedfe.tipos import FUSO_BRASILIA, Ambiente

FOLGA_EMISSAO_SEGUNDOS = 60


def _q(nome: str) -> str:
    return f"{{{NAMESPACE_NFSE}}}{nome}"


def _no(pai: etree._Element, nome: str, valor: object | None = None) -> etree._Element:
    """Cria o filho; ``valor`` ``None`` cria o elemento vazio (grupo)."""
    elemento = etree.SubElement(pai, _q(nome))
    if valor is not None:
        elemento.text = _formatar(valor)
    return elemento


def _opcional(pai: etree._Element, nome: str, valor: object | None) -> None:
    if valor is not None and valor != "":
        _no(pai, nome, valor)


def _formatar(valor: object) -> str:
    """Texto do elemento: ``Decimal`` com 2 casas, datas ISO, enums pelo valor."""
    if isinstance(valor, Enum):
        return str(valor.value)
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    if isinstance(valor, dt.datetime):
        return valor.astimezone(FUSO_BRASILIA).replace(microsecond=0).isoformat()
    if isinstance(valor, dt.date):
        return valor.isoformat()
    return str(valor)


class MontadorDps:
    """Monta ``<DPS>`` conforme o leiaute da NT 004 v2.0 (XSD 1.01).

    Args:
        ambiente: ambiente da fachada; ``tpAmb`` nunca vem do modelo (evita E0006).
        relogio: relógio UTC injetável (testes).
        folga_emissao_segundos: subtraída do horário atual quando ``data_emissao`` não é
            informada, porque ``dhEmi`` não pode ser futura (E0008).
        versao_aplicativo: ``verAplic`` (até 20 caracteres). Padrão ``freedfe/<versão>``.
    """

    leiaute: VersaoLeiaute = VersaoLeiaute.NT004_V2

    def __init__(self, ambiente: Ambiente, relogio: Relogio = relogio_utc,
                 folga_emissao_segundos: int = FOLGA_EMISSAO_SEGUNDOS,
                 versao_aplicativo: str | None = None):
        self._ambiente = Ambiente(ambiente)
        self._relogio = relogio
        self._folga = dt.timedelta(seconds=folga_emissao_segundos)
        self._versao_aplicativo = (versao_aplicativo or f"freedfe/{__version__}")[:20]

    def identificador(self, dps: Dps) -> str:
        """Id da DPS (45 posições) calculado a partir do emitente, da série e do ``nDPS``."""
        if dps.numero is None:
            raise ValidacaoErro("Informe o número da DPS (ServicoNfse numera automaticamente).")
        return id_dps(dps.codigo_municipio_emissor, dps.documento_emitente, dps.serie,
                      dps.numero)

    def montar(self, dps: Dps) -> etree._Element:
        """Elemento ``DPS`` sem ``Signature``, na ordem exigida pelo XSD."""
        emissao = self._data_emissao(dps)
        raiz = etree.Element(_q("DPS"), nsmap={None: NAMESPACE_NFSE},
                             versao=self.leiaute.versao_dps)
        inf = etree.SubElement(raiz, _q("infDPS"), Id=self.identificador(dps))
        self._ide(inf, dps, emissao)
        self._subst(inf, dps)
        self._prestador(inf, dps.prestador)
        if dps.tomador is not None:
            self._pessoa(_no(inf, "toma"), dps.tomador)
        if dps.intermediario is not None:
            self._pessoa(_no(inf, "interm"), dps.intermediario)
        self._servico(inf, dps)
        self._valores(inf, dps)
        self._ibscbs(inf, dps)
        return raiz

    def serializar(self, dps: Dps) -> bytes:
        """XML da DPS em UTF-8, sem declaração e sem formatação (como será assinado)."""
        return etree.tostring(self.montar(dps), encoding="utf-8", xml_declaration=False)

    # ------------------------------------------------------------------
    # Grupos
    # ------------------------------------------------------------------

    def _data_emissao(self, dps: Dps) -> dt.datetime:
        agora = self._relogio().astimezone(FUSO_BRASILIA)
        emissao = dps.data_emissao or agora - self._folga
        emissao = emissao.astimezone(FUSO_BRASILIA).replace(microsecond=0)
        if emissao > agora:
            raise ValidacaoErro(f"dhEmi no futuro ({emissao.isoformat()}); a Sefin rejeita "
                                "com E0008. Verifique o relógio e o fuso.")
        if dps.data_competencia > emissao.date():
            raise ValidacaoErro("A competência não pode ser posterior à data de emissão.")
        return emissao

    def _ide(self, inf: etree._Element, dps: Dps, emissao: dt.datetime) -> None:
        _no(inf, "tpAmb", int(self._ambiente))
        _no(inf, "dhEmi", emissao)
        _no(inf, "verAplic", self._versao_aplicativo)
        _no(inf, "serie", dps.serie)
        _no(inf, "nDPS", dps.numero)
        _no(inf, "dCompet", dps.data_competencia)
        _no(inf, "tpEmit", int(dps.tipo_emitente))
        self._finalidade(inf)
        _no(inf, "cLocEmi", dps.codigo_municipio_emissor)

    def _finalidade(self, inf: etree._Element) -> None:
        """Na NT 004 a finalidade (``finNFSe``) só existe dentro de ``IBSCBS``."""

    def _subst(self, inf: etree._Element, dps: Dps) -> None:
        if dps.substituicao is None:
            return
        subst = _no(inf, "subst")
        _no(subst, "chSubstda", dps.substituicao.chave_substituida)
        _no(subst, "cMotivo", dps.substituicao.codigo_motivo)
        _opcional(subst, "xMotivo", dps.substituicao.motivo)

    def _prestador(self, inf: etree._Element, prestador: Prestador) -> None:
        no = _no(inf, "prest")
        self._identificacao(no, prestador)
        _opcional(no, "IM", prestador.inscricao_municipal)
        _opcional(no, "xNome", prestador.nome)
        self._endereco_opcional(no, prestador.endereco)
        _opcional(no, "fone", prestador.telefone)
        _opcional(no, "email", prestador.email)
        regime = _no(no, "regTrib")
        _no(regime, "opSimpNac", int(prestador.opcao_simples))
        if prestador.regime_apuracao_simples is not None:
            _no(regime, "regApTribSN", int(prestador.regime_apuracao_simples))
        _no(regime, "regEspTrib", int(prestador.regime_especial))

    def _pessoa(self, no: etree._Element, pessoa: Pessoa, *, com_inscricao: bool = True) -> None:
        self._identificacao(no, pessoa)
        if com_inscricao:
            _opcional(no, "IM", pessoa.inscricao_municipal)
        _no(no, "xNome", pessoa.nome)
        self._endereco_opcional(no, pessoa.endereco)
        _opcional(no, "fone", pessoa.telefone)
        _opcional(no, "email", pessoa.email)

    @staticmethod
    def _identificacao(no: etree._Element, pessoa: Pessoa) -> None:
        if pessoa.cnpj:
            _no(no, "CNPJ", pessoa.cnpj)
        elif pessoa.cpf:
            _no(no, "CPF", pessoa.cpf)
        elif pessoa.nif:
            _no(no, "NIF", pessoa.nif)
        else:
            _no(no, "cNaoNIF", pessoa.motivo_sem_nif)

    @staticmethod
    def _endereco_opcional(pai: etree._Element, endereco: Endereco | None) -> None:
        if endereco is None:
            return
        no = _no(pai, "end")
        if endereco.exterior:
            exterior = _no(no, "endExt")
            _no(exterior, "cPais", endereco.pais)
            _no(exterior, "cEndPost", endereco.codigo_postal)
            _no(exterior, "xCidade", endereco.cidade)
            _no(exterior, "xEstProvReg", endereco.estado)
        else:
            nacional = _no(no, "endNac")
            _no(nacional, "cMun", endereco.codigo_municipio)
            _no(nacional, "CEP", endereco.cep)
        _no(no, "xLgr", endereco.logradouro)
        _no(no, "nro", endereco.numero)
        _opcional(no, "xCpl", endereco.complemento)
        _no(no, "xBairro", endereco.bairro)

    def _servico(self, inf: etree._Element, dps: Dps) -> None:
        servico = dps.servico
        no = _no(inf, "serv")
        local = _no(no, "locPrest")
        if servico.codigo_municipio_prestacao:
            _no(local, "cLocPrestacao", servico.codigo_municipio_prestacao)
        else:
            _no(local, "cPaisPrestacao", servico.pais_prestacao)
        self._codigos_servico(_no(no, "cServ"), servico)
        if servico.obra is not None:
            obra = _no(no, "obra")
            _opcional(obra, "inscImobFisc", servico.obra.inscricao_imobiliaria)
            if servico.obra.codigo_obra:
                _no(obra, "cObra", servico.obra.codigo_obra)
            else:
                _no(obra, "cCIB", servico.obra.cib)
        if any((servico.documento_tecnico, servico.documento_referencia, servico.pedido,
                servico.informacoes_complementares)):
            complemento = _no(no, "infoCompl")
            _opcional(complemento, "idDocTec", servico.documento_tecnico)
            _opcional(complemento, "docRef", servico.documento_referencia)
            _opcional(complemento, "xPed", servico.pedido)
            _opcional(complemento, "xInfComp", servico.informacoes_complementares)

    def _codigos_servico(self, codigo: etree._Element, servico: Servico) -> None:
        """``cServ`` da NT 004: ``xDescServ`` logo depois de ``cTribMun``."""
        _no(codigo, "cTribNac", servico.codigo_tributacao_nacional)
        _opcional(codigo, "cTribMun", servico.codigo_tributacao_municipal)
        _no(codigo, "xDescServ", servico.descricao)
        _opcional(codigo, "cNBS", servico.codigo_nbs)
        _opcional(codigo, "cIntContrib", servico.codigo_interno)

    def _ajuste_base(self, no: etree._Element, valores: Valores) -> None:
        """``vDedRed`` (NT 004): percentual ``pDR`` ou valor ``vDR``."""
        if valores.deducao_percentual is not None or valores.deducao_valor is not None:
            deducao = _no(no, "vDedRed")
            if valores.deducao_percentual is not None:
                _no(deducao, "pDR", valores.deducao_percentual)
            else:
                _no(deducao, "vDR", valores.deducao_valor)

    def _valores(self, inf: etree._Element, dps: Dps) -> None:
        valores = dps.valores
        no = _no(inf, "valores")
        servico = _no(no, "vServPrest")
        _opcional(servico, "vReceb", valores.recebido)
        _no(servico, "vServ", valores.servico)
        if valores.desconto_incondicionado is not None or valores.desconto_condicionado is not None:
            descontos = _no(no, "vDescCondIncond")
            _opcional(descontos, "vDescIncond", valores.desconto_incondicionado)
            _opcional(descontos, "vDescCond", valores.desconto_condicionado)
        self._ajuste_base(no, valores)
        trib = _no(no, "trib")
        self._trib_municipal(trib, dps)
        self._trib_federal(trib, dps)
        self._tot_trib(trib, dps)

    @staticmethod
    def _trib_municipal(trib: etree._Element, dps: Dps) -> None:
        municipal = dps.trib_municipal
        no = _no(trib, "tribMun")
        _no(no, "tribISSQN", int(municipal.tributacao))
        _opcional(no, "cPaisResult", municipal.pais_resultado)
        if municipal.tipo_imunidade is not None:
            _no(no, "tpImunidade", int(municipal.tipo_imunidade))
        if municipal.suspensao is not None:
            suspensa = _no(no, "exigSusp")
            _no(suspensa, "tpSusp", int(municipal.suspensao.tipo))
            _no(suspensa, "nProcesso", municipal.suspensao.numero_processo)
        if municipal.beneficio is not None:
            beneficio = _no(no, "BM")
            _no(beneficio, "nBM", municipal.beneficio.numero)
            _opcional(beneficio, "vRedBCBM", municipal.beneficio.reducao_valor)
            _opcional(beneficio, "pRedBCBM", municipal.beneficio.reducao_percentual)
        _no(no, "tpRetISSQN", int(municipal.retencao))
        _opcional(no, "pAliq", municipal.aliquota)

    @staticmethod
    def _trib_federal(trib: etree._Element, dps: Dps) -> None:
        federal = dps.trib_federal
        if federal is None:
            return
        no = _no(trib, "tribFed")
        if federal.piscofins is not None:
            pc = federal.piscofins
            grupo = _no(no, "piscofins")
            _no(grupo, "CST", pc.cst)
            _opcional(grupo, "vBCPisCofins", pc.base_calculo)
            _opcional(grupo, "pAliqPis", pc.aliquota_pis)
            _opcional(grupo, "pAliqCofins", pc.aliquota_cofins)
            _opcional(grupo, "vPis", pc.valor_pis)
            _opcional(grupo, "vCofins", pc.valor_cofins)
            if pc.tipo_retencao is not None:
                _no(grupo, "tpRetPisCofins", int(pc.tipo_retencao))
        _opcional(no, "vRetCP", federal.retencao_cp)
        _opcional(no, "vRetIRRF", federal.retencao_irrf)
        _opcional(no, "vRetCSLL", federal.retencao_csll)

    @staticmethod
    def _tot_trib(trib: etree._Element, dps: Dps) -> None:
        total = dps.tot_trib
        no = _no(trib, "totTrib")
        if total.tipo is TipoTotTrib.VALORES:
            grupo = _no(no, "vTotTrib")
            _no(grupo, "vTotTribFed", total.federal)
            _no(grupo, "vTotTribEst", total.estadual)
            _no(grupo, "vTotTribMun", total.municipal)
        elif total.tipo is TipoTotTrib.PERCENTUAIS:
            grupo = _no(no, "pTotTrib")
            _no(grupo, "pTotTribFed", total.federal)
            _no(grupo, "pTotTribEst", total.estadual)
            _no(grupo, "pTotTribMun", total.municipal)
        elif total.tipo is TipoTotTrib.NAO_INFORMADO:
            _no(no, "indTotTrib", 0)
        else:
            _no(no, "pTotTribSN", total.percentual_simples)

    # ------------------------------------------------------------------
    # IBS/CBS
    # ------------------------------------------------------------------

    def _ibscbs(self, inf: etree._Element, dps: Dps) -> None:
        """``IBSCBS`` no leiaute da NT 004 (XSD 1.01): CST/cClassTrib dentro de ``gIBSCBS``."""
        ibs = dps.ibs_cbs
        if ibs is None:
            return
        no = self._ibscbs_cabecalho(inf, ibs)
        _no(no, "indDest", ibs.indicador_destinatario)
        if ibs.destinatario is not None:
            self._pessoa(_no(no, "dest"), ibs.destinatario, com_inscricao=False)
        valores = _no(no, "valores")
        trib = _no(valores, "trib")
        grupo = _no(trib, "gIBSCBS")
        _no(grupo, "CST", ibs.cst)
        _no(grupo, "cClassTrib", ibs.codigo_classificacao)
        self._ibscbs_detalhe(grupo, ibs)

    def _ibscbs_cabecalho(self, inf: etree._Element, ibs: IbsCbs) -> etree._Element:
        """``IBSCBS`` até ``tpEnteGov``; na NT 004 abre com ``finNFSe``."""
        no = _no(inf, "IBSCBS")
        self._finalidade_ibscbs(no)
        if ibs.indicador_consumidor_final is not None:
            _no(no, "indFinal", ibs.indicador_consumidor_final)
        _no(no, "cIndOp", ibs.codigo_indicador_operacao)
        if ibs.tipo_operacao is not None:
            _no(no, "tpOper", ibs.tipo_operacao)
        if ibs.notas_referenciadas:
            referencias = _no(no, "gRefNFSe")
            for chave in ibs.notas_referenciadas:
                _no(referencias, "refNFSe", chave)
        if ibs.ente_governamental is not None:
            _no(no, "tpEnteGov", ibs.ente_governamental)
        return no

    def _finalidade_ibscbs(self, no: etree._Element) -> None:
        _no(no, "finNFSe", 0)

    @staticmethod
    def _ibscbs_detalhe(grupo: etree._Element, ibs: IbsCbs) -> None:
        """``cCredPres``, ``gTribRegular`` e ``gDif`` dentro de ``gIBSCBS``."""
        _opcional(grupo, "cCredPres", ibs.codigo_credito_presumido)
        if ibs.tributacao_regular is not None:
            regular = _no(grupo, "gTribRegular")
            _no(regular, "CSTReg", ibs.tributacao_regular.cst)
            _no(regular, "cClassTribReg", ibs.tributacao_regular.classificacao)
        if ibs.diferimento is not None:
            diferimento = _no(grupo, "gDif")
            _no(diferimento, "pDifUF", ibs.diferimento.percentual_uf)
            _no(diferimento, "pDifMun", ibs.diferimento.percentual_municipio)
            _no(diferimento, "pDifCBS", ibs.diferimento.percentual_cbs)


class MontadorDpsNt009(MontadorDps):
    """Leiaute da NT 009 v1.01, release 1 (produção restrita desde 20/10/2026).

    Estrutura conferida contra ``esquemas_prod_restrita_20261020``; ver o módulo.
    """

    leiaute = VersaoLeiaute.NT009_V101
    SERIE_MAXIMA = 89999  # TSSerieDPS: [0-9]{1,4}|[0-8][0-9]{4}

    def identificador(self, dps: Dps) -> str:
        if int(dps.serie) > self.SERIE_MAXIMA:
            raise ValidacaoErro(
                f"A série {dps.serie} não é aceita no leiaute da NT 009 (máximo "
                f"{self.SERIE_MAXIMA}).")
        return super().identificador(dps)

    def _finalidade(self, inf: etree._Element) -> None:
        _no(inf, "finNFSe", 0)  # NFS-e regular; as notas de ajuste ainda não estão no XSD

    def _finalidade_ibscbs(self, no: etree._Element) -> None:
        """A finalidade saiu de ``IBSCBS`` e foi para a raiz de ``infDPS``."""

    def _codigos_servico(self, codigo: etree._Element, servico: Servico) -> None:
        _no(codigo, "cTribNac", servico.codigo_tributacao_nacional)
        _opcional(codigo, "cTribMun", servico.codigo_tributacao_municipal)
        _opcional(codigo, "cNBS", servico.codigo_nbs)
        _opcional(codigo, "cIntContrib", servico.codigo_interno)
        _no(codigo, "xDescServ", servico.descricao)

    def _ajuste_base(self, no: etree._Element, valores: Valores) -> None:
        """``vAjusteBC`` unifica ``vDedRed`` e ``gReeRepRes`` (NT 009, seção 2.3)."""
        if valores.deducao_percentual is not None or valores.deducao_valor is not None:
            ajuste = _no(no, "vAjusteBC")
            if valores.deducao_percentual is not None:
                _no(ajuste, "pAjusteBCISSQN", valores.deducao_percentual)
            else:
                _no(ajuste, "vAjusteBCISSQN", valores.deducao_valor)

    def _ibscbs(self, inf: etree._Element, dps: Dps) -> None:
        """``CST``/``cClassTrib`` em ``trib``; ``gIBSCBS`` só com detalhamento."""
        ibs = dps.ibs_cbs
        if ibs is None:
            return
        no = self._ibscbs_cabecalho(inf, ibs)
        _no(no, "indDest", ibs.indicador_destinatario)
        if ibs.destinatario is not None:
            self._pessoa(_no(no, "dest"), ibs.destinatario, com_inscricao=False)
        trib = _no(_no(no, "valores"), "trib")
        _no(trib, "CST", ibs.cst)
        _no(trib, "cClassTrib", ibs.codigo_classificacao)
        if (ibs.codigo_credito_presumido or ibs.tributacao_regular is not None
                or ibs.diferimento is not None):
            self._ibscbs_detalhe(_no(trib, "gIBSCBS"), ibs)


def montador_para(leiaute: VersaoLeiaute, ambiente: Ambiente, **opcoes) -> MontadorDps:
    """Montador do leiaute informado."""
    classe = MontadorDpsNt009 if leiaute is VersaoLeiaute.NT009_V101 else MontadorDps
    return classe(ambiente, **opcoes)
