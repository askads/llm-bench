# llm-bench

[🇷🇺 Русский](README.md) · **🇬🇧 English**

A standalone harness to **run models against askads' MCP tools** (Yandex Direct / VK Ads / Metrica) and **benchmark them against each other** on our domain (Russian ad analytics + multi-step tool use) rather than on unrelated coding benchmarks.

Two modes:
- **`fixed`** — deterministic fixtures (a frozen fake ad account served as `tool_result`). Reproducible model comparison; runs in CI without network or account tokens.
- **`live`** — spawns REAL MCP servers over stdio (`mcp-yandex-direct`, etc.) with tokens from env. Integration tests of the tools themselves against a live account.

The engine and MCP client are **decoupled from askads** (extracted into `llmbench/`), so the repository is self-contained.

## Terms

How to read the results table:

| Metric | What | Computed by |
|---|---|---|
| **Accuracy** | number correctness (CTR/CPC/CPA/spend) + right campaign attribution (entity anchoring), nothing invented | code |
| **Tools Use** | right tools called successfully, in the right order, nothing extra/forbidden | code |
| **Edge Cases** | behavior in edge cases (empty report, refusing a bid change, clarifying) | judges |
| **Lang quality** | naturalness and clarity of the Russian | judges |
| **Score** | a run's overall score = mean of the available components (see "How Score is computed") | — |
| **Cost per Answer** | mean cost of one answer, USD | — |
| **Score per USD** | "quality per dollar" (Score ÷ cost); higher = better value | — |
| **Stability** | `5 − spread of Score between repeats of a case`; higher = more stable | — |
| **⭐ (Pareto)** | best quality/price balance — a variant that can't be both beaten and undercut | — |

## Latest run results

**[`results/2026-07-03/`](results/2026-07-03/results.en.md)** (+ Russian `results.ru.md`) — a fresh full grid (16 variants × 9 cases × 3 repeats = 432 runs, 0 errors, ≈ $18) on the fixed scoring: Top-3 + Pareto frontier + takeaways. In short: **GLM-4.6 without thinking** is the best quality/price, **GPT-4.1** is surprisingly strong and cheap, **Opus 4.8 (adaptive/high)** is the quality ceiling; **Sonnet (production)** drops on edge cases. The `results/2026-06-29/` run is historical, before the scoring fixes.

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
results/        # per-date folders: results.ru.md + results.en.md + raw runs.jsonl (all in VCS for fixed)
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

Account-token env (for `--mode live`):

| Env | Purpose |
|---|---|
| `YANDEX_DIRECT_TOKEN` | Yandex Direct token (required for live) |
| `YANDEX_DIRECT_LOGIN` | optional — account login if the token is an agency token |
| `YANDEX_METRIKA_TOKEN` | Metrica token (for metrika cases) |
| `VK_ADS_TOKEN` | VK Ads token |
| `MCP_PATH_YANDEX_DIRECT` | optional — override the MCP server path (`/path/to/dist/index.js`) |

Live mode preflights (tokens + server presence) BEFORE the first paid call.

Runner flags:

| Flag | What it does |
|---|---|
| `--mode fixed\|live` | fixtures (default) or real MCP servers |
| `--variants <substrings…>` | which variants to run (a typo in the filter is an error, not a silent full grid) |
| `--cases <id…>` | which cases to run (same typo guard) |
| `--repeat N` | repeats per case (default 2) |
| `--judges panel\|neutral\|off` | judge panel / neutral only / no judges |
| `--concurrency N` | parallel runs within a variant (default 4) |
| `--dry-run` | show the estimate (run/judge counts) without keys or spend |
| `--out <dir>` | report directory (default `results/<date>/`) |
| `--report-from <jsonl>` | rebuild the report (ru+en) from `runs.jsonl` for free |
| `--resume <jsonl>` | catch up an interrupted run (only missing/failed keys) |

The variant list (model x thinking/effort/reasoning) lives in `llmbench/runner.py`; adding a model is one line (don't forget the rate in `core.MODEL_RATES`, or the runner warns).

## Artifacts and re-scoring

Every run lands in a dated folder **`results/<date>/`**: the raw `runs.jsonl` (one record per run — answer, tool trace, usage, all scores, errors) and the bilingual generated report `results.ru.md` + `results.en.md`. `runs.jsonl` is the source of truth: both report versions are rebuilt from it for free, with no repeat model calls —
```bash
python -m llmbench.runner --report-from results/2026-07-03/runs.jsonl
```
`runs.jsonl` is **committed for fixed runs** (fake account, no private data; ~230 KB packed). ⚠️ **Live runs contain real account data — don't commit their `runs.jsonl`** (keep local / in a private S3). The `.md` reports are in VCS. Top-3, prose and takeaways are added by hand on top of the generated grid in the same dated folder. A second run on the same day doesn't clobber the first (a time suffix is added on a date collision).

**Interrupted (out of credits, Ctrl-C)?** Completed runs are already in `runs.jsonl` (written line-by-line as they finish) — top up and catch up the rest, paying only for what's left:
```bash
RUN_BENCH=1 …keys… python -m llmbench.runner --resume results/2026-07-03/runs.jsonl
```
`--resume` skips already-successful `(variant, case, repeat)` keys, runs only the failed/missing ones, appends to the same file, and rebuilds the report (on duplicates, success wins). You can also add a key (e.g. `OPENAI_API_KEY`) and backfill variants skipped in the original run.

## How Score is computed

A run's `Score` is the **mean of the available components** (not all four columns at once):

- **Tools Use** — always;
- **Accuracy** — if the case has golden facts (numeric cases);
- **Edge Cases** and **Lang quality** — if judges ran.

The component set depends on the case, so:
- Score is **comparable** across variants (everyone runs the same cases) but is **not equal** to the mean of the four report columns;
- **failed runs** (API errors, token-limit truncation) are excluded from metrics — they show in a separate `Err` column;
- **`Stability = 5 − mean spread (σ) of Score between repeats of the same case`** — it captures model noise, not case difficulty.

## Known limitations

- **Case ceiling:** top models max out at 5.0 on Tool/Numeric → "parity" here means "both ace THESE tasks". Harder cases are needed to truly separate quality.
- **Judges are secondary:** without a neutral vendor, the primary soft score is the panel mean (advisory, self-preference possible). The comparison rests on the key metrics.
- Model rates and cache multipliers are from price lists; verify against billing.
- `glm-5`/`gpt-5`: availability ≠ identity of the expected model — verify.
- `--repeat` is a coarse noise flag; "wrong number == missing" in numeric is a simplification.
- `fixed` mode does not catch robustness on messy API output (use `--mode live` for that).
- The `system prompt` in `core.py` is domain-specific (a Direct analyst); swap it for your case.
