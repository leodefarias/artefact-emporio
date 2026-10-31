# Mestre André: agente de atendimento da Empório da Música

Protótipo do desafio técnico de AI Engineer da Artefact. O Mestre André atende clientes de uma loja fictícia de instrumentos musicais em Campo Grande/MS: consulta catálogo e pedidos, aplica as políticas da loja e recusa o que foge do escopo.

**Arquitetura:** loop ReAct com function calling nativo, em que o RAG das políticas é uma das ferramentas. Resultado medido: 20 de 20 cenários aprovados em duas rodadas, cerca de 5 s por cenário.

## Como rodar

Requisitos: [uv](https://docs.astral.sh/uv/) e uma chave do [OpenRouter](https://openrouter.ai/keys) com créditos.

```bash
uv sync
cp .env.example .env        # depois edite e coloque sua chave em LLM_API_KEY
uv run --env-file .env python agent.py
```

| Comando | O que faz |
|---|---|
| `uv run --env-file .env python agent.py --trace` | Conversa mostrando pensamento, ação e observação do ReAct |
| `uv run --env-file .env pytest` | Testes. Sem chave, os de recuperação são pulados |
| `uv run --env-file .env python eval_agent.py` | Avaliação ponta a ponta com o LLM real |
| `uv run --env-file .env python rag.py ingerir` | Reconstrói o índice (também é criado sozinho na primeira consulta) |
| `uv run --env-file .env python rag.py perguntar "..."` | Mostra os trechos recuperados, sem LLM |

Exemplos de conversas reais, sem edição, em [`examples/`](examples/).

### Alternativa local (LM Studio)

Funciona com qualquer API compatível com OpenAI; basta usar a variante comentada no `.env.example`. Nesta máquina, foi preciso:

- Baixar o LLM e o `bge-m3` em GGUF.
- Carregar com `lms load <modelo> --context-length 8192 --parallel 1 --gpu off`. Sem `--parallel 1`, o contexto é dividido entre os slots.
- Selecionar o runtime só-CPU, porque o runtime CUDA estourava a memória da GTX 750 Ti, mesmo com `--gpu off`.
- Por instalar via flatpak, rodar `lms` com `HOME=~/.var/app/ai.lmstudio.lm-studio`.

## Arquitetura

```
Cliente → agent.py: loop ReAct (máx. 4 passos) → LLM com tools
                     ├─ buscar_produtos   → CSV; Python calcula preços
                     ├─ consultar_pedido  → CSV; Python verifica identidade
                     └─ buscar_politicas  → RAG sobre o manual em PDF
```

| Arquivo | Papel |
|---|---|
| `agent.py` | Persona, loop ReAct, CLI |
| `tools.py` | Ferramentas e seus schemas |
| `rag.py` | Ingestão e recuperação do manual |
| `eval_agent.py` | 20 cenários ponta a ponta |
| `test_tools.py`, `test_rag.py`, `test_agent.py` | 43 testes; `test_agent.py` usa um LLM falso |

## Decisões técnicas

### ReAct + function calling, sem framework

A cada passo o modelo decide entre chamar uma ferramenta ou responder; o resultado volta como observação. A escolha da ferramenta **é** a decisão "dados ou políticas" pedida no desafio.

- **Sem parse de texto `Thought/Action`:** tool calling nativo é mais robusto do que interpretar esse formato em texto livre.
- **Pensamento explícito:** cada ferramenta exige o argumento `pensamento`, com uma frase sobre o que falta e por que essa ferramenta. O loop o retira antes de executar e o mostra no `--trace`; o cliente nunca o vê. Pedir isso só no prompt não funcionou (0 pensamentos em 16 ações): o modelo chamava a ferramenta sem escrever texto.
- **Sem LangChain ou similar:** o loop tem poucas dezenas de linhas, e é fácil auditar e testar cada guarda.

### O que o LLM não decide

Modelos erram contas e podem ser manipulados. Por isso, o que tem consequência fica em Python:

- **Preços:** promoção ativa mostra preço original, percentual e preço promocional, sem PIX adicional (§6.2 do manual). Sem promoção, mostra tabela e PIX. A exceção é a divisão de parcelas, que o agente faz para conferir o mínimo do manual.
- **Identidade (LGPD):** `consultar_pedido` exige número e e-mail ou telefone, e devolve a mesma resposta para pedido inexistente e contato errado. Só o primeiro nome sai. Injeção de prompt não contorna isso, porque a verificação não passa pelo modelo.
- **Datas:** dias desde a compra e atraso são calculados, assim como se a loja está aberta e quando a equipe volta.
- **Guarda:** resposta com `R$` sem ferramenta no turno recebe uma nova tentativa (§7.1: nunca informar preço sem consultar o sistema).
- **Limites:** no máximo 4 passos por turno; depois disso, encaminha para a equipe.

### RAG: seguindo o notebook da disciplina

Porte da célula de 20/10 de `ia_e_machine_learning.ipynb`: pdfplumber, chunks de 1200 caracteres com 150 de sobreposição, vetores normalizados em numpy, top-4 por produto escalar, trechos numerados e citação da página. Sem banco vetorial, porque 12 chunks não precisam de um. As demais aulas do notebook (sklearn, redes neurais) não foram usadas, por não servirem ao problema.

Mudanças em relação ao notebook:

- **Bug corrigido:** `chunk_pages` descartava a palavra que estourava o limite, perdendo uma palavra a cada corte. Há teste para isso.
- Índice em cache, em vez de relido a cada pergunta.
- Cabeçalho e rodapé de página removidos antes do embedding.
- `task_type` (`RETRIEVAL_DOCUMENT`/`RETRIEVAL_QUERY`) deixou de ser usado: é a única diferença em relação ao embedding do notebook. O OpenRouter o ignora (vetor idêntico com e sem o parâmetro, cosseno 1,0), e usá-lo exigiria a API direta do Google. Todos os testes de recuperação passam sem ele.

`test_rag.py` valida a recuperação sem LLM. A pergunta "Promoção acumula com desconto do PIX?" cruza duas seções, então exige as duas páginas no top-4, em vez da correta em 1º.

### Embedding: `google/gemini-embedding-001`

É o modelo do notebook e o 1º colocado no [MTEB-BR](https://arxiv.org/html/2607.04581v2), benchmark nativo de português do Brasil (93 modelos, 22 tarefas). Indexar o manual inteiro (~4 mil tokens) custa menos de US$ 0,001.

| Modelo | MTEB-BR | US$/M tokens |
|---|---|---|
| `google/gemini-embedding-001` | 1º, 0,6820 | 0,15 |
| `qwen/qwen3-embedding-8b` | 2º, 0,6704 | 0,01 |
| `voyageai/voyage-4-large` | 9º, 0,6532 | 0,12 |
| `openai/text-embedding-3-large` | 16º, 0,645 | 0,13 |
| `google/gemini-embedding-2` | 28º, 0,633 | 0,20 |

Ressalvas: os autores consideram os 6 primeiros um grupo sem ordem estatisticamente resolvida, e os scores são média de várias tarefas, não só recuperação. Por isso, o critério final foi `test_rag.py`. As posições 16 e 28 vieram de um resumo do artigo, não da leitura direta da tabela. Preços: API do OpenRouter, em 31/10/2026.

### LLM: `anthropic/claude-haiku-5.5` via OpenRouter

A ideia inicial era um modelo local escolhido com `llmfit`. A medição nesta máquina (i5-4570, 4 núcleos, GTX 750 Ti de 2 GB) mudou o plano:

| | Qwen3.5 4B local (CPU) | Claude Haiku 5.5 (API) |
|---|---|---|
| Cenários aprovados | 12 de 18 | 20 de 20, em 2 rodadas |
| Tempo por cenário | 99 s (pico de 594 s) | ~5 s |
| Geração | ~3,2 tok/s (`llmfit` estimava 18,9) | ~150 tok/s, com prefill |

- **A comparação não é idêntica:** o Qwen rodou antes de ajustes no prompt e dos 2 últimos cenários. Mesmo assim, suas falhas eram responder sem chamar ferramenta, e a latência sozinha já inviabilizava o uso.
- **Candidatos locais incompletos:** Qwen3 4B só teve teste rápido; Llama 3.2 3B não terminou.
- **Sobre o `llmfit`:** os mais bem pontuados eram destilações da comunidade em formatos que não rodam aqui, e a velocidade estimada ficou cerca de 6 vezes acima da real.
- **Por que Haiku 5.5:** barato (US$ 0,10/M entrada, US$ 0,50/M saída), com tool calling. Não houve comparação com outros modelos de API: foi uma decisão direta, pois já atingia o objetivo. Trocar de modelo exige só `LLM_MODEL`.

### Estratégia de prompt

Persona e tom vêm do §7.1 do manual ("informal mas profissional", como um amigo que entende de música). O prompt descreve quando usar cada ferramenta e traz regras curtas, em especial as situações do §7.3. Data e hora entram no turno, não no prompt de sistema, para manter o prefixo estável.

O nome Mestre André ecoa a cantiga "Na loja do Mestre André".

### Interface e histórico

- **CLI:** o foco é o agente, e ela permite gerar as conversas de `examples/` por redirecionamento.
- **Histórico em memória, últimas 10 mensagens:** cobre follow-ups ("e no PIX?"). Sem persistência em disco, para não reter dados pessoais sem necessidade. Resultados de ferramentas não ficam no histórico, então preço e estoque são sempre consultados de novo.

## Suposições sobre os dados

| Situação | Decisão |
|---|---|
| WhatsApp aparece como 3341-4444 (capa e §1.2) e 3321-4500 (§7) | **3341-4444**, a mais frequente, corrigida na ingestão |
| E-mail aparece com e sem acento | Sem acento: `contato@emporiodamusica.com.br` |
| Parcelamento: tabela do §3 (mínimo R$ 100) contra faixas do §3.1 | **§3.1**, mais específico |
| Não há data de recebimento | `estimated_delivery` como aproximação |
| Não há canal de compra | Arrependimento tratado como compra online; a equipe confirma |
| Produto descontinuado ou "em breve" com estoque | Não conta como disponível; descontinuado não mostra preço |
| Duas promoções ativas para o mesmo produto | Vale a maior (não acumulam); não ocorre nos dados atuais |
| Manual cita "mais de 300 instrumentos"; catálogo tem 65, sem categorias 6 a 8 | Agente diz que não encontrou; não inventa |
| Pedidos de out/2025 a mar/2026 | Em 31/10/2026, quase todos fora dos prazos de 7 e 30 dias. O agente mostra o motivo e aponta garantia |

## Avaliação

`eval_agent.py` verifica ferramenta chamada, fatos obrigatórios e proibidos. Cobre os cenários do enunciado, esgotado, descontinuado, promoção expirada e ativa, pedido com e sem identificação, injeção de prompt, acessórios, fora de escopo e follow-up.

**Limite:** expressões regulares conferem fatos, não tom nem utilidade. A leitura manual das conversas encontrou defeitos que elas não pegavam (por exemplo, a guarda vazava para o cliente como "você tem razão"); os defeitos foram corrigidos e viraram testes. 20 de 20 não significa que o agente esteja pronto para produção.

## Limitações conhecidas e o que faria com mais tempo

- **Privacidade:** mensagens e dados do pedido (primeiro nome, itens, e-mail ou telefone digitado) passam pelo OpenRouter e pelo provedor do modelo. Em produção, exigiria avaliação de LGPD ou modelo em infraestrutura própria.
- **Identidade fraca:** número do pedido com e-mail é pouco para produção, e não há limite de tentativas. Adicionaria limite e código de confirmação.
- **Sem handoff real:** o agente orienta falar com a equipe, mas não registra reclamações nem transfere. Adicionaria uma ferramenta de registro e a integração com WhatsApp.
- **Parcelamento depende do modelo:** a conta de parcelas é a exceção à regra de não calcular. Passaria para uma ferramenta.
- **Feriados:** o aviso de loja fechada não os considera.
- **Avaliação:** ampliaria os cenários com conversas reais, com avaliação de tom por humanos ou LLM.
- **Busca de produtos:** é por substring. Com catálogo maior, usaria busca semântica. Reranking também ajudaria o RAG com um manual maior.
- **Índice:** não detecta alteração no PDF; rode `rag.py ingerir` ao atualizar o manual.
- **Repetição:** o aviso de retorno da equipe ainda reaparece além do necessário.

## Uso de assistentes de código

Usei o **Claude Code** (modelo Claude Opus 5.5) como par de programação em todo o projeto.

**Meu papel:** defini a arquitetura (ReAct, RAG, function calling), o RAG baseado no notebook e a persona. Decidi as trocas de rumo: LM Studio em vez de Ollama, sair do modelo local para OpenRouter e usar o Haiku 5.5. Aprovei os planos e fiz todos os commits, marco a marco.

**O papel do Claude Code:**

- Leu o enunciado, o manual, os dados e o notebook, e propôs o plano, que revisei antes de qualquer código.
- Escreveu a maior parte do código, dos testes e deste README.
- Rodou testes e avaliações, diagnosticou falhas (como o estouro do runtime CUDA) e pesquisou os benchmarks de embedding.
- Identificou as armadilhas dos dados e o bug de chunking do notebook.

**O que ajudou a manter a qualidade:** trabalhar em marcos com testes, medir antes de decidir e ler as respostas reais em vez de confiar só nos testes automatizados. Foi assim que apareceram erros do próprio assistente, como um prompt que fazia o agente recusar consultas com a loja fechada. As conversas de `examples/` são execuções reais, sem edição.
