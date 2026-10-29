import datetime as dt
from decimal import Decimal

import pytest

from freedfe.excecoes import ValidacaoErro
from freedfe.nfse.modelo import (
    BeneficioMunicipal,
    Destinatario,
    Endereco,
    ExigibilidadeSuspensa,
    IbsCbs,
    Intermediario,
    Obra,
    OpcaoSimplesNacional,
    PisCofins,
    Prestador,
    RetencaoIssqn,
    Servico,
    Substituicao,
    TipoEmitente,
    TipoSuspensao,
    TipoTotTrib,
    Tomador,
    TotTrib,
    TribFederal,
    TribMunicipal,
    TributacaoIssqn,
    Valores,
)

from fabrica_nfse import (
    CNPJ_PRESTADOR,
    CNPJ_TOMADOR,
    CPF_TOMADOR,
    MUNICIPIO,
    dps_minima,
    endereco_tomador,
    gerar_chave_nfse,
    prestador_simples,
    tomador_pj,
)


def test_dps_minima_valida_e_normaliza():
    dps = dps_minima(serie="00900", numero="6")
    assert dps.serie == 900 and dps.numero == 6
    assert dps.tipo_emitente is TipoEmitente.PRESTADOR
    assert dps.valores.servico == Decimal("1000.00")


def test_dps_e_imutavel():
    with pytest.raises(AttributeError):
        dps_minima().serie = 1  # type: ignore[misc]


class TestPessoas:
    def test_exige_exatamente_um_documento(self):
        with pytest.raises(ValidacaoErro, match="exatamente um"):
            Tomador(nome="X")
        with pytest.raises(ValidacaoErro, match="exatamente um"):
            Tomador(nome="X", cnpj=CNPJ_TOMADOR, cpf=CPF_TOMADOR)

    @pytest.mark.parametrize("campos", [{"cnpj": "11222333000182"}, {"cpf": "52998224724"},
                                        {"cnpj": "123"}])
    def test_documento_com_dv_invalido(self, campos):
        with pytest.raises(ValidacaoErro, match="E0188"):
            Tomador(nome="X", **campos)

    def test_cnpj_alfanumerico_e_cpf_sao_aceitos(self):
        assert Tomador(nome="X", cnpj="12abc34501de35").cnpj == "12ABC34501DE35"
        assert Tomador(nome="X", cpf=CPF_TOMADOR).documento == CPF_TOMADOR

    def test_tomador_exige_nome_e_textos_sao_sanitizados(self):
        with pytest.raises(ValidacaoErro, match="Nome"):
            Tomador(cnpj=CNPJ_TOMADOR)
        pessoa = Tomador(cnpj=CNPJ_TOMADOR, nome="  “Empresa” – Teste  ")
        assert pessoa.nome == '"Empresa" - Teste'
        with pytest.raises(ValidacaoErro, match="excede 300"):
            Tomador(cnpj=CNPJ_TOMADOR, nome="A" * 301)

    def test_telefone_e_email(self):
        assert Tomador(cnpj=CNPJ_TOMADOR, nome="X", telefone="(71) 99999-0000").telefone == (
            "71999990000")
        with pytest.raises(ValidacaoErro, match="Telefone"):
            Tomador(cnpj=CNPJ_TOMADOR, nome="X", telefone="123")

    def test_destinatario_e_intermediario(self):
        assert Intermediario(cnpj=CNPJ_TOMADOR, nome="Interm").nome == "Interm"
        with pytest.raises(ValidacaoErro, match="150"):
            Destinatario(cnpj=CNPJ_TOMADOR, nome="A" * 151)


class TestPrestador:
    def test_regime_obrigatorio(self):
        with pytest.raises(ValidacaoErro, match="Opção pelo Simples"):
            Prestador(cnpj=CNPJ_PRESTADOR)

    def test_me_epp_exige_regime_de_apuracao(self):
        with pytest.raises(ValidacaoErro, match="regime de apuração"):
            Prestador(cnpj=CNPJ_PRESTADOR, opcao_simples=OpcaoSimplesNacional.ME_EPP)
        nao_optante = Prestador(cnpj=CNPJ_PRESTADOR, opcao_simples=1)
        assert nao_optante.opcao_simples is OpcaoSimplesNacional.NAO_OPTANTE

    def test_precisa_de_cnpj_ou_cpf(self):
        with pytest.raises(ValidacaoErro, match="CNPJ ou CPF"):
            Prestador(nif="123", opcao_simples=1)

    def test_valores_de_enum_invalidos(self):
        with pytest.raises(ValidacaoErro, match="Opção pelo Simples"):
            Prestador(cnpj=CNPJ_PRESTADOR, opcao_simples=7)


class TestEndereco:
    def test_nacional(self):
        endereco = endereco_tomador()
        assert endereco.codigo_municipio == "2910800" and endereco.cep == "44001000"
        assert not endereco.exterior

    def test_exterior(self):
        endereco = Endereco.no_exterior("Main St", "10", "Downtown", "us", "10001", "New York",
                                        "NY")
        assert endereco.exterior and endereco.pais == "US"

    def test_nacional_xor_exterior(self):
        with pytest.raises(ValidacaoErro, match="não ambos nem nenhum"):
            Endereco("Rua", "1", "Bairro")
        with pytest.raises(ValidacaoErro, match="não ambos nem nenhum"):
            Endereco("Rua", "1", "Bairro", codigo_municipio=MUNICIPIO, cep="40000000", pais="US")

    @pytest.mark.parametrize("cep", ["1234", "abcdefgh", "123456789"])
    def test_cep_invalido(self, cep):
        with pytest.raises(ValidacaoErro):
            Endereco.nacional("Rua", "1", "Bairro", MUNICIPIO, cep)

    def test_municipio_invalido(self):
        with pytest.raises(ValidacaoErro, match="IBGE"):
            Endereco.nacional("Rua", "1", "Bairro", "999", "40000000")


class TestServico:
    def test_valido(self):
        servico = Servico("010101", "Linha 1\r\n\r\n\r\n\r\nLinha 2",
                          codigo_municipio_prestacao=MUNICIPIO,
                          codigo_tributacao_municipal="001", codigo_nbs="115022000")
        assert servico.descricao == "Linha 1\n\nLinha 2"

    @pytest.mark.parametrize("campos, mensagem", [
        ({"codigo_tributacao_nacional": "0101"}, "cTribNac"),
        ({"codigo_tributacao_municipal": "000"}, "E0315"),
        ({"codigo_tributacao_municipal": "12"}, "cTribMun"),
        ({"codigo_nbs": "123"}, "cNBS"),
        ({"descricao": "   "}, "obrigatório"),
        ({"descricao": "x" * 2001}, "2000"),
        ({"pais_prestacao": "BR"}, "exatamente um"),
        ({"codigo_municipio_prestacao": None}, "exatamente um"),
        ({"codigo_interno": "ab-12"}, "letras e números"),
    ])
    def test_invalidos(self, campos, mensagem):
        dados = dict(codigo_tributacao_nacional="010101", descricao="Serviço",
                     codigo_municipio_prestacao=MUNICIPIO)
        dados.update(campos)
        with pytest.raises(ValidacaoErro, match=mensagem):
            Servico(**dados)

    def test_prestacao_no_exterior_e_obra(self):
        servico = Servico("070201", "Obra", pais_prestacao="pt", obra=Obra(codigo_obra="OB1"))
        assert servico.pais_prestacao == "PT" and servico.obra.codigo_obra == "OB1"
        with pytest.raises(ValidacaoErro, match="exatamente um"):
            Obra(codigo_obra="a", cib="12345678")
        with pytest.raises(ValidacaoErro, match="CIB"):
            Obra(cib="123")


class TestValores:
    def test_decimais(self):
        valores = Valores("10", desconto_condicionado=1, recebido="9.5")
        assert valores.servico == Decimal("10.00") and valores.recebido == Decimal("9.50")

    @pytest.mark.parametrize("valor", [-1, "abc", "10.005", 1.5, Decimal("NaN"), 10 ** 15])
    def test_invalidos(self, valor):
        with pytest.raises(ValidacaoErro):
            Valores(valor)

    def test_valor_do_servico_obrigatorio_e_deducao_excludente(self):
        with pytest.raises(ValidacaoErro, match="obrigatório"):
            Valores(None)
        with pytest.raises(ValidacaoErro, match="percentual ou valor"):
            Valores(10, deducao_percentual=5, deducao_valor=1)
        assert Valores(10, deducao_percentual="12.5").deducao_percentual == Decimal("12.50")


class TestTributacao:
    def test_tribmunicipal_padrao_e_aliquota(self):
        trib = TribMunicipal(aliquota="2")
        assert trib.tributacao is TributacaoIssqn.TRIBUTAVEL
        assert trib.retencao is RetencaoIssqn.NAO_RETIDO and trib.aliquota == Decimal("2.00")
        with pytest.raises(ValidacaoErro, match="Alíquota"):
            TribMunicipal(aliquota="10")  # TSDec1V2: no máximo 9,99
        with pytest.raises(ValidacaoErro, match="Tributação"):
            TribMunicipal(tributacao=9)

    def test_suspensao_e_beneficio(self):
        susp = ExigibilidadeSuspensa(TipoSuspensao.DECISAO_JUDICIAL, "1" * 30)
        assert susp.numero_processo == "1" * 30
        with pytest.raises(ValidacaoErro):
            ExigibilidadeSuspensa(1, "123")
        assert BeneficioMunicipal("1" * 14, reducao_percentual="10").reducao_percentual == (
            Decimal("10.00"))
        with pytest.raises(ValidacaoErro, match="valor ou percentual"):
            BeneficioMunicipal("1" * 14, reducao_valor=1, reducao_percentual=1)

    def test_tribfederal_e_piscofins(self):
        federal = TribFederal(PisCofins("01", base_calculo="100", aliquota_pis="0.65",
                                        aliquota_cofins="3", tipo_retencao=0),
                              retencao_irrf="1.5")
        assert federal.piscofins.cst == "01" and federal.retencao_irrf == Decimal("1.50")
        with pytest.raises(ValidacaoErro):
            PisCofins("1")
        with pytest.raises(ValidacaoErro, match="tpRetPisCofins"):
            PisCofins("01", tipo_retencao=10)

    def test_tottrib_um_unico_ramo(self):
        assert TotTrib.nao_informado().tipo is TipoTotTrib.NAO_INFORMADO
        assert TotTrib.simples("6").percentual_simples == Decimal("6.00")
        valores = TotTrib.valores(1, 2, "3.5")
        assert (valores.federal, valores.estadual, valores.municipal) == (
            Decimal("1.00"), Decimal("2.00"), Decimal("3.50"))
        assert TotTrib.percentuais(10, 5, 2).tipo is TipoTotTrib.PERCENTUAIS
        with pytest.raises(ValidacaoErro):
            TotTrib.valores(1, 2, None)
        with pytest.raises(ValidacaoErro):
            TotTrib.simples(None)


class TestIbsCbs:
    def ibs(self, **alteracoes):
        dados = dict(codigo_indicador_operacao="100301", indicador_destinatario=0, cst="000",
                     codigo_classificacao="000001")
        dados.update(alteracoes)
        return IbsCbs(**dados)

    def test_valido(self):
        ibs = self.ibs(notas_referenciadas=(gerar_chave_nfse(),), codigo_credito_presumido="01")
        assert ibs.finalidade == "0" and len(ibs.notas_referenciadas) == 1

    @pytest.mark.parametrize("campos", [
        {"indicador_destinatario": 2}, {"cst": "00"}, {"codigo_classificacao": "1"},
        {"codigo_indicador_operacao": "10"}, {"tipo_operacao": 6}, {"ente_governamental": 0},
        {"indicador_consumidor_final": 3}, {"notas_referenciadas": ("123",)},
    ])
    def test_invalido(self, campos):
        with pytest.raises(ValidacaoErro):
            self.ibs(**campos)

    def test_nbs_obrigatoria_na_dps(self):
        with pytest.raises(ValidacaoErro, match="E0322"):
            dps_minima(ibs_cbs=self.ibs())
        servico = Servico("010101", "Sistema", codigo_municipio_prestacao=MUNICIPIO,
                          codigo_nbs="115022000")
        assert dps_minima(ibs_cbs=self.ibs(), servico=servico).ibs_cbs is not None


class TestSubstituicao:
    def test_motivos(self):
        subst = Substituicao(gerar_chave_nfse(), "1")
        assert subst.codigo_motivo == "01" and subst.motivo is None
        outros = Substituicao(gerar_chave_nfse(), "99", "Motivo com mais de quinze letras")
        assert outros.codigo_motivo == "99"

    @pytest.mark.parametrize("args", [("123", "01", None), (gerar_chave_nfse(), "07", None),
                                      (gerar_chave_nfse(), "99", None),
                                      (gerar_chave_nfse(), "01", "curto")])
    def test_invalidos(self, args):
        with pytest.raises(ValidacaoErro):
            Substituicao(*args)


class TestDps:
    def test_prestador_emitente_nao_informa_endereco(self):
        prestador = prestador_simples(endereco=endereco_tomador())
        with pytest.raises(ValidacaoErro, match="E0128"):
            dps_minima(prestador=prestador)
        # Quando o emitente é o tomador, o prestador é identificado pelos dados informados.
        assert dps_minima(prestador=prestador, tipo_emitente=TipoEmitente.TOMADOR,
                          tomador=tomador_pj()).prestador.endereco is not None

    def test_data_emissao_exige_fuso_e_competencia_nao_posterior(self):
        with pytest.raises(ValidacaoErro, match="fuso"):
            dps_minima(data_emissao=dt.datetime(2026, 10, 29, 10, 0))
        with pytest.raises(ValidacaoErro, match="posterior"):
            dps_minima(data_competencia=dt.date(2026, 11, 1))
        with pytest.raises(ValidacaoErro, match="data"):
            dps_minima(data_competencia=dt.datetime(2026, 10, 29, 1, 0))

    def test_emitente_define_o_documento_do_id(self):
        assert dps_minima().documento_emitente == CNPJ_PRESTADOR
        tomador = tomador_pj()
        dps = dps_minima(tipo_emitente=TipoEmitente.TOMADOR, tomador=tomador)
        assert dps.documento_emitente == CNPJ_TOMADOR
        with pytest.raises(ValidacaoErro, match="tomador"):
            dps_minima(tipo_emitente=TipoEmitente.TOMADOR)
        with pytest.raises(ValidacaoErro, match="intermediário"):
            dps_minima(tipo_emitente=TipoEmitente.INTERMEDIARIO)
        with pytest.raises(ValidacaoErro, match="CNPJ ou CPF"):
            dps_minima(tipo_emitente=TipoEmitente.TOMADOR,
                       tomador=Tomador(nif="ABC123", nome="Exterior"))

    @pytest.mark.parametrize("campos", [{"serie": 0}, {"serie": 100000}, {"numero": 0},
                                        {"numero": "x"}, {"codigo_municipio_emissor": "123"},
                                        {"tipo_emitente": 4}])
    def test_campos_invalidos(self, campos):
        with pytest.raises(ValidacaoErro):
            dps_minima(**campos)
