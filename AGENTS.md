# AGENTS.md — ai-longitudinal-probe

Coletor diário do *Observatório Longitudinal de Modelos de IA em Produção* (Python `>=3.12`, gerenciado com `uv`).

## Mapa do repositório
- `questions.jsonl` — Bateria fixa (`v1.0.0`, 19 itens: 8 `F`, 4 `G`, 3 `C`, 4 `K`).
- `scripts/schema.py` — Contratos Pydantic (`Record`, `Manifest`, `Question`) e `load_questions`.
- `scripts/collect.py` — Coletor (`draw -> question -> model`, sem `temperature`, `MAX_TOKENS = 3000`).
- `scripts/validate.py` — Portão de qualidade pós-coleta (schema, cobertura, canários, cache).
- `scripts/summarize.py` — Sumarizador stdlib que gera `manifests/summary.json` para os badges do repositório.
- `scheduler/` — Cloudflare Worker + Workflow (`wrangler.toml` + `worker.js`) que dispara `collect.yml` com jitter na janela noturna (`00:00–03:59 BRT`).
- `.github/workflows/collect.yml` — Matriz paralela (`openrouter`, `gemini`, `maritaca`) idempotente por manifesto + upload no R2 + commit em `manifests/` via `github-actions[bot]`.
- `.github/workflows/ci.yml` — Lint (`ruff`) e testes (`pytest`) em Python `3.12`.

## Regras operacionais
- **Não quebrar a série histórica:** nunca altere `text` ou `answer_key` de `questions.jsonl` sem atualizar `instrument_version` (exceto antes do início efetivo da série) e o contrato em `tests/test_questions.py`.
- **Escopo estrito:** o coletor apenas coleta, valida estrutura e persiste — não adicione scoring, juiz ou pós-processamento de respostas aqui.
- **Segurança & artefatos:** nunca comite `.env`, chaves ou `data/*.jsonl`. Secrets no GitHub: `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `MARITACA_API_KEY`, `BUCKET_API_KEY`, `BUCKET_ACCOUNT_ID` e variável `BUCKET_NAME`.
- **Proveniência & transparência:** preserve respostas literalmente (inclusive erradas/vazias) e mantenha `AI_ASSISTANCE.md` atualizado se o papel de IA mudar.

## Comandos obrigatórios
```bash
uv sync                                              # instalar deps + dev
uv run --locked ruff check scripts tests             # lint
uv run --locked pytest -q                            # suíte de testes
```
