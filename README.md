# Mestre André: customer service agent for Empório da Música

Prototype for Artefact's AI Engineer technical challenge. Mestre André serves customers of a fictional musical instrument store in Campo Grande, Brazil: he looks up the catalog and orders, applies store policies, and declines anything out of scope.

**Architecture:** a ReAct loop on native function calling, with RAG over the store policies as one of the tools. Measured result: 20 of 20 scenarios passed in two runs, at about 5 s per scenario.

The agent replies in Brazilian Portuguese because its customers do, and the store data, CLI messages, code identifiers, and example conversations are in Portuguese. Documentation, code comments, and the agent's instructions (system prompt and tool descriptions) are in English.

## How to run

Requirements: [uv](https://docs.astral.sh/uv/) and an [OpenRouter](https://openrouter.ai/keys) API key with credits.

```bash
uv sync
cp .env.example .env        # then edit it and put your key in LLM_API_KEY
uv run --env-file .env python agent.py
```

| Command | What it does |
|---|---|
| `uv run --env-file .env python agent.py --trace` | Chat showing the ReAct thought, action, and observation |
| `uv run --env-file .env pytest` | Tests. Without a key, retrieval tests are skipped |
| `uv run --env-file .env python eval_agent.py` | End-to-end evaluation with the real LLM |
| `uv run --env-file .env python rag.py ingerir` | Rebuilds the index (also built automatically on first query) |
| `uv run --env-file .env python rag.py perguntar "..."` | Shows the retrieved excerpts, without the LLM |

Real, unedited conversations are in [`examples/`](examples/).

### Local alternative (LM Studio)

The agent works with any OpenAI-compatible API; use the commented variant in `.env.example`. On this machine, it took the following:

- Download the LLM and `bge-m3` as GGUF.
- Load with `lms load <model> --context-length 8192 --parallel 1 --gpu off`. Without `--parallel 1`, the context is split across slots.
- Select the CPU-only runtime, because the CUDA runtime ran out of memory on the GTX 750 Ti, even with `--gpu off`.
- Because it was installed through flatpak, run `lms` with `HOME=~/.var/app/ai.lmstudio.lm-studio`.

## Architecture

```
Customer → agent.py: ReAct loop (max. 4 steps) → LLM with tools
                      ├─ buscar_produtos   → CSV; Python computes prices
                      ├─ consultar_pedido  → CSV; Python verifies identity
                      └─ buscar_politicas  → RAG over the PDF manual
```

| Tool | Purpose |
|---|---|
| `buscar_produtos` | Search products: price, stock, availability, active promotions |
| `consultar_pedido` | Look up an order: status, items, tracking |
| `buscar_politicas` | Search the store manual: hours, address, payment, returns, shipping, warranty |

| File | Role |
|---|---|
| `agent.py` | Persona, ReAct loop, CLI |
| `tools.py` | Tools and their schemas |
| `rag.py` | Manual ingestion and retrieval |
| `eval_agent.py` | 20 end-to-end scenarios |
| `test_tools.py`, `test_rag.py`, `test_agent.py` | 43 tests; `test_agent.py` uses a fake LLM |

## Technical decisions

### ReAct + function calling, no framework

At each step the model either calls a tool or answers; the result comes back as an observation. Choosing a tool **is** the "data or policies" decision the challenge asks for.

- **No parsing of `Thought/Action` text:** native tool calling is more robust than interpreting that format from free text.
- **Explicit thought:** every tool requires a `pensamento` ("thought") argument, one English sentence on what is missing and why this tool. The loop removes it before executing and shows it in `--trace`; the customer never sees it. Asking for this in the prompt alone did not work (0 thoughts in 16 actions): the model called tools without writing any text.
- **No LangChain or similar:** the loop is a few dozen lines, and each guard is easy to audit and test.

### What the LLM does not decide

Models get math wrong and can be manipulated, so whatever has consequences lives in Python:

- **Prices:** an active promotion shows the original price, percentage, and promotional price, with no additional PIX discount (manual §6.2). Without a promotion, it shows the list and PIX prices. The one exception is dividing installments, which the agent does to check the manual's minimum.
- **Identity (LGPD, Brazil's data protection law):** `consultar_pedido` requires the order number and an email or phone, and returns the same response for a nonexistent order and a wrong contact. Only the first name is returned. Prompt injection cannot bypass this, because the check does not go through the model.
- **Dates:** days since purchase and delays are computed, as are whether the store is open and when the staff returns.
- **Guard:** a reply containing `R$` with no tool call in that turn gets one retry (§7.1: never state a price without checking the system).
- **Limits:** at most 4 steps per turn; after that, the agent refers the customer to the staff.

### RAG: following the course notebook

A port of the 20/10 cell of `ia_e_machine_learning.ipynb`: pdfplumber, 1200-character chunks with 150 of overlap, normalized numpy vectors, top-4 by dot product, numbered excerpts, and page citations. There is no vector database, because 12 chunks do not need one. The notebook's other lessons (sklearn, neural networks) were not used, as they do not serve this problem.

Changes from the notebook:

- **Bug fixed:** `chunk_pages` dropped the word that overflowed the limit, losing one word at each cut. A test covers this.
- The index is cached instead of reloaded on every question.
- Page headers and footers are removed before embedding.
- `task_type` (`RETRIEVAL_DOCUMENT`/`RETRIEVAL_QUERY`) is no longer used; this is the only difference from the notebook's embedding. OpenRouter ignores it (identical vector with and without the parameter, cosine 1.0), and using it would require Google's API directly. All retrieval tests pass without it.

`test_rag.py` validates retrieval without the LLM. The question "Promoção acumula com desconto do PIX?" ("Does a promotion stack with the PIX discount?") spans two sections, so it requires both pages in the top 4 rather than the correct page first.

### Embedding: `google/gemini-embedding-001`

It is the notebook's model and ranks 1st on [MTEB-BR](https://arxiv.org/html/2607.04581v2), a native Brazilian Portuguese benchmark (93 models, 22 tasks). Indexing the whole manual (~4 thousand tokens) costs less than US$ 0.001.

| Model | MTEB-BR | US$/M tokens |
|---|---|---|
| `google/gemini-embedding-001` | 1st, 0.6820 | 0.15 |
| `qwen/qwen3-embedding-8b` | 2nd, 0.6704 | 0.01 |
| `voyageai/voyage-4-large` | 9th, 0.6532 | 0.12 |
| `openai/text-embedding-3-large` | 16th, 0.645 | 0.13 |
| `google/gemini-embedding-2` | 28th, 0.633 | 0.20 |

Caveats: the authors consider the top 6 a group with no statistically resolved order, and scores average several tasks, not just retrieval. So `test_rag.py` was the final criterion. Ranks 16 and 28 came from a summary of the paper, not a direct reading of the table. Prices: OpenRouter API, on 2026-10-31.

### LLM: `anthropic/claude-haiku-5.5` via OpenRouter

The initial idea was a local model chosen with `llmfit`. Measuring on this machine (i5-4570, 4 cores, 2 GB GTX 750 Ti) changed the plan:

| | Qwen3.5 4B local (CPU) | Claude Haiku 5.5 (API) |
|---|---|---|
| Scenarios passed | 12 of 18 | 20 of 20, in 2 runs |
| Time per scenario | 99 s (peak of 594 s) | ~5 s |
| Generation | ~3.2 tok/s (`llmfit` estimated 18.9) | ~150 tok/s, including prefill |

- **The comparison is not like for like:** Qwen ran before prompt adjustments and before the last 2 scenarios were added. Even so, its failures were answering without calling a tool, and latency alone ruled it out.
- **Incomplete local candidates:** Qwen3 4B only had a quick test; Llama 3.2 3B did not finish.
- **About `llmfit`:** its top-scored models were community distillations in formats that do not run here, and its speed estimate was about 6 times the real speed.
- **Why Haiku 5.5:** inexpensive (US$ 0.10/M input, US$ 0.50/M output), with tool calling. It was not compared with other API models: this was a direct decision, since it already met the goal. Switching models only takes `LLM_MODEL`.

### Prompt strategy

Persona and tone come from manual §7.1 ("informal but professional", like a friend who knows music). The prompt is in English with an explicit rule to reply in Brazilian Portuguese, and the policy-search tool asks for questions in Portuguese, the manual's language. It describes when to use each tool and has short rules, especially for the situations in §7.3. Date and time go in the turn rather than the system prompt, to keep the prefix stable.

The name Mestre André echoes the Brazilian children's song "Na loja do Mestre André" ("At Master André's shop").

### Interface and history

- **CLI:** the focus is the agent, and a CLI can generate the `examples/` conversations through redirection.
- **In-memory history, last 10 messages:** covers follow-ups ("e no PIX?", "and with PIX?"). There is no persistence to disk, to avoid retaining personal data unnecessarily. Tool results stay out of the history, so price and stock are always looked up again.

## Data assumptions

| Situation | Decision |
|---|---|
| WhatsApp appears as 3341-4444 (cover and §1.2) and 3321-4500 (§7) | **3341-4444**, the most frequent, corrected at ingestion |
| Email appears with and without an accent | Without: `contato@emporiodamusica.com.br` |
| Installments: §3 table (R$ 100 minimum) versus §3.1 tiers | **§3.1**, which is more specific |
| No delivery-received date | `estimated_delivery` as an approximation |
| No purchase channel | Withdrawal treated as an online purchase; staff confirms |
| Discontinued or "coming soon" product with stock | Does not count as available; discontinued shows no price |
| Two active promotions for the same product | The larger applies (they do not stack); does not occur in current data |
| Manual cites "more than 300 instruments"; catalog has 65, with no categories 6 to 8 | Agent says it did not find it; does not make things up |
| Orders from Oct 2025 to Mar 2026 | On 2026-10-31, almost all are past the 7- and 30-day windows. The agent gives the reason and points to the warranty |

## Evaluation

`eval_agent.py` checks the tool called and required and forbidden facts. It covers the challenge's scenarios, out-of-stock, discontinued, expired and active promotions, orders with and without identification, prompt injection, accessories, out-of-scope questions, and follow-ups.

**Limit:** regular expressions check facts, not tone or usefulness. Reading the conversations by hand found defects they missed (for example, the guard leaked to the customer as "você tem razão", "you're right"); these were fixed and became tests. 20 of 20 does not mean the agent is ready for production.

## Known limitations and what I would do with more time

- **Privacy:** messages and order data (first name, items, the email or phone typed) pass through OpenRouter and the model provider. Production would require an LGPD assessment or a model on our own infrastructure.
- **Weak identity check:** order number plus email is too little for production, and attempts are not limited. I would add a limit and a confirmation code.
- **No real handoff:** the agent tells customers to contact the staff, but does not log complaints or transfer conversations. I would add a logging tool and WhatsApp integration.
- **Installments depend on the model:** this math is the exception to the no-calculation rule. I would move it to a tool.
- **Holidays:** the store-closed notice does not consider them.
- **Evaluation:** I would expand scenarios with real conversations, with tone assessed by people or an LLM.
- **Product search:** it uses substring matching. With a larger catalog, I would use semantic search. Reranking would also help RAG with a larger manual.
- **Index:** it does not detect changes to the PDF; run `rag.py ingerir` after updating the manual.
- **Repetition:** the staff-return notice still appears more than necessary.

## Use of coding assistants

I used **Claude Code** (Claude Opus 5.5 model) as a pair programmer throughout the project.

**My role:** I defined the architecture (ReAct, RAG, function calling), the notebook-based RAG, and the persona. I made the changes of direction: LM Studio instead of Ollama, moving from a local model to OpenRouter, and using Haiku 5.5. I approved the plans and made all commits, milestone by milestone.

**Claude Code's role:**

- Read the challenge, manual, data, and notebook, and proposed the plan, which I reviewed before any code.
- Wrote most of the code, the tests, and this README.
- Ran tests and evaluations, diagnosed failures (such as the CUDA runtime running out of memory), and researched the embedding benchmarks.
- Identified the data traps and the notebook's chunking bug.

**What helped maintain quality:** working in milestones with tests, measuring before deciding, and reading real answers instead of relying only on automated tests. That is how the assistant's own errors surfaced, such as a prompt that made the agent refuse lookups while the store was closed. The `examples/` conversations are real, unedited runs; only their headers are in English.
