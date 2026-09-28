# freedfe

Biblioteca Python open source (Apache-2.0) para consultar, baixar o XML e gerar o PDF dos
documentos fiscais eletrônicos (NF-e, CT-e e MDF-e) de interesse de um CNPJ ou CPF, usando os
Web Services oficiais de **Distribuição DF-e** da SEFAZ / Ambiente Nacional.

- Consulta por período, modelo e direção (emitidos/recebidos), com retorno em lista de objetos,
  CSV, JSON, XML ou XLSX.
- Download dos XMLs completos.
- Geração de DANFE, DACTE e DAMDFE em PDF.
- Manifestação do Destinatário (Ciência, Confirmação, Desconhecimento, Operação não Realizada).
- Controle de NSU e de consumo indevido (rejeição 656/678) conforme as Notas Técnicas.

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

Extras: `pdf` (BrazilFiscalReport, LGPL-3.0), `xlsx` (openpyxl, MIT), `todos`, `dev`.
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
| `documentos` | Leitura dos XMLs e extração dos dados (NF-e, CT-e, MDF-e, resumos, eventos) |
| `repositorio` | `RepositorioDocumentos` (contrato), `RepositorioArquivos`, filtro, importador |
| `exportacao` | Exportadores CSV/JSON/XML/XLSX e fábrica por formato |
| `pdf` | `GeradorPdf` (contrato) e implementação com BrazilFiscalReport |
| `servicos` | Endpoints, namespaces e versões dos leiautes (ponto único de atualização) |

`Transporte`, `RepositorioDocumentos` e `GeradorPdf` são classes abstratas: dá para plugar outro
transporte, um repositório em PostgreSQL ou outro gerador de PDF sem alterar o restante.

## Base normativa

- NF-e: NT 2014.002 v1.40 (schema `PL_NFeDistDFe_104`, `versao="1.01"`)
- NF-e, manifestação: NT 2020.001 v1.60 (prazo conclusivo de 90 dias)
- CT-e: NT 2015.002 v1.05 (`PL_CTeDistDFe_100`)
- MDF-e: NT 2015.002 v1.03 (`PL_MDFeDistDFe_100`)
- CNPJ alfanumérico: NT Conjunta 2025.001 (aceito na validação e nas chaves)

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
- Os XMLs contêm dados pessoais (LGPD): restrinja o acesso à pasta de dados.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

Os testes não acessam a rede: usam transporte falso, certificado autoassinado gerado na hora e
XMLs sintéticos.
