# Status da implementação (v0.2.0)

Etapas 0 a 12 **implementadas** na branch `claude/plano-nfse-nacional`, um commit por etapa,
com `pytest` e `ruff` verdes. Depois delas, uma rodada de verificação contra os artefatos oficiais
(pacote XSD de produção e pacote PRODREST da NT 009, NT 008, manuais) e contra os servidores reais
sem certificado, e as correções do primeiro smoke test real com A1. Resultado dos testes: 521
passam e 25 ficam ignorados por padrão; com os dois pacotes XSD são 536; com
`FREEDFE_TESTE_REDE=1` também, 546 (os 10 de rede real).
O restante deste documento é o plano aprovado, mantido como foi escrito. Os desvios abaixo
vieram de conferir o plano contra os **XSD oficiais** (NT 004 v2.0 e NT 009 v1.01), os exemplos
oficiais, a NT 008 e o relatório técnico.

## Desvios em relação ao plano

| Tema | Plano | Implementado | Motivo |
|---|---|---|---|
| Id do pedido de evento | 62 posições na NT 004 e 59 na NT 009 | **59** nos dois leiautes | O XSD 1.01 (`PRE[0-9]{56}`) recusa 62; o relatório confirma |
| Escolha do leiaute | por `dCompet` | pela **data de recepção** (hoje, Brasília) | A Sefin valida o schema em vigor no recebimento, não na competência |
| `ind_gIBSCBS`, `verCalcIBSCBS` | campos da DPS | **removidos** | O primeiro é coluna da tabela de CST; o segundo pertence à resposta |
| Grupo `gIBSCBS` da DPS | base, alíquotas e valores informados | CST, `cClassTrib`, `cCredPres`, `gTribRegular`, `gDif` | O XSD v1.01 mostra que os valores são calculados pela Sefin |
| `prest/regTrib` | opcional no modelo | **obrigatório** (`opSimpNac`, `regEspTrib`) | Exigido pelo XSD |
| `Id` da DPS | emitente = prestador | documento do **emitente** (`tpEmit`) | O Id usa a inscrição de quem emite |
| `ErroNfse` | em `nfse/resposta.py` | em `excecoes.py` (reexportado) | Evita import circular |
| Clientes REST | cada um com sua chamada | base comum `nfse/cliente_base.py` | Mesma política de repetição e auditoria |
| `GeradorPdf.gerar(situacao=)` | passado a todos | passado **só para NFS-e** | Não quebra geradores personalizados já existentes |
| Numeração automática | só `proximo_numero` | **recusada** com emissão `ENVIADA`/`INCERTA` na série | Reenviar sem número fixo geraria uma segunda nota |
| Retries | 3 tentativas | 1 + 3 repetições (2, 4 e 8 s), configuráveis | Leitura literal de "backoff 2/4/8 s" |
| Diferenças NT 004 × NT 009 | só IBS/CBS | também `finNFSe` na raiz, ordem de `cServ`, `vAjusteBC` no lugar de `vDedRed` e série até 89999 | XSD oficial do PRODREST (validado) |
| URL do QR Code | `.../ConsultaPublica/?chave=` (hipótese) | `.../ConsultaPublica/?tpc=1&chave=` | NT 008 v1.02, item 2.4.3 |
| Leiaute do DANFSe | conteúdo do XML, campos a conferir | blocos, rótulos e regras do Anexo I da NT 008 | NT 008 v1.02 |
| Proxy | não previsto | `proxy="auto"` (HTTPS_PROXY/NO_PROXY) ou URL; túnel CONNECT | Necessário em redes corporativas e neste ambiente |
| Testes de rede | só transporte falso | servidor mTLS local, proxy CONNECT e testes opt-in nos servidores reais | Cobrir handshake, timeout e fluxo completo em sockets |
| Declaração XML | `xml_declaration=False`, como no relatório | `<?xml version="1.0" encoding="UTF-8"?>` em todo XML enviado | A Sefin real recusou com E1229 a DPS sem declaração |
| Convênio | campos na raiz | busca também em `parametrosConvenio`; `aderente_emissor_nacional` | Resposta real da produção restrita |
| Veredito do smoke | lista de códigos ruins | só E0712 é sucesso; saída com código 1 nos demais casos | O E1229 passou como "OK" no primeiro teste real |

Adições além do plano: `ConfiguracaoNfseLocal.pausa`, `esperas_consulta`, `bloqueio_adn_segundos`,
`RepositorioDocumentos.obter_atual`, `ClienteParametros(rota_legada=True)` e
`ServicoNfse.gerar_dps_assinada`.

## O que ficou como hipótese (só com o certificado A1)

Resolvido na rodada de verificação: estrutura da DPS da NT 009 (validada contra o XSD oficial),
QR Code e leiaute do DANFSe (NT 008) e rotas dos manuais. Observado sem certificado: produção só
monta `/SefinNacional`; produção restrita monta `/SefinNacional` e `/API/SefinNacional`; ADN e
parametrização recusam certificado não ICP-Brasil no handshake.

Resolvido com o primeiro smoke test real, com A1, em 29/10/2026: os dois prefixos da produção
restrita aceitam o certificado; `HEAD /dps/{id}` de DPS inexistente devolve 404; a forma da
resposta de convênio; a exigência da declaração UTF-8 (E1229).

1. Emissão autorizada. A assinatura foi aceita por contraste (adulterada: E0714; correta: E0120
   de cadastro); falta o cadastro complementar do prestador no CNC da produção restrita.
2. Status 200 de `HEAD /dps/{id}` para DPS existente, nomes dos campos JSON de resposta, de evento
   e do ADN, e semântica do NSU do ADN.
3. Semântica de `aderenteEmissorNacional` e nome do campo da data de ativação do convênio.
4. Dígito verificador da chave de 50 posições (validação estrutural apenas).

O script `examples/nfse_smoke_producao_restrita.py` cobre rota, convênio e assinatura com o A1.

---

# Plano de implementação: NFS-e Nacional (Sefin/ADN) na biblioteca `freedfe`

## Contexto

A `freedfe` (v0.1.0, Apache-2.0, ~4,1 mil linhas) hoje cobre **distribuição** de NF-e/CT-e/MDF-e
(SOAP, NSU), manifestação do destinatário, repositório local em arquivos, exportação e PDF via
BrazilFiscalReport. O relatório técnico anexado (out/2026) mostra que a NFS-e Nacional usa outro
contrato: **REST/JSON síncrono com mTLS**, XML da DPS assinado com **Exclusive C14N + RSA-SHA256**,
comprimido em GZip+Base64, e **DANFSe gerado localmente** (API oficial suspensa em 03/08/2026).

Objetivo: incluir na biblioteca, de forma incremental e testável sem rede, a **emissão** de NFS-e
(DPS em XML pronto **ou** via modelo tipado, leiautes **NT 004 v2.0 e NT 009 v1.01**), a
**consulta** (por chave, por Id da DPS, eventos, parâmetros municipais e distribuição ADN por NSU),
o **cancelamento** (evento 101101) e a **geração do DANFSe em PDF com QR Code** (reportlab).
Público: empresas prestadoras de serviços em geral (não específico do agro).

Decisões já tomadas com o usuário:

| Tema | Decisão |
|---|---|
| Entrada da DPS | XML pronto (núcleo) **e** montador tipado (dataclasses) |
| Leiaute | NT 004 v2.0 e NT 009 v1.01 desde o início, selecionados por flag ou por data |
| Escopo extra | Cancelamento 101101, parâmetros municipais com cache, sincronização ADN por NSU, validação XSD opcional |
| PDF/QR | reportlab (BSD, QR nativo), novo extra `nfse` |

Princípios herdados da base: só `cryptography` + `lxml` como obrigatórias; contratos abstratos
(`Transporte`, `RepositorioDocumentos`, `GeradorPdf`) substituíveis; XML gravado **exatamente** como
recebido; testes sem rede (transporte falso, certificado autoassinado, XML sintético); auditoria
em JSONL; URLs centralizadas em `servicos.py`; senhas nunca em argumento de linha de comando.

---

## Arquitetura alvo

```
src/freedfe/
├── assinatura.py              # NOVO (movido de manifestacao/assinatura.py) + PerfilAssinatura
├── manifestacao/assinatura.py # vira re-export (compatibilidade)
├── transporte.py              # + RespostaHttp, TransporteRest (ABC), ClienteRest, contexto TLS compartilhado
├── tipos.py                   # + ModeloDocumento.NFSE, SituacaoDocumento.SUBSTITUIDO
├── validadores.py             # + validar_chave_nfse (50), validar_id_dps (45), validar_codigo_ibge (7)
├── servicos.py                # + ConfiguracaoNfse (bases Sefin/ADN/parametrização, prefixo /API/, URL consulta pública)
├── excecoes.py                # + RejeicaoNfseErro, EmissaoIncertaErro
├── documentos/extratores.py   # + ExtratorNFSe, ExtratorEventoNFSe
├── documentos/leitor.py       # mapeia raízes NFSe / evento NFS-e
├── documentos/resumo.py       # EVENTOS_CANCELAMENTO[NFSE], EVENTOS_SUBSTITUICAO
├── pdf/gerador.py             # GeradorPdfPadrao (composto): NFSE -> GeradorDanfse, demais -> BrazilFiscalReport
├── pdf/danfse.py              # NOVO: GeradorDanfse (reportlab + QR)
└── nfse/                      # NOVO pacote
    ├── leiaute.py             # VersaoLeiaute (NT004_V2, NT009_V101), namespace, atributo versao, seleção por data
    ├── identificadores.py     # id_dps(), decompor_chave(), id_pedido_evento()
    ├── texto.py               # sanitização TSString (aspas curvas, travessão, controle, espaços)
    ├── modelo.py              # dataclasses: Dps, Prestador, Tomador, Intermediario, Servico, Valores, TribMunicipal, TribFederal, TotTrib, IbsCbs, Substituicao
    ├── montador_dps.py        # MontadorDps (base, ordem do XSD) + MontadorDpsNt004 + MontadorDpsNt009
    ├── validador_xsd.py       # ValidadorXsd (opcional: pasta de XSD por leiaute)
    ├── envelope.py            # gz_b64 / desfazer_gz_b64 (tolerante a Base64 duplo)
    ├── resposta.py            # ErroNfse, RetornoEmissao, RetornoEventoNfse, parse tolerante PascalCase/camelCase
    ├── estado_emissao.py      # EstadoEmissao: máquina de estados PENDENTE→ENVIADA→AUTORIZADA/REJEITADA/INCERTA, numeração local
    ├── cliente_sefin.py       # ClienteSefin: POST /nfse, GET /nfse/{chave}, HEAD+GET /dps/{id}, eventos
    ├── eventos.py             # TipoEventoNfse, MontadorPedidoEvento (101101)
    ├── parametros.py          # ClienteParametros + CacheParametros (TTL 24 h)
    ├── cliente_adn.py         # ClienteAdn: GET /contribuintes/DFe/{NSU}
    └── servico.py             # ServicoNfse: sub-fachada exposta como FreeDFe.nfse
```

Exposição pública: `FreeDFe.nfse` (propriedade preguiçosa) devolve `ServicoNfse`, que compartilha
certificado, ambiente, repositório, auditoria e pasta com a fachada. As operações genéricas da fachada
(`consultar`, `baixar_xmls`, `gerar_pdfs`, `importar_xmls`) passam a aceitar `ModeloDocumento.NFSE`
sem mudança de assinatura.

Estrutura em disco (dentro de `<pasta>/<ambiente>/<cnpj_cpf>/`):

```
_estado/
├── nsu_nfse.json              # sequência ADN (reusa EstadoNSU)
├── nsu_nfse.lock
├── emissao_nfse.lock          # 1 envio concorrente por emitente
├── emissoes_nfse.json         # EstadoEmissao (por Id da DPS)
└── parametros_nfse/<codMun>/  # cache JSON com TTL
nfse/
├── indice.json
├── <AAAA-MM>/<chave>-NFSe.xml           # NFS-e (documento fiscal) via repositório
├── eventos/<AAAA-MM>/<chave>-<tipo>-<id>.xml
└── dps/<AAAA-MM>/<idDPS>-dps.xml        # DPS assinada exatamente como enviada
                 <idDPS>-resposta.json   # corpo bruto da resposta (auditoria)
```

---

## Etapas

Cada etapa termina com `pytest` e `ruff check src tests` verdes e é mergeável isoladamente.
Ordem pensada para que o núcleo de emissão (Etapa 7) chegue com todas as fundações testadas.

### Etapa 0 — Fundações (tipos, validadores, exceções, catálogo de serviços)

Arquivos: `tipos.py`, `validadores.py`, `excecoes.py`, `servicos.py`, `nfse/__init__.py`, `nfse/leiaute.py`.

0.1 `tipos.py`
- `ModeloDocumento.NFSE = "nfse"`. `_CODIGOS_MODELO` **não** ganha NFSE (não há código de modelo na chave).
- `ModeloDocumento.de_chave`: se `len(chave) == 50` → `NFSE`; senão comportamento atual.
- `SituacaoDocumento.SUBSTITUIDO = "substituido"`.

0.2 `validadores.py`
- `validar_chave_nfse(chave)`: 50 posições, `[0-9]{7}[12][12][0-9A-Z]{14}[0-9]{13}[0-9]{4}[0-9]{9}[0-9]`
  (validação estrutural; DV módulo 11 fica como **hipótese** atrás de `verificar_dv=False` até
  confirmar o algoritmo no Anexo I).
- `validar_id_dps(id)`: `DPS` + 42 posições (`[0-9]{7}[12][0-9A-Z]{14}[0-9]{5}[0-9]{15}`).
- `validar_codigo_ibge(codigo)`: 7 dígitos, prefixo nas `UFS_IBGE` existentes.
- `exigir_chave_nfse`, `exigir_id_dps` (mesmo padrão de `exigir_chave`).

0.3 `excecoes.py`
- `RejeicaoNfseErro(FreeDFeErro)`: atributos `erros: list[ErroNfse]`, `status_http`, `id_dps`.
  Propriedade `codigos`. Mensagem lista `E0xxx: descrição`.
- `EmissaoIncertaErro(ComunicacaoErro)`: timeout/5xx **sem** confirmação via `HEAD /dps/{id}`;
  carrega `id_dps` e instrui a chamar `reconciliar()`. Nunca reenviar automaticamente.

0.4 `servicos.py` — `ConfiguracaoNfse` (dataclass frozen), uma por ambiente:
- `base_sefin` (produção `https://sefin.nfse.gov.br/SefinNacional`, restrita
  `https://sefin.producaorestrita.nfse.gov.br/SefinNacional`), `prefixo_api: str = ""`
  (opção `"/API"` para a produção restrita, divergência não resolvida), `base_adn_contribuintes`,
  `base_parametrizacao` (host ADN `/parametrizacao`), `url_consulta_publica`
  (**hipótese**: `https://www.nfse.gov.br/ConsultaPublica/?chave={chave}`; confirmar no anexo da
  NT 008), `timeout_emissao=60`, `timeout_consulta=30`.
- Função `configuracao_nfse(ambiente, prefixo_api="")`.
- Docstring com as datas: NT 004 v2.0 até 30/11/2026, NT 009 v1.01 a partir de 01/12/2026.

0.5 `nfse/leiaute.py`
- `VersaoLeiaute(Enum)`: `NT004_V2`, `NT009_V101`. Atributos por membro: `namespace`
  (`http://www.sped.fazenda.gov.br/nfse`), `versao_dps` (**hipótese**: `"1.01"` em ambas; o
  pacote `PRODREST…20261020` pode trazer outro valor — ler do XSD e ajustar), `tamanho_id_evento`
  (62 na NT 004 com `nPedRegEvento`; 59 na NT 009), `inicio_vigencia_producao`.
- `VersaoLeiaute.selecionar(ambiente, data_referencia)`: produção restrita → sempre NT 009
  (desde 20/10/2026); produção → NT 004 se `data < 2026-12-01`, senão NT 009. Parâmetro explícito
  sempre vence.

Testes: `tests/test_validadores.py` (novas funções), `tests/test_leiaute.py` (seleção por
ambiente/data), ajuste em `test_estado`/`test_fachada` se algo listar `ModeloDocumento`.

### Etapa 1 — Transporte REST com mTLS

Arquivo: `transporte.py`; `tests/conftest.py`.

1.1 Extrair de `ClienteSoap._obter_contexto` (transporte.py:66-75) a função de módulo
`criar_contexto_tls(certificado, ca_bundle) -> ssl.SSLContext` (TLS 1.2+, `arquivos_pem()` do
`CertificadoA1`, `verificar_validade()`). `ClienteSoap` passa a usá-la. Sem mudança de comportamento.

1.2 Novos tipos:
```python
@dataclass(frozen=True)
class RespostaHttp: status: int; corpo: bytes; cabecalhos: dict[str, str]

class TransporteRest(ABC):
    def requisitar(self, metodo, url, corpo: bytes | None = None,
                   cabecalhos: dict[str, str] | None = None, timeout: int | None = None) -> RespostaHttp
```
`ClienteRest(TransporteRest)` com `http.client` (sem `requests`): mTLS pelo contexto compartilhado,
`User-Agent: freedfe/<versão>`, `Accept: application/json`. **Não** levanta em 4xx/5xx (quem interpreta
é o cliente Sefin); levanta `ComunicacaoErro` em `OSError`/`HTTPException`/timeout. `HEAD` lê e
descarta o corpo.

1.3 `tests/conftest.py`: `TransporteRestFalso` (lista de `RespostaHttp` programadas; registra
`(metodo, url, corpo, cabecalhos)`), no mesmo estilo de `TransporteFalso`.

Testes: contexto TLS criado uma vez; cabeçalhos; propagação de status sem exceção; `ComunicacaoErro`
em falha de socket (monkeypatch em `http.client.HTTPSConnection`).

### Etapa 2 — Assinatura XMLDSig parametrizada (perfil NFS-e)

Arquivos: `assinatura.py` (novo), `manifestacao/assinatura.py` (re-export), `tests/test_assinatura.py`.

2.1 Mover `AssinadorXml` para `freedfe/assinatura.py`. `manifestacao/assinatura.py` passa a
`from freedfe.assinatura import *` + `__all__` (mantém `tests/test_manifestacao.py` e importações
externas).

2.2 `PerfilAssinatura` (dataclass frozen): `c14n_algoritmo`, `c14n_exclusivo: bool`,
`assinatura_algoritmo`, `digest_algoritmo`, `hash` (`hashes.SHA1()`/`hashes.SHA256()`).
- `PERFIL_NFE` = atual (C14N inclusiva, RSA-SHA1, SHA1) — **padrão**, zero regressão.
- `PERFIL_NFSE` = `http://www.w3.org/2001/10/xml-exc-c14n#`, RSA-SHA256, SHA-256
  (confirmado empiricamente no relatório: inclusiva → E0714, C14N 1.1 → E0715).

2.3 `AssinadorXml(certificado, perfil=PERFIL_NFE)`; `assinar(elemento, alvo=None)`: `alvo` padrão
`elemento[0]` (compatível); `Signature` anexada como **último filho de `elemento`** (irmã após
`infDPS`, exigência E1235), **sem prefixo** (já é namespace default, evita E1228), `KeyInfo` só com o
certificado final (já é assim; cadeia gera E0714). Canonicalização: `etree.tostring(alvo,
method="c14n", exclusive=perfil.c14n_exclusivo)`; transforms `enveloped-signature` + C14N do perfil.

2.4 `verificar_assinatura(elemento, perfil) -> bool` (função de módulo): recalcula digest e verifica
`SignatureValue` com a chave pública do `X509Certificate` embutido. Usada nos testes e, na Etapa 7,
para conferir a assinatura da NFS-e devolvida (opcional, não bloqueante).

Testes: (a) perfil NF-e inalterado (reaproveitar `test_assinatura_verificavel`); (b) perfil NFS-e:
`SignedInfo` com os URIs corretos, digest SHA-256 **sobre a forma canônica exclusiva** cujos bytes
começam com `<infDPS xmlns="http://www.sped.fazenda.gov.br/nfse"` (armadilha libxml2 2.11 do
relatório: xmlns herdado perdido → E0714); (c) após serializar e re-parsear, `verificar_assinatura`
é verdadeiro; (d) `Signature` é irmã de `infDPS`, não filha.

### Etapa 3 — Identificadores, envelope e resposta (funções puras)

Arquivos: `nfse/identificadores.py`, `nfse/envelope.py`, `nfse/resposta.py`, `nfse/texto.py`.

3.1 `identificadores.py`
- `id_dps(codigo_municipio, documento, serie, numero) -> str` (45): `DPS` + IBGE(7) + tipo
  (1=CPF, 2=CNPJ) + inscrição(14, CPF com `000` à esquerda) + série(5, zeros à esquerda **só no Id**)
  + nDPS(15). Divergência gera E0004; teste cobre CPF e CNPJ alfanumérico.
- `decompor_chave(chave) -> ChaveNfse` (dataclass: município, ambiente_gerador, tipo_inscrição,
  inscrição, numero, aamm, codigo, dv).
- `id_pedido_evento(chave, tipo_evento, leiaute, n_pedido=None)`: `PRE` + chave + tipo (59) na
  NT 009; + `nPedRegEvento`(3) (62) na NT 004.
- `formatar_chave(chave)` para o DANFSe (grupos de 4).

3.2 `envelope.py`: `comprimir_b64(xml: bytes) -> str`, `descomprimir_b64(texto) -> bytes` tolerante
a **Base64 duplo** (`arquivoXml` da consulta de eventos): decodifica, se não começar com o magic
gzip `1f 8b`, decodifica de novo; falha → `RespostaInvalidaErro`.

3.3 `resposta.py`
- `ErroNfse(codigo, descricao, complemento)`; `RetornoEmissao(chave, xml_nfse: bytes | None,
  alertas: list[ErroNfse], tipo_ambiente, versao_aplicativo, data_processamento, status_http,
  id_dps)`; `RetornoEventoNfse(...)`; `campo(dados, *nomes)` que aceita PascalCase/camelCase.
- `interpretar_emissao(resposta: RespostaHttp) -> RetornoEmissao | list[ErroNfse]`: 201 →
  descomprime `nfseXmlGZipB64`; corpo com `erros`/`erro`/`Erros` → lista de `ErroNfse`; JSON
  inválido → `RespostaInvalidaErro` com os 500 primeiros bytes.

3.4 `texto.py`: `sanitizar(texto, maximo)` — normaliza NFC, troca aspas curvas/travessão/meia-risca
por ASCII, remove caracteres de controle, colapsa espaços, trunca. Aplicado a todo `TSString`
do montador (relatório: aspas curvas de editores causam falha de schema).

Testes: tabela de Ids (CNPJ, CPF, alfanumérico), chave decomposta, Base64 simples e duplo, resposta
em PascalCase e camelCase, alertas vs erros, sanitização.

### Etapa 4 — Modelo tipado da DPS

Arquivo: `nfse/modelo.py`, `tests/test_modelo_nfse.py`.

Dataclasses `frozen=True`, validação em `__post_init__`/`validar()` com `ValidacaoErro`
(mesmo padrão de `DadosManifestacao.validar` em `manifestacao/evento.py:55`). Subconjunto para
**prestadores de serviço em geral**; o que não couber entra pelo caminho "XML pronto".

| Classe | Campos (XML) | Regras locais |
|---|---|---|
| `Prestador` | `cnpj`/`cpf` (um), `inscricao_municipal` (`IM`), `nome` (`xNome`, só quando não emitente), `regime_simples` (`opSimpNac` 1/2/3), `regime_especial` (`regEspTrib`), `telefone`, `email` | Quando o prestador é o emitente, **sem endereço** (E0128). CNPJ/CPF validados (`validar_cnpj`/`validar_cpf`) |
| `EnderecoNacional` / `EnderecoExterior` | `endNac`: `cMun`(IBGE 7), `CEP`; `xLgr`, `nro`, `xCpl`, `xBairro` / `endExt`: `cPais`, `cEndPost`, `xCidade`, `xEstProvReg` | IBGE via `validar_codigo_ibge` |
| `Tomador`, `Intermediario` | `cnpj`/`cpf`/`nif`, `nome`, `inscricao_municipal`, `endereco`, `telefone`, `email` | DV válido (E0188) |
| `Servico` | `cTribNac` (6), `cTribMun`, `cNBS`, `xDescServ`, `cLocPrestacao` (IBGE), `cPaisPrestacao`, `cIntContrib` | `cTribMun != "000"` (E0315); NBS obrigatório se houver `IbsCbs` (E0322) |
| `Valores` | `vServ`, `vDescIncond`, `vDescCond`, `vDedRed` (NT 004) | Decimal, 2 casas |
| `TribMunicipal` | `tribISSQN` (1..4), `tpRetISSQN` (1..3), `pAliq`, `nBM`, exigibilidade suspensa (`tpSusp`, `nProcesso`) | |
| `TribFederal` | PIS/COFINS (`CST`, `vBCPisCofins`, `pAliqPis`, `pAliqCofins`, `vPis`, `vCofins`, `tpRetPisCofins`), `vRetCP`, `vRetIRRF`, `vRetCSLL` | |
| `TotTrib` | `xs:choice`: `valores(fed, est, mun)` / `percentuais(fed, est, mun)` / `nao_informar` (`indTotTrib=0`) / `percentual_simples` (`pTotTribSN`) | Enum `OpcaoTotTrib`; o montador emite só o ramo escolhido (E0710/E0712/E0713 são regra de negócio do Anexo I, não do XSD) |
| `IbsCbs` | `cIndOp`, `CST`, `cClassTrib`, `ind_gIBSCBS`, `vAjusteBC` (NT 009), grupo `gIBSCBS` (BC, alíquotas e valores IBS UF/Mun e CBS), `comExt` (`nDUIMP`/`nDUE`) | **Hipótese**: nomes dos subelementos de `gIBSCBS` a confirmar no XSD de cada pacote; ordem difere entre leiautes |
| `Substituicao` | `subst`: `chSubstda`, `cMotivo`, `xMotivo` | chave NFS-e válida |
| `Dps` | `serie`, `numero` (`nDPS`, opcional → numeração local), `data_competencia` (`dCompet`), `data_emissao` (padrão agora, Brasília, sem microssegundos), `tipo_emitente` (`tpEmit` 1/2/3), `codigo_municipio_emissor` (`cLocEmi`), `prestador`, `tomador`, `intermediario`, `servico`, `valores`, `trib_municipal`, `trib_federal`, `tot_trib`, `ibs_cbs`, `substituicao`, `destinatario` (`indDest`/`dest`, NT 009), `informacoes_complementares` | `dhEmi` nunca futura (E0008); `tpAmb` **não** é campo do modelo: vem do ambiente da fachada (regra do `nfse-sefin`, evita E0006) |

Reuso: `FUSO_BRASILIA` de `manifestacao/evento.py:18` (mover para `tipos.py` e re-exportar).

### Etapa 5 — Montador da DPS por leiaute + validação XSD opcional

Arquivos: `nfse/montador_dps.py`, `nfse/validador_xsd.py`, `tests/test_montador_dps.py`, `tests/fabrica_nfse.py`.

5.1 `MontadorDps` (base): `montar(dps: Dps, ambiente, id_dps) -> etree._Element` com raiz
`<DPS xmlns=NS versao=...>` > `<infDPS Id=...>`, usando lxml como `MontadorEvento`
(`manifestacao/evento.py:76-98`). Métodos por grupo na **ordem do XSD**: `_ide`, `_subst`, `_prest`,
`_toma`, `_interm`, `_serv`, `_valores`, `_ibscbs`, `_dest`. Omite elementos `None`; formata
`Decimal` com 2 casas (`vServ`) ou conforme o XSD (`pAliq` 4 casas, **hipótese** a confirmar).
`verAplic = "freedfe/<__version__>"`.

5.2 `MontadorDpsNt004(MontadorDps)`: `vDedRed`/`gReeRepRes` nos valores; `IBSCBS` com `gIBSCBS`
antes de `CST`/`cClassTrib`; sem `indDest` na raiz.
`MontadorDpsNt009(MontadorDps)`: `CST`/`cClassTrib` antes de `gIBSCBS`; `vAjusteBC` substitui
`vDedRed`/`gReeRepRes`; `ind_gIBSCBS`; `indDest`/`dest` em `infDPS`; `comExt`; `verCalcIBSCBS`.
`montador_para(leiaute)` devolve a instância.

5.3 `ValidadorXsd(pasta_xsd)`: carrega `DPS_v1.01.xsd` (ou o nome real do pacote) com
`etree.XMLSchema`; `validar(elemento)` levanta `ValidacaoErro` com as mensagens de linha. Opcional:
só ativa se `ConfiguracaoNfse.pasta_xsd[leiaute]` for informada. Os XSD **não** são embutidos.

5.4 `tests/fabrica_nfse.py`: `dps_minima(leiaute)`, `nfse_xml(chave, ...)`, `evento_nfse_xml(...)`,
`resposta_emissao_json(...)`, `resposta_erro_json(...)`, `resposta_adn_json(...)`, `gerar_chave_nfse()`.

Testes: ordem dos elementos por leiaute (comparação por lista de nomes locais); `Id` == `id_dps()`;
`dhEmi` sem microssegundos e com `-03:00`; `tpAmb` igual ao ambiente; campos sanitizados; ramo único
de `totTrib`; **teste de schema real** parametrizado por `FREEDFE_XSD_NFSE_NT004` /
`FREEDFE_XSD_NFSE_NT009` (`pytest.mark.skipif` quando a variável não existe) validando a DPS
mínima e a completa contra o XSD oficial — esta é a prova de que a ordem está certa.

### Etapa 6 — Leitura da NFS-e e dos eventos no repositório

Arquivos: `documentos/extratores.py`, `documentos/leitor.py`, `documentos/resumo.py`,
`repositorio/base.py`, `fachada.py`, `exportacao/base.py` (docstrings).

6.1 `ExtratorNFSe` (raiz `NFSe`, ns sped): `chave` = `infNFSe/@Id[3:]` (53 → 50); `numero` =
`nNFSe`; `serie` = `DPS/infDPS/serie`; `data_emissao` = `infDPS/dhEmi` (fallback `dhProc`);
`emitente_*` = `emit` (prestador); `destinatario_*` = `toma`; `valor_total` = `valores/vServ`
(**hipótese** de caminho; `vLiq` fica como alternativa documentada); `protocolo` = `None`;
`demais_participantes` = intermediário; `tipo_xml="NFSe"`; `xml_completo=True`.

6.2 `ExtratorEventoNFSe`: lê `chNFSe`; `tipo_evento` do elemento `e\d{6}` ou de `tpEvento`
(**hipótese** sobre o leiaute do XML de evento devolvido pela Sefin/ADN; confirmar no Anexo II);
`descricao` de `xDesc`; `data_evento` de `dhEvento`/`dhProc`.

6.3 `LeitorDocumentoFiscal.extratores_padrao()`: `"NFSe": ExtratorNFSe()`, raízes de evento NFS-e
(`"evento"` condicionada ao namespace sped — o leitor passa a considerar `(nome_local, namespace)`
para não colidir com `evento` da NF-e).

6.4 `resumo.py`: `EVENTOS_CANCELAMENTO[NFSE] = {101101, 105104, 305101}`;
`EVENTOS_SUBSTITUICAO = {NFSE: {105102}}`; `evento_substitui()`. `RepositorioDocumentos._aplicar_eventos`
(base.py:97) passa a devolver `SUBSTITUIDO` quando aplicável (cancelado tem precedência).

6.5 `fachada.py`: `_modelos()` padrão passa a ser `list(SERVICOS_DISTRIBUICAO)` (exclui NFSE);
`sincronizar(["nfse"])` delega a `self.nfse.sincronizar()`; `_validar_uf` ignora NFSE;
`gerar_pdfs` passa `situacao` ao gerador (ver Etapa 11). Docstrings "chave (44 posições)" → "44 ou 50".

Testes: `test_extratores.py` (NFS-e e evento sintéticos), `test_repositorio.py` (armazenar NFS-e e
evento 101101 → `CANCELADO`; 105102 → `SUBSTITUIDO`; `importar_xmls` de pasta com NFS-e),
`test_fachada.py` (`consultar(modelos=["nfse"])`, exportação CSV com chave de 50).

### Etapa 7 — Cliente Sefin, estado de emissão e `ServicoNfse.emitir` (núcleo)

Arquivos: `nfse/cliente_sefin.py`, `nfse/estado_emissao.py`, `nfse/servico.py`, `fachada.py`.

7.1 `ClienteSefin(cfg, ambiente, transporte: TransporteRest, auditoria)`:
- `emitir(dps_assinada: bytes) -> RespostaHttp` → `POST {base}/nfse` com `{"dpsXmlGZipB64": ...}`,
  `Content-Type: application/json`, timeout de emissão. **Sem retry.**
- `consultar_nfse(chave) -> bytes` (`GET /nfse/{chave}` → `nfseXmlGZipB64`).
- `dps_processada(id_dps) -> bool` (`HEAD /dps/{id}`: 200 → True, 404 → False; outro status →
  `RespostaInvalidaErro`). **Hipótese** dos códigos; parametrizável.
- `chave_da_dps(id_dps) -> str | None` (`GET /dps/{id}`).
- `registrar_evento(chave, pedido_assinado) -> RespostaHttp` (`POST /nfse/{chave}/eventos`, campo
  `pedidoRegistroEventoXmlGZipB64` — nome errado gera 500 mudo).
- `eventos(chave, tipo=None, seq=None)`.
- GET/HEAD: até 3 tentativas com backoff 2/4/8 s em `ComunicacaoErro`/5xx. Auditoria por chamada:
  `modo`, `id_dps`, `chave`, `status_http`, `codigos`, `duracao_ms`. **Nunca** registra XML em log.

7.2 `EstadoEmissao(arquivo, relogio)` (padrão de `EstadoNSU`, escrita atômica): registro por
`id_dps` com `serie`, `numero`, `situacao` (`PENDENTE`, `ENVIADA`, `AUTORIZADA`, `REJEITADA`,
`INCERTA`), `chave`, `erros`, `ts_*`, `caminho_dps`. Métodos: `reservar(id_dps, serie, numero)`,
`marcar(id_dps, situacao, **dados)`, `obter`, `listar(situacao=None)`, `proximo_numero(serie)`
(max + 1 entre os registros da série; o ERP continua sendo a fonte oficial da numeração).

7.3 `ServicoNfse.emitir(dps: Dps | bytes, *, leiaute=None) -> RetornoEmissao`, sob
`TravaArquivo(_estado/emissao_nfse.lock)`:
1. Leiaute: explícito > `VersaoLeiaute.selecionar(ambiente, dCompet)`.
2. Se `Dps`: numeração (`numero` ausente → `proximo_numero`), `id_dps()`, montador do leiaute,
   validação XSD se configurada. Se `bytes`: parse seguro (`resolve_entities=False`), exige raiz
   `DPS`/`infDPS@Id` válido, `tpAmb` igual ao ambiente, **não reassina** se já houver `Signature`.
3. Idempotência local: `AUTORIZADA` → devolve o retorno gravado; `ENVIADA`/`INCERTA` → reconcilia
   antes (passo 6) e só envia se a Sefin confirmar inexistência.
4. Assina (`PERFIL_NFSE`, alvo `infDPS`), grava `nfse/dps/<AAAA-MM>/<id>-dps.xml`, marca `ENVIADA`.
5. `POST /nfse`. 201 → descomprime, confere `infNFSe/@Id == "NFS"+chaveAcesso` (senão
   `RespostaInvalidaErro`), opcionalmente `verificar_assinatura` (log de aviso, não bloqueia),
   `repositorio.armazenar([DocumentoRecebido(xml)])`, grava resposta bruta, marca `AUTORIZADA`.
   4xx com `erros` → `REJEITADA` + `RejeicaoNfseErro` (**exceção**: `E0014` → tratar como "já
   emitida": reconcilia e devolve a NFS-e). `ComunicacaoErro`/5xx/timeout → `INCERTA`, reconcilia
   uma vez; se não existir, levanta `EmissaoIncertaErro`.
6. `reconciliar(id_dps)`: `HEAD` → `GET /dps/{id}` → `GET /nfse/{chave}` → armazena e marca
   `AUTORIZADA`; devolve `None` se a Sefin não conhece a DPS (marca `REJEITADA` com motivo
   `"nao_localizada"` só se o chamador pedir). `reconciliar_pendentes()` percorre `ENVIADA`/`INCERTA`.
7. `gerar_dps_assinada(dps, leiaute) -> bytes` (conferência sem enviar, análogo a
   `gerar_lote_assinado`), `consultar_chave(chave, armazenar=True)`, `emissoes(situacao=None)`.

7.4 `fachada.py`: `FreeDFe.__init__(..., config_nfse: ConfiguracaoNfseLocal | None = None,
transporte_rest: TransporteRest | None = None)`; `_obter_transporte_rest()` (lazy, igual a
`_obter_transporte`); propriedade `nfse` → `ServicoNfse(...)`.
`ConfiguracaoNfseLocal`: `leiaute` fixo ou `None` (auto), `pasta_xsd: dict[VersaoLeiaute, Path]`,
`prefixo_api`, `verificar_assinatura_retorno`, `pausa_entre_lotes_adn`.

Testes (`tests/test_cliente_sefin.py`, `tests/test_emissao_nfse.py`): emissão 201 feliz (NFS-e
armazenada, índice, estado `AUTORIZADA`, auditoria sem XML); rejeição com `erros` em camelCase e
PascalCase; `E0014` → reconciliação; timeout → `INCERTA` → `HEAD 200` → `AUTORIZADA` sem segundo
POST (asserção `len(transporte.enviados) == 1` para POST); `HEAD 404` → `EmissaoIncertaErro`; DPS em
bytes já assinada não é reassinada; `tpAmb` divergente → `ValidacaoErro` antes de qualquer chamada;
trava impede emissão concorrente; `Id` da NFS-e divergente da chave → `RespostaInvalidaErro`.

### Etapa 8 — Cancelamento (evento 101101)

Arquivos: `nfse/eventos.py`, `nfse/servico.py`.

- `TipoEventoNfse(Enum)`: `CANCELAMENTO = "101101"`, mais os códigos de leitura (105102, 105104,
  105105, 305101, confirmações 2022xx/2032xx/2042xx) com `cancela`/`substitui` como propriedades.
- `MontadorPedidoEvento(ambiente, leiaute, autor: Interessado)`: `cancelamento(chave,
  codigo_motivo, justificativa)` → `<pedRegEvento versao><infPedReg Id="PRE…">` com `tpAmb`,
  `verAplic`, `dhEvento` (Brasília), `CNPJAutor`/`CPFAutor`, `chNFSe`, `e101101` > `xDesc`
  ("Cancelamento de NFS-e"), `cMotivo`, `xMotivo` (**hipótese** dos nomes; confirmar no Anexo II /
  XSD `pedRegEvento`). Validações: chave NFS-e, `xMotivo` 15–255, nota não cancelada localmente
  (E0840) com aviso de que a regra municipal (prazo, análise fiscal) só a Sefin aplica.
- `ServicoNfse.cancelar(chave, codigo_motivo, justificativa) -> RetornoEventoNfse`: assina
  `infPedReg` (`PERFIL_NFSE`), `registrar_evento`, interpreta (evento devolvido em GZip+Base64),
  `repositorio.armazenar` do XML do evento → situação `CANCELADO` via `_aplicar_eventos`.
  `listar_eventos(chave)` consulta a Sefin e armazena o que faltar.

Testes: XML do pedido (Id de 59/62 por leiaute, assinatura verificável), envio com campo JSON
correto, situação local após o evento, rejeição E0824/E0840 mapeada em `RejeicaoNfseErro`.

### Etapa 9 — Parâmetros municipais com cache

Arquivos: `nfse/parametros.py`, `nfse/servico.py`.

- `ClienteParametros(cfg, transporte)`: `convenio(cod_mun)`, `servico(cod_mun, cod_serv)`,
  `contribuinte(cod_mun, cnpj_cpf)` — `GET {base_parametrizacao}/{codMun}/convenio` etc. (rota
  migrada para o host ADN; a rota legada `parametros_municipais` fica como `rota_legada=True`).
- `CacheParametros(pasta, relogio, ttl=24h)`: JSON por chave de consulta; `obter_ou_buscar`.
- `Convenio` (dataclass tolerante a nomes: `aderente_ambiente_nacional`, `dataAtivacao`…) com
  `aderente: bool`, `data_ativacao`; demais retornos como `dict` bruto.
- `ServicoNfse.convenio(cod_mun)`, `parametros_servico(...)`, `parametros_contribuinte(...)`,
  `verificar_pre_emissao(dps)` (opcional, chamado por `emitir(..., verificar_parametros=True)`):
  município sem convênio → `ValidacaoErro` antes do POST (evita E0037); competência anterior à
  ativação → aviso (E0016).

Testes: cache válido não chama a rede; expirado chama; `Convenio` com nomes alternativos;
`verificar_pre_emissao` bloqueia município não aderente.

### Etapa 10 — Sincronização ADN por NSU

Arquivos: `nfse/cliente_adn.py`, `nfse/servico.py`, `distribuicao/estado.py`.

- `EstadoNSU.__init__(..., bloqueio_segundos=BLOQUEIO_SEGUNDOS)` (NFS-e não tem a regra de 1 h da
  NT 2014.002; padrão da NFS-e: 15 min, configurável).
- `ClienteAdn(cfg, transporte, estado: EstadoNSU, repositorio, auditoria, interessado)`:
  `sincronizar(max_lotes)` → `GET {base_adn}/DFe/{NSU}?tipoNSU=DISTRIBUICAO&lote=true`; interpreta
  a lista de documentos (**hipótese** dos nomes `NSU`, `ArquivoXml`, `TipoDocumento`; parse
  tolerante por `campo()`), descomprime, `repositorio.armazenar(docs, sequencia=NFSE)`, atualiza
  `ult_nsu`; para em lote vazio; 429 → respeita `Retry-After` ou 60 s; backoff em 5xx.
  Parâmetro para CNPJ diferente do certificado (mesma raiz, matriz/filiais) quando a API expuser.
- `ServicoNfse.sincronizar(max_lotes=200) -> ResultadoSincronizacao` sob `TravaArquivo`
  (`_estado/nsu_nfse.lock`), reaproveitando `ResultadoSincronizacao` de `distribuicao/cliente.py`.
- Situação das notas emitidas é atualizada pelos eventos recebidos (cancelamento por ofício,
  análise fiscal) via `_aplicar_eventos` — sem lógica nova.

Testes: dois lotes e término em vazio; `ult_nsu` persistido; eventos recebidos alteram a situação;
429 com `Retry-After`; trava.

### Etapa 11 — DANFSe em PDF com QR Code (reportlab)

Arquivos: `pdf/danfse.py`, `pdf/gerador.py`, `fachada.py`, `pyproject.toml`.

- `pyproject.toml`: extra `nfse = ["reportlab>=4"]`; `todos` inclui `nfse`; `dev` idem.
- `GeradorPdf.gerar(xml, modelo, destino, situacao: SituacaoDocumento | None = None)` (kwarg
  opcional; implementações existentes ignoram). `fachada.gerar_pdfs` passa `resumo.situacao`.
- `GeradorPdfPadrao(GeradorPdf)`: roteia `NFSE` → `GeradorDanfse`, demais →
  `GeradorPdfBrazilFiscalReport`. Passa a ser o padrão da fachada. Import preguiçoso de `reportlab`
  com `DependenciaAusenteErro("pip install 'freedfe[nfse]'")`, como em `pdf/gerador.py:42-49`.
- `GeradorDanfse`: lê a NFS-e com `xml_utils` (`achar`/`texto`); A4 retrato, margens 10 mm;
  blocos: cabeçalho ("DANFSe – Documento Auxiliar da NFS-e", nº NFS-e, série/nº DPS, emissão,
  competência, chave formatada, ambiente de homologação em destaque), QR Code (`reportlab.graphics.
  barcode.qr`, **25 mm**, ≥ 1,52 cm exigidos) com `cfg.url_consulta_publica(chave)`, prestador,
  tomador, intermediário, serviço (cTribNac/cTribMun/NBS, descrição com quebra automática, local),
  tributação municipal (BC, alíquota, ISS, retenção), tributos federais e retenções, bloco IBS/CBS
  (só se existir no XML), destinatário (NT 009), valores (serviço, descontos, deduções/ajuste,
  líquido), informações complementares, rodapé com versão. Marca d'água diagonal **CANCELADA** /
  **SUBSTITUÍDA** por `situacao`. Fonte Helvetica (cobre Latin-1; fora disso, substituição
  documentada). Layout segue a NT 008 v1.02 (DANFSe v2.0) **na medida do que o relatório descreve**;
  campos exatos a conferir no anexo da NT.
- `ServicoNfse.gerar_danfse(chave, destino) -> Path` (lê do repositório; situação pelos eventos).

Testes: PDF começa com `%PDF`; geração com NFS-e mínima, completa (IBS/CBS) e cancelada; dependência
ausente → `DependenciaAusenteErro` (monkeypatch em `importlib`); `gerar_pdfs(modelos=["nfse"])` pela
fachada; `test_gerar_pdf_continua_apos_falha` continua passando (gerador composto).

### Etapa 12 — Fachada final, documentação, exemplos e smoke test

Arquivos: `__init__.py`, `README.md`, `examples/emitir_nfse.py`, `examples/nfse_smoke_producao_restrita.py`, `pyproject.toml`.

- `__init__.py`: exporta `Dps`, `Prestador`, `Tomador`, `Servico`, `Valores`, `TribMunicipal`,
  `TotTrib`, `IbsCbs`, `VersaoLeiaute`, `RetornoEmissao`, `RejeicaoNfseErro`, `EmissaoIncertaErro`,
  `TipoEventoNfse`. `__version__ = "0.2.0"`.
- README: seção "NFS-e Nacional" (fluxo, máquina de estados, idempotência, leiautes e datas,
  DANFSe local, limites conhecidos, hipóteses não confirmadas), tabela de métodos de `dfe.nfse`,
  estrutura de pastas, base normativa (NT 004 v2.0, NT 007, NT 008 v1.02, NT 009 v1.01, Manual
  v1.2, Manual ADN v1.0), extra `nfse`.
- `examples/emitir_nfse.py`: montar `Dps`, `emitir`, tratar `RejeicaoNfseErro` (código + caminho),
  `EmissaoIncertaErro` → `reconciliar`, `gerar_danfse`, `cancelar`.
- `examples/nfse_smoke_producao_restrita.py` (único que acessa a rede, fora do pytest): mTLS,
  sonda das duas rotas (`/SefinNacional` × `/API/SefinNacional`), convênio do município, DPS com
  defeito proposital detectado **após** a assinatura (ex.: `totTrib` incompatível → E0712) para
  validar o perfil de assinatura sem gerar nota; imprime códigos e `versaoAplicativo`.
- `pyproject.toml`: keywords `nfse`, `danfse`, `sefin`; description atualizada.

---

## Reuso explícito de código existente

| Necessidade | Reusar |
|---|---|
| Certificado, PEM temporário 0600, validade | `certificado.CertificadoA1` (certificado.py:48-131) |
| Contexto TLS mTLS | extrair de `transporte.ClienteSoap._obter_contexto` (transporte.py:66-75) |
| Assinatura XMLDSig | `manifestacao/assinatura.AssinadorXml` → parametrizar perfil |
| Montagem lxml, `_tag`/`_filho`, fuso Brasília, `dh` sem microssegundos | `manifestacao/evento.py:18,80-98,112-119` |
| Estado persistido atômico + relógio injetável | `distribuicao/estado.EstadoNSU`, `relogio_utc` |
| Trava de consumidor único | `distribuicao/trava.TravaArquivo` |
| Armazenar XML bruto + índice + eventos → situação | `repositorio/base.RepositorioDocumentos.armazenar/_aplicar_eventos`, `DocumentoRecebido` |
| GZip+Base64 | `distribuicao/retorno.InterpretadorRetorno.descompactar` (generalizar em `nfse/envelope.py`) |
| Leitura de XML sem namespace | `xml_utils.achar/texto/todos` |
| Auditoria JSONL | `auditoria.RegistroAuditoria` |
| Validação CNPJ/CPF/UF | `validadores.validar_cnpj/validar_cpf/validar_uf` |
| Resultado de sincronização | `distribuicao/cliente.ResultadoSincronizacao` |
| Dependência opcional | padrão de `pdf/gerador.py:42-49` |
| Fixtures de teste | `tests/conftest.py` (`gerar_pfx`, `TransporteFalso`, `RelogioFalso`), `tests/fabrica_xml.py` |

---

## Hipóteses a confirmar (registrar no README e nos docstrings)

1. Valor do atributo `versao` da DPS e nomes/ordem dos subelementos de `IBSCBS`/`gIBSCBS`,
   `comExt`, `dest` por leiaute → **fonte: XSD dos pacotes v1.01-20260209 e PRODREST-20261020**.
   O teste de schema (Etapa 5) é a verificação.
2. Nomes dos campos JSON de resposta (`chaveAcesso`, `nfseXmlGZipB64`, `erros[].codigo`) e do ADN
   (`NSU`, `ArquivoXml`) → parse tolerante + smoke test.
3. Status HTTP de `HEAD /dps/{id}` (200/404) → parametrizável.
4. Prefixo `/API/` na produção restrita → `prefixo_api` configurável + sonda no smoke test.
5. URL exata do QR Code da consulta pública e leiaute detalhado do DANFSe v2.0 → anexo da NT 008.
6. Raiz e campos do XML de evento devolvido (`evento`/`infEvento`, `e101101`) → Anexo II.
7. Algoritmo do DV da chave de 50 posições → validação estrutural até confirmar.
8. Caminho do valor total no `ResumoDocumento` (`vServ` × `vLiq`).
9. Regras municipais de cancelamento (prazo, análise fiscal) → só a Sefin aplica; a biblioteca
   expõe o código de rejeição.

---

## Verificação

1. Ambiente: `pip install -e ".[dev]"` (o container atual não tem `pytest` nem o pacote instalado).
2. A cada etapa: `pytest -q` e `ruff check src tests` verdes; nenhum teste acessa a rede.
3. Regressão: `tests/test_manifestacao.py` e `tests/test_fachada.py` inalterados em comportamento
   (perfil NF-e padrão, `sincronizar()` sem NFSE, gerador composto).
4. Schema: com os XSD oficiais baixados em pasta local, `FREEDFE_XSD_NFSE_NT004=... FREEDFE_XSD_NFSE_NT009=... pytest tests/test_montador_dps.py`
   deve validar a DPS mínima e a completa em ambos os leiautes.
5. Assinatura: `tests/test_assinatura.py` prova digest exclusivo com `xmlns` presente e verificação
   após re-parse; `examples/nfse_smoke_producao_restrita.py` com certificado real na produção
   restrita deve devolver uma rejeição **de negócio** (E07xx), nunca E0714/E0715/E1228/E1235.
6. Fluxo ponta a ponta (produção restrita, manual): `emitir` → NFS-e em `nfse/<AAAA-MM>/` e
   `emissoes_nfse.json` em `AUTORIZADA` → `gerar_danfse` abre com QR legível → `cancelar` →
   `consultar(modelos=["nfse"])` mostra `cancelado` → `sincronizar(["nfse"])` traz o evento pelo ADN.
7. Idempotência: simular timeout (transporte falso) e confirmar um único POST com reconciliação.
