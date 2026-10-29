# freedfe

Biblioteca Python open source (Apache-2.0) para consultar, baixar o XML e gerar o PDF dos
documentos fiscais eletrônicos (NF-e, CT-e e MDF-e) de interesse de um CNPJ ou CPF, usando os
Web Services oficiais de **Distribuição DF-e** da SEFAZ / Ambiente Nacional, e para **emitir,
consultar e cancelar NFS-e** na API da NFS-e Nacional (Sefin Nacional e ADN), com geração do
**DANFSe em PDF com QR Code** (ver [NFS-e Nacional](#nfs-e-nacional-emissão-consulta-e-danfse)).

- Consulta por período, modelo e direção (emitidos/recebidos), com retorno em lista de objetos,
  CSV, JSON, XML ou XLSX.
- Download dos XMLs completos.
- Geração de DANFE, DACTE e DAMDFE em PDF.
- Manifestação do Destinatário (Ciência, Confirmação, Desconhecimento, Operação não Realizada).
- Controle de NSU e de consumo indevido (rejeição 656/678) conforme as Notas Técnicas.
- NFS-e Nacional: emissão idempotente (DPS tipada ou XML pronto), consulta, cancelamento,
  parâmetros municipais, sincronização pelo ADN e DANFSe local com QR Code.

## Leia antes de usar: limites do serviço oficial

O Web Service de Distribuição DF-e **não é uma consulta por período**. Ele entrega documentos
por NSU (sequência incremental por interessado). Por isso a biblioteca **sincroniza** os
documentos para um repositório local, e a consulta por período é feita sobre esse repositório.

| Limite | Consequência |
|---|---|
| Janela de 90 dias (NF-e/CT-e) e 6 meses (MDF-e) | Não serve para carga histórica. Use `importar_xmls()` com os XMLs do ERP ou da contabilidade. |
| Não existe NSU retroativo; a geração para após 60 dias sem uso (NF-e) | Ative a sincronização **antes** de precisar e agende-a a cada 60–65 min. |
| O emitente **não** recebe os próprios documentos | Documentos "de" (emitidos) vêm do ERP: use `importar_xmls()`. |
| O destinatário recebe só o resumo (`resNFe`) até manifestar | Use `pendentes_manifestacao()` e `manifestar_ciencia()`. |
| Autenticação pelo CNPJ-base do certificado; sem procuração | Use o certificado A1 do próprio CNPJ/CPF consultado. |
| 1 sequência de NSU por CNPJ; 1 h de espera ao esgotar | Um único consumidor por CNPJ. A biblioteca usa trava de arquivo e bloqueio local. |
| Consultas pontuais (`consNSU`/`consChNFe`): 20 por hora | Use só para lacunas e exceções. |

Somente certificado **A1** (.pfx/.p12). A3 (token/cartão) exigiria PKCS#11 e não é suportado.

## Instalação

```bash
pip install "freedfe[todos] @ git+https://github.com/topoconsultoria/freedfe"
```

Extras: `pdf` (BrazilFiscalReport, LGPL-3.0), `xlsx` (openpyxl, MIT), `nfse` (reportlab,
BSD, para o DANFSe), `todos`, `dev`.
Dependências obrigatórias: `cryptography` e `lxml`. Python 3.9+.

## Uso

```python
import datetime as dt
import os

from freedfe import Ambiente, Direcao, FiltroConsulta, FormatoSaida, FreeDFe, ModeloDocumento

dfe = FreeDFe(
    "/seguro/cliente.pfx", os.environ["SEFAZ_PFX_SENHA"],   # senha nunca no código
    cnpj="11222333000181",
    uf="29",                          # código IBGE da UF (29 = BA). Obrigatório para CT-e e MDF-e
    ambiente=Ambiente.HOMOLOGACAO,    # valide em homologação antes da produção
    pasta="./dados_dfe",
)

# 1. Sincronizar com a SEFAZ (única operação de distribuição que acessa o Web Service)
for resultado in dfe.sincronizar([ModeloDocumento.NFE, ModeloDocumento.CTE]):
    print(resultado.modelo, resultado.documentos, resultado.ultimo_cstat)

# 2. Consultar por período, modelo e direção
filtro = FiltroConsulta(
    data_inicial=dt.date(2026, 7, 1),
    data_final=dt.date(2026, 9, 30),
    modelos=[ModeloDocumento.NFE],
    direcao=Direcao.RECEBIDOS,        # EMITIDOS ("de"), RECEBIDOS ("para") ou TODOS
)
documentos = dfe.consultar(filtro)                       # list[ResumoDocumento]
csv_texto = dfe.consultar(filtro, formato="csv")         # str
json_texto = dfe.consultar(filtro, formato=FormatoSaida.JSON)
dfe.consultar(filtro, formato="xlsx", destino="dfe.xlsx")  # grava e devolve o Path

# 3. Baixar os XMLs e gerar os PDFs com o mesmo filtro
dfe.baixar_xmls(filtro, destino="./xml")
resultado = dfe.gerar_pdfs(filtro, destino="./pdf")
print(resultado.gerados, resultado.falhas)

# 4. Liberar o XML completo das NF-e recebidas só como resumo
pendentes = dfe.pendentes_manifestacao()
dfe.manifestar_ciencia([p.chave for p in pendentes])   # na próxima sincronização vem o XML
```

Consultas ao repositório local não precisam de certificado:

```python
leitura = FreeDFe(cnpj="11222333000181", pasta="./dados_dfe")
leitura.consultar(filtro, formato="json")
```

### Formatos de retorno de `consultar`

| `formato` | Retorno |
|---|---|
| `"lista"` (padrão) | `list[ResumoDocumento]` |
| `"csv"` | `str`, separador `;`, vírgula decimal (Excel pt-BR). Com `destino`, UTF-8 com BOM |
| `"json"` | `str`, datas ISO 8601 |
| `"xml"` | `str`, `<documentos><documento>...</documento></documentos>` |
| `"xlsx"` | `bytes`, cabeçalho congelado e filtro automático |

Colunas: `chave, modelo, numero, serie, data_emissao, emitente_documento, emitente_nome,
destinatario_documento, destinatario_nome, valor_total, situacao, protocolo, xml_completo,
tipo_xml, nsu, caminho_xml`.

### Outras operações

| Método | Descrição |
|---|---|
| `importar_xmls(pasta)` | Importa XMLs externos (emitidos pelo próprio CNPJ, histórico do ERP). |
| `consultar_chave(chave)` | NF-e por chave (`consChNFe`). Conta no limite de 20/hora. |
| `consultar_nsu(modelo, nsu)` | NSU específico (`consNSU`). Conta no limite de 20/hora. |
| `lacunas_nsu(modelo)` | NSU faltantes entre os recebidos. |
| `estado_nsu(modelo)` | Último NSU, bloqueio e última execução. |
| `info_certificado()` | Titular, CNPJ/CPF e validade do certificado. |
| `manifestar_confirmacao / _desconhecimento / _nao_realizada` | Manifestações conclusivas. |

## NFS-e Nacional: emissão, consulta e DANFSe

A NFS-e Nacional usa outro contrato: **REST/JSON síncrono** com mTLS (certificado A1, sem token).
A DPS (Declaração de Prestação de Serviços) é um XML assinado, comprimido em GZip e Base64; o
mesmo `POST` devolve a NFS-e ou a rejeição. Tudo fica em `dfe.nfse`.

```python
import datetime as dt
import os
from decimal import Decimal

from freedfe import (Ambiente, Dps, EmissaoIncertaErro, FreeDFe, OpcaoSimplesNacional, Prestador,
                     RegimeApuracaoSimples, RejeicaoNfseErro, Servico, Tomador, TotTrib,
                     TribMunicipal, Valores)

dfe = FreeDFe("/seguro/cliente.pfx", os.environ["SEFAZ_PFX_SENHA"], cnpj="11222333000181",
              ambiente=Ambiente.HOMOLOGACAO, pasta="./dados_dfe")   # homologação = produção restrita

dps = Dps(
    serie=900,                                   # série exclusiva do ERP; o nDPS é numerado aqui
    data_competencia=dt.date(2026, 10, 29),
    codigo_municipio_emissor="2927408",          # IBGE (7 dígitos)
    prestador=Prestador(
        cnpj="11222333000181", inscricao_municipal="152422",
        opcao_simples=OpcaoSimplesNacional.ME_EPP,
        regime_apuracao_simples=RegimeApuracaoSimples.FEDERAIS_E_MUNICIPAL_PELO_SIMPLES),
    tomador=Tomador(cnpj="11444777000161", nome="EMPRESA TOMADORA LTDA"),
    servico=Servico("010101", "Desenvolvimento de sistema sob encomenda",
                    codigo_municipio_prestacao="2927408"),
    valores=Valores(Decimal("1000.00")),
    trib_municipal=TribMunicipal(),              # tributável, ISSQN não retido
    tot_trib=TotTrib.simples("6.00"),            # um único ramo do xs:choice (regra do regime: Sefin)
)
try:
    nota = dfe.nfse.emitir(dps)                  # assina, envia e grava a NFS-e no repositório
    pdf = dfe.nfse.gerar_danfse(nota.chave)      # DANFSe com QR Code (extra nfse)
except RejeicaoNfseErro as erro:                 # rejeição de negócio: corrija e emita de novo
    print(erro.codigos, [e.complemento for e in erro.erros])
except EmissaoIncertaErro as erro:               # timeout/5xx: NÃO reenvie à mão
    dfe.nfse.reconciliar(erro.id_dps)            # consulta HEAD/GET /dps/{id} na Sefin
```

### Idempotência e máquina de estados

O `POST /nfse` **nunca é repetido** automaticamente. Antes de enviar, a DPS assinada é gravada e
marcada `ENVIADA`; diante de timeout, 5xx ou resposta ilegível a biblioteca consulta a Sefin pelo
Id da DPS e só então decide:

```
PENDENTE ──> ENVIADA ──201──────────────> AUTORIZADA
                 ├──4xx com erros───────> REJEITADA   (corrija; reemitir reabre a DPS)
                 └──timeout / 5xx ──────> INCERTA ──HEAD/GET /dps──> AUTORIZADA
```

- `E0014` (DPS já gerou NFS-e) é tratado como "já emitida": reconcilia e devolve a nota.
- Emitir de novo uma DPS `AUTORIZADA` devolve a nota gravada, sem chamar a Sefin.
- Com emissão incerta pendente na série, a **numeração automática é recusada** (geraria uma
  segunda nota): chame `reconciliar_pendentes()` ou informe o número explicitamente.
- Um envio por vez por emitente (trava de arquivo), para preservar a numeração.
- A auditoria (`_estado/chamadas.jsonl`) guarda status, códigos de erro e Ids, **nunca XML**.

### Métodos de `dfe.nfse`

| Método | Descrição |
|---|---|
| `emitir(dps, leiaute=None, verificar_parametros=False)` | `Dps` tipado ou XML pronto (`bytes`; não reassina se já houver `Signature`). |
| `gerar_dps_assinada(dps)` | DPS assinada para conferência, sem enviar nem reservar número. |
| `reconciliar(id_dps)` / `reconciliar_pendentes()` | Resolve emissões `ENVIADA`/`INCERTA` na Sefin. |
| `emissoes(situacao=None)` | Estado local das emissões. |
| `consultar_chave(chave)` | `GET /nfse/{chave}` e grava no repositório. |
| `cancelar(chave, motivo, justificativa)` | Evento 101101, assinado, com situação local atualizada. |
| `listar_eventos(chave)` | Eventos na Sefin, gravados no repositório. |
| `sincronizar(max_lotes=200, cnpj_consulta=None)` | ADN por NSU: notas tomadas e eventos fora do ERP. |
| `convenio(cod_mun)`, `parametros_servico(...)`, `parametros_contribuinte(...)` | Parâmetros municipais com cache de 24 h. |
| `verificar_pre_emissao(dps)` | Bloqueia município sem convênio (E0037). |
| `gerar_danfse(chave, destino=None)` | PDF com QR Code e marca d'água de cancelada/substituída. |

`dfe.consultar(FiltroConsulta(modelos=["nfse"]))`, `baixar_xmls`, `gerar_pdfs`, `importar_xmls`
e `sincronizar(["nfse"])` também funcionam para NFS-e. A situação das notas considera os eventos
armazenados: cancelamento (101101, 105104, 305101) e substituição (105102).

### Leiautes e datas

| Ambiente | Leiaute da DPS |
|---|---|
| Produção restrita (`Ambiente.HOMOLOGACAO`) | NT 009 v1.01, desde 20/10/2026 |
| Produção até 30/11/2026 | NT 004 v2.0 (XSD 1.01) |
| Produção a partir de 01/12/2026 | NT 009 v1.01 |

A escolha é automática (`VersaoLeiaute.selecionar`), pela **data de recepção** e não pela
competência, ou fixa por `ConfiguracaoNfseLocal(leiaute=...)` / `emitir(..., leiaute=...)`.
Mesmo sem IBS/CBS os leiautes **não** geram o mesmo XML (conferido nos XSD oficiais):

| Item | NT 004 v2.0 | NT 009 v1.01 |
|---|---|---|
| `finNFSe` (finalidade) | ausente | obrigatório, logo após `tpEmit` |
| `cServ` | `xDescServ` antes de `cNBS` | `xDescServ` por último |
| Redução da base | `vDedRed` | `vAjusteBC` (`pAjusteBCISSQN` ou `vAjusteBCISSQN`) |
| Série (`serie`) | até 99999 | até 89999 (acima é recusada localmente) |
| Id do pedido de evento | 62 posições (`nPedRegEvento`) | 59 posições |
| `IBSCBS` | `gIBSCBS` antes de `CST`/`cClassTrib` | `indDest`/`dest` dentro de `IBSCBS`; `CST`/`cClassTrib` antes de `gIBSCBS` |

O modelo tipado cobre prestadores de serviço em geral; o que não couber (comércio exterior,
reembolso de IBS/CBS, dedução por documentos) entra pelo XML pronto.

### Validação pelo XSD oficial (opcional)

Os XSD não acompanham a biblioteca. Baixe o pacote do leiaute no portal da NFS-e Nacional e
aponte a pasta; a DPS é validada **antes** de assinar e enviar:

```python
from freedfe import ConfiguracaoNfseLocal, VersaoLeiaute

dfe = FreeDFe(..., config_nfse=ConfiguracaoNfseLocal(
    pasta_xsd={VersaoLeiaute.NT004_V2: "/xsd/NFSe-ESQUEMAS_XSD-v1.01-20260209"}))
```

Os testes de schema rodam com `FREEDFE_XSD_NFSE_NT004=<pasta> pytest` (e `..._NT009` para o
pacote PRODREST); sem as variáveis ficam ignorados. No pacote de produção (NT 004) use a pasta
`Schemas/1.01`. O XSD publicado traz o padrão de `serie` com `^...$` (inválido em XSD, que ancora
sozinho); o `ValidadorXsd` remove as âncoras ao carregar, sem alterar os arquivos.

### Estrutura de dados da NFS-e

```
<pasta>/<ambiente>/<cnpj_cpf>/
├── _estado/
│   ├── emissoes_nfse.json     # estado por Id da DPS (situação, chave, erros, tentativas)
│   ├── emissao_nfse.lock      # um envio concorrente por emitente
│   ├── nsu_nfse.json          # sequência do ADN (nsu_nfse_<cnpj>.json para outro CNPJ)
│   └── parametros_nfse/<codMun>/convenio.json
└── nfse/
    ├── indice.json
    ├── 2026-10/<chave>-NFSe.xml                  # NFS-e (documento fiscal), exatamente como recebida
    ├── eventos/2026-10/<chave>-<tipo>-EVT....xml
    ├── eventos_enviados/2026-10/<chave>-101101-pedido.xml / -resposta.json
    ├── dps/2026-10/<idDPS>-dps.xml / -resposta.json   # evidência por tentativa (-dps-2.xml...)
    └── danfse/<chave>.pdf
```

### Hipóteses e limites conhecidos

A documentação oficial está defasada em relação ao serviço real. **Conferido** contra os XSD
oficiais: DPS (NT 004 e NT 009), NFS-e, evento e pedido de cancelamento assinado, inclusive a
estrutura de `IBSCBS` da NT 009. **Conferido** nos documentos oficiais: rotas dos manuais da Sefin e
do ADN, leiaute e QR Code do DANFSe (NT 008 v1.02). **Observado** nos servidores, sem certificado
(`FREEDFE_TESTE_REDE=1 pytest tests/test_rede_real.py`):

- A Sefin de **produção** só monta `/SefinNacional`; `/API/SefinNacional` dá 404. Em produção não
  use `prefixo_api`.
- A Sefin de **produção restrita** monta `/SefinNacional` e `/API/SefinNacional` (403 sem ICP-Brasil).
- O IIS da Sefin devolve 403 a quem não tem certificado ICP-Brasil; ADN e parametrização
  recusam o certificado já no handshake TLS (reset de conexão ou `bad record mac`).
- A Consulta Pública do QR Code (`/ConsultaPublica/?tpc=1&chave=`) existe nos dois ambientes.

**Observado** na produção restrita **com certificado A1 real** (smoke test, 29/10/2026):

- mTLS aceito. `HEAD /dps/{id}` de DPS inexistente devolve **404** em `/SefinNacional` e em
  `/API/SefinNacional`: os dois prefixos atendem, e o padrão sem prefixo está correto.
- O convênio responde em `adn.../parametrizacao/{codMun}/convenio`, com os indicadores dentro de
  `parametrosConvenio`, como inteiros 0/1: `aderenteAmbienteNacional` e `aderenteEmissorNacional`.
- **Todo XML enviado à Sefin precisa da declaração UTF-8.** Uma DPS ASCII, válida como UTF-8, mas
  sem `<?xml version="1.0" encoding="UTF-8"?>`, foi recusada com **E1229** ("XML não está utilizando
  codificação UTF8") antes da assinatura. A biblioteca acrescenta a declaração na DPS e no pedido de
  evento. Num XML pronto já assinado, ela só prefixa a declaração, o que não invalida a assinatura,
  e recusa localmente outra codificação declarada.

**Ainda não confirmado** (só com o A1; teste em produção restrita antes de produção; tudo é
configurável ou tolerante):

1. Aceitação da assinatura pela Sefin: o primeiro teste real parou no E1229. O smoke test agora
   só dá "OK" com a rejeição de negócio esperada, E0712.
2. Status de `HEAD /dps/{id}` para DPS existente (200), nomes dos campos JSON de resposta (emissão,
   evento, ADN) e semântica do NSU do ADN (a biblioteca pede o último NSU recebido, o que tolera as
   duas).
3. Semântica exata de `aderenteEmissorNacional`, que gera só aviso, e o nome do campo da data de
   ativação do convênio. Os demais parâmetros municipais são tratados como dicionário tolerante.
4. Algoritmo do dígito verificador da chave de 50 posições (validação só estrutural).
5. Regras municipais de cancelamento (prazo, análise fiscal, E0824) só a Sefin aplica; a
   biblioteca devolve o código de rejeição.
6. Não há contingência offline documentada: em indisponibilidade, reenvie depois (a competência
   permite emissão tardia; confirme as regras com o município).
7. Só certificado A1. O certificado da conexão deve ser do emitente (E0718).

**DANFSe.** Segue a NT 008 v1.02 (DANFSe v2.0): blocos e rótulos do Anexo I, traço nos campos
vazios, blocos suprimidos só com a mensagem oficial, QR Code de 25 mm com a legenda oficial e
"NFS-e SEM VALIDADE JURÍDICA" fora de produção. Diferenças assumidas: fonte Helvetica (não Arial),
logomarca textual, posições calculadas pelo fluxo da página e sem canhoto. Nomes de município só
existem no XML para o emitente; para tomador, destinatário e intermediário aparece o código IBGE.

`examples/nfse_smoke_producao_restrita.py` valida mTLS, rota, convênio e o perfil de assinatura
(Exclusive C14N + RSA-SHA256) na produção restrita **sem gerar nota**. O veredito é estrito: só a
rejeição esperada E0712 conta como sucesso, e qualquer outro resultado sai com código 1.

## Estrutura de dados local

```
<pasta>/<producao|homologacao>/<cnpj_cpf>/
├── _estado/
│   ├── nsu_nfe.json          # ultNSU, maxNSU, bloqueio, consultas pontuais
│   ├── nsu_nfe.lock          # trava de consumidor único (durante a sincronização)
│   └── chamadas.jsonl        # trilha de auditoria de cada chamada (cStat, NSU, duração)
├── nfe/
│   ├── indice.json           # resumos, eventos e NSU recebidos
│   ├── 2026-07/<chave>-nfeProc.xml
│   └── eventos/2026-07/<chave>-110111-<protocolo>.xml
├── cte/ ...
├── mdfe/ ...
└── nao_reconhecidos/         # XML de schema desconhecido: preservado, nunca descartado
```

O XML é gravado exatamente como recebido. Um resumo nunca substitui um XML completo.

## Arquitetura

| Módulo | Responsabilidade |
|---|---|
| `fachada.FreeDFe` | API pública; monta as dependências |
| `distribuicao` | Mensagens `distDFeInt`, interpretação do retorno, estado de NSU, trava, cliente |
| `manifestacao` | Evento, assinatura XMLDSig, envio ao Ambiente Nacional |
| `assinatura` | XMLDSig por perfil: `PERFIL_NFE` (C14N 1.0, RSA-SHA1) e `PERFIL_NFSE` (Exclusive C14N, RSA-SHA256) |
| `transporte` | `ClienteSoap` (distribuição) e `ClienteRest` (NFS-e), ambos mTLS TLS 1.2+ |
| `nfse` | Modelo da DPS, montadores por leiaute, clientes Sefin/ADN/parâmetros, estado de emissão, eventos, `ServicoNfse` |
| `documentos` | Leitura dos XMLs e extração dos dados (NF-e, CT-e, MDF-e, resumos, eventos) |
| `repositorio` | `RepositorioDocumentos` (contrato), `RepositorioArquivos`, filtro, importador |
| `exportacao` | Exportadores CSV/JSON/XML/XLSX e fábrica por formato |
| `pdf` | `GeradorPdf` (contrato), BrazilFiscalReport (DANFE/DACTE/DAMDFE) e `GeradorDanfse` (reportlab) |
| `servicos` | Endpoints, namespaces e versões dos leiautes (ponto único de atualização) |

`Transporte`, `RepositorioDocumentos` e `GeradorPdf` são classes abstratas: dá para plugar outro
transporte, um repositório em PostgreSQL ou outro gerador de PDF sem alterar o restante.

## Base normativa

- NF-e: NT 2014.002 v1.40 (schema `PL_NFeDistDFe_104`, `versao="1.01"`)
- NF-e, manifestação: NT 2020.001 v1.60 (prazo conclusivo de 90 dias)
- CT-e: NT 2015.002 v1.05 (`PL_CTeDistDFe_100`)
- MDF-e: NT 2015.002 v1.03 (`PL_MDFeDistDFe_100`)
- CNPJ alfanumérico: NT Conjunta 2025.001 (aceito na validação e nas chaves)
- NFS-e Nacional: NT 004 v2.0 (XSD 1.01), NT 007 (PIS/COFINS), NT 008 v1.02 (DANFSe v2.0),
  NT 009 v1.01 (IBS/CBS), Manual Contribuintes - Emissor Público API v1.2 e Manual ADN v1.0

**Hipótese não validada:** a NT do CT-e não traz exemplo de envelope. O namespace usado
(`.../cte/wsdl/CTeDistribuicaoDFe`) segue a analogia com a NF-e. Confirme no `?wsdl` em
homologação antes de usar em produção.

## Boas práticas operacionais

- Agende `sincronizar()` a cada 60–65 min por CNPJ e alerte se um CNPJ passar 45 dias sem execução.
- Monitore o vencimento do certificado (`info_certificado().dias_para_vencer`).
- Manifestação é ato jurídico do contribuinte: automatize a Ciência só com autorização formal;
  deixe as conclusivas para o fluxo de recebimento.
- Se o TLS do servidor não validar, informe `ca_bundle` com a cadeia ICP-Brasil. Nunca desabilite
  a verificação.
- **Proxy.** Por padrão os transportes usam `HTTPS_PROXY`/`NO_PROXY` do ambiente (túnel `CONNECT`
  `http://`). `FreeDFe(..., proxy="http://usuario:senha@host:3128")` força um, `proxy=None` nunca usa.
  O mTLS segue fim a fim; proxy que intercepta TLS impede o certificado cliente de chegar à Fazenda.
- Os XMLs contêm dados pessoais (LGPD): restrinja o acesso à pasta de dados.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

Por padrão os testes não acessam a rede externa. Há três camadas:

- **Unitários:** transporte falso, certificado autoassinado gerado na hora e XMLs sintéticos.
- **Integração local** (`tests/test_integracao_http.py`): servidor HTTPS com mTLS e proxy `CONNECT`
  em `127.0.0.1` que imitam Sefin, ADN e parametrização, conferem a assinatura XMLDSig recebida e
  exercitam handshake, timeout, E0014, reconciliação sem segundo POST, cancelamento, sincronização
  e DANFSe em sockets reais.
- **Rede real, opt-in** (`FREEDFE_TESTE_REDE=1 pytest tests/test_rede_real.py`): comportamento dos
  servidores da NFS-e sem certificado (prefixos, 403, recusa no handshake, Consulta Pública).

Com os XSD oficiais da NFS-e em disco, `FREEDFE_XSD_NFSE_NT004=<pasta>` e
`FREEDFE_XSD_NFSE_NT009=<pasta>` validam a DPS, a NFS-e, o evento e o pedido de cancelamento
contra o schema (inclusive a DPS que o servidor local recebe na emissão).
