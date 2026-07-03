# llm-bench

[🇷🇺 Русский](README.md) · **🇬🇧 English**

A standalone harness to **run models against askads' MCP tools** (Yandex Direct / VK Ads /
Metrica) and **benchmark them against each other** on our domain (Russian ad analytics +
multi-step tool use) rather than on unrelated coding benchmarks.

Two modes:
- **`fixed`** — deterministic fixtures (a frozen fake ad account served as `tool_result`).
  Reproducible model comparison; runs in CI without network or account tokens.
- **`live`** — spawns REAL MCP servers over stdio (`mcp-yandex-direct`, etc.) with tokens
  from env. Integration tests of the tools themselves against a live account.

The engine and MCP client are **decoupled from askads** (extracted into `llmbench/`), so the
repository is self-contained.

> Full review of the harness and the list of fixed issues — see [`REVIEW.md`](REVIEW.md).

## What is scored, and by whom

| Dimension | By | Where |
|---|---|---|
| **Tool-Use** (right tools succeed / order / call cap) | code | `scoring.score_tooluse` |
| **Numeric-Accuracy** (number correctness + entity anchoring, "don't invent CPA") | code | `scoring.score_numeric` |
| Interpretation / Russian / Edge handling | judge panel | `judges.py` |

Key metrics are the backbone of the comparison; judges are strictly secondary.

## Layout

```
llmbench/
  core.py       # allowlists, tool-schema converters, truncation, rates, system prompt, retry, timeouts
  fixtures.py   # frozen account + FIXTURE_VERSION (single source of golden facts)
  mcp.py        # live stdio client + server registry + fake session + preflight_live
  engines.py    # agentic loops: run_anthropic (Claude/GLM), run_openai (GPT); retries, timeouts
  scoring.py    # numeric (entity anchoring) + toolcheck (is_error aware) + cost
  judges.py     # panel {Claude, GPT, GLM, (opt.) Gemini}, neutrality, determinism
  cases.py      # cases (question + trace spec + golden_facts with entity + rubric)
  report.py     # record aggregation, Stability/Score/Pareto, markdown build (CI-tested)
  runner.py     # variant grid x cases x repeat, JSONL persistence, report
tests/          # offline self-test: scoring + aggregation + runner pipeline (all in CI)
results/        # curated reports (.ru/.en.md) + generated report and runs-*.jsonl (gitignored)
```

## Running

Offline self-test (no network/money; CI):
```bash
pip install -r requirements-dev.txt
pytest -q
```

Deterministic model benchmark (needs model keys):
```bash
RUN_BENCH=1 ANTHROPIC_API_KEY=… ZAI_API_KEY=… OPENAI_API_KEY=… \
  python -m llmbench.runner --mode fixed --repeat 2
```
Optional `GOOGLE_API_KEY` adds a Gemini judge (neutral when GPT is a candidate).

Against REAL tools (needs the npm servers + account tokens):
```bash
npm install                       # installs mcp-yandex-direct, etc.
RUN_BENCH=1 ANTHROPIC_API_KEY=… YANDEX_DIRECT_TOKEN=… \
  python -m llmbench.runner --mode live --variants "GLM-4.6 disabled" --judges off
```
Account-token env: `YANDEX_DIRECT_TOKEN` (+ optional `YANDEX_DIRECT_LOGIN`),
`YANDEX_METRIKA_TOKEN`, `VK_ADS_TOKEN`. Override a server path with
`MCP_PATH_YANDEX_DIRECT=/path/to/dist/index.js`. Live mode preflights (tokens + server
presence) BEFORE the first paid call.

Flags: `--variants`, `--cases` (a typo in the filter is an error, not a silent full grid),
`--repeat`, `--judges panel|neutral|off`, `--concurrency N` (parallel runs within a variant),
`--dry-run` (shows the estimate without keys), `--out`, `--report-from <jsonl>`. The variant
list (model x thinking/effort/reasoning) lives in `llmbench/runner.py`; adding a model is one
line (don't forget the rate in `core.MODEL_RATES`, or the runner warns).

## Artifacts and re-scoring

Every run writes **`results/runs-<ts>.jsonl`** — one record per run (answer, tool trace,
usage, all scores, errors). This is the source of truth: the report is rebuilt from it for
free, with no repeat model calls —
```bash
python -m llmbench.runner --report-from results/runs-20260703-120000.jsonl
```
The runner writes the generated report to `results/model-comparison-grid.generated.md`
(gitignored) so it never clobbers the hand-curated `model-comparison-grid.ru.md` / `…en.md`
(top-3, prose, bilingual) — those are edited by hand from the generated one.

## How Score is computed

A run's `Score` is the mean of the **available** components: Tools Use (always), Accuracy (if
the case has golden facts), Edge Cases and Lang quality (if judges ran). The set depends on
the case, so Score is comparable across variants (everyone runs the same cases) but is NOT
equal to the mean of the four report columns. Failed runs (API errors, token-limit
truncation) are excluded from metrics and shown in a separate `Err` column.
`Stability = 5 − mean spread of Score between repeats of the same case` (not across cases of
differing difficulty).

## Latest run results

`results/model-comparison-grid.ru.md` (+ English `…en.md`) — curated summary + Pareto
frontier. ⚠️ Their current numbers are from the 2026-06-29 run, BEFORE the scoring/fixture
fixes (see `REVIEW.md`); regenerate with a fresh run.

## Known limitations

- **Case ceiling:** top models max out at 5.0 on Tool/Numeric → "parity" here means "both ace
  THESE tasks". Harder cases are needed to truly separate quality.
- **Judges are secondary:** without a neutral vendor, the primary soft score is the panel mean
  (advisory, self-preference possible). The comparison rests on the key metrics.
- Model rates and cache multipliers are from price lists; verify against billing.
- `glm-5`/`gpt-5`: availability ≠ identity of the expected model — verify.
- `--repeat` is a coarse noise flag; "wrong number == missing" in numeric is a simplification.
- `fixed` mode does not catch robustness on messy API output (use `--mode live` for that).
- The `system prompt` in `core.py` is domain-specific (a Direct analyst); swap it for your case.
