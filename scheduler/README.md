# scheduler

Disparador externo de [`collect.yml`](../.github/workflows/collect.yml) via **Cloudflare Workflows**, em horário aleatório dentro da janela **`00:00–03:59 BRT`**.

## Por que existe

Nos seis primeiros dias de operação (`2026-11-14` a `2026-11-19`), o escalonador `schedule` do GitHub Actions (`08:17 UTC` / `05:17 BRT`) disparou sistematicamente entre `15:03` e `18:31 UTC` (`12h–15h BRT`), atrasos de 7h a 10h causados pela fila compartilhada da plataforma. Como `workflow_dispatch` não passa por essa fila, mover o disparo para fora resolve o atraso sem alterar o coletor.

## Funcionamento

1. Um único Cron Trigger em [`wrangler.toml`](wrangler.toml) acorda **uma vez por dia** às `03:00 UTC` (`00:00 BRT`), mantendo todos os disparos dentro do mesmo dia civil em BRT.
2. `scheduled()` sorteia um atraso uniforme entre `0` e `14.400` segundos (`0` a `4h`) via `crypto.getRandomValues` e cria uma instância de `CollectWorkflow` (`[[workflows]]`).
3. `CollectWorkflow` hiberna com `step.sleep()` até o segundo sorteado (sem limite de 15 minutos e sem consumir tempo de CPU) e executa `step.do()` chamando `POST .../dispatches` no `collect.yml`.
4. O `schedule` do `collect.yml` permanece ativo como *fallback*: como a coleta é idempotente por manifesto, qualquer disparo redundante após o dia fechado encerra sem efeito.

## Contrato de deploy e ambiente

* **Escopo de build:** raiz `scheduler/`, observando apenas `scheduler/*` (para que commits diários em `manifests/` não disparem rebuilds).
* **Variáveis públicas** ([`wrangler.toml`](wrangler.toml)): `GITHUB_REPO`, `WORKFLOW_FILE`, `WORKFLOW_REF`, `JITTER_MAX_SECONDS`.
* **Secret requerido no Worker:** `GITHUB_TOKEN` (PAT restrito ao repo com *Actions: Read and write*).
