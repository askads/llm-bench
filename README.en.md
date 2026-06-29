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

## What is scored, and by whom

| Dimension | By | Where |
|---|---|---|
| **Tool-Use** (right tools / order / call cap) | code | `scoring.score_tooluse` |
| **Numeric-Accuracy** (number correctness, "don't invent CPA") | code | `scoring.score_numeric` |
| Interpretation / Russian / Edge handling | judge panel | `judges.py` |

Key metrics are the backbone of the decision; judges are strictly secondary.

## Layout

```
llmbench/
  core.py       # allowlists, tool-schema converters, truncation, rates, system prompt, retry
  fixtures.py   # frozen account + FIXTURE_VERSION
  mcp.py        # live stdio client + server registry + fake session + open_session(mode)
  engines.py    # agentic loops: run_anthropic (Claude/GLM), run_openai (GPT)
  scoring.py    # numeric + toolcheck + cost + decision rule
  judges.py     # panel {Claude, GPT, GLM, (opt.) Gemini} + neutrality
  cases.py      # cases (question + trace spec + golden_facts + rubric)
  runner.py     # variant grid x cases x repeat, report, verdicts
tests/test_fixtures.py   # offline self-test (CI)
results/                 # report + raw per-run log of the latest run
```

## Running

Offline self-test (no network/money; CI):
```bash
pip install -r requirements-dev.txt
pytest -q tests/test_fixtures.py
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
`MCP_PATH_YANDEX_DIRECT=/path/to/dist/index.js`.

Flags: `--variants`, `--cases`, `--repeat`, `--judges panel|neutral|off`, `--dry-run`,
`--out`. The variant list (model x thinking/effort/reasoning) lives in `llmbench/runner.py`;
adding a model is one line.

## Decision rule

`scoring.DECISION_RULE` (numeric ≥ 4.5, tool ≥ 4.5, edge ≥ 4.0, score/$ ≥ baseline) is fixed
BEFORE the run; the runner prints PASS/FAIL per variant vs the baseline (current prod —
`Sonnet disabled/high`). Both axes (quality and score/$) are measured on the shippable config.

## Latest run results

`results/model-comparison-grid.md` — summary + Pareto frontier + verdicts;
`results/run-log.txt` — raw per-run log (aggregates are recomputable from it).

In short (fixed, repeat 2, 16 variants): **GLM-4.6 without thinking** — parity with Claude on
the key metrics (Numeric/Tool 5.0), the most stable non-Claude variant (σ 0.36), and **~11×
cheaper** than prod (score/$ 1630 vs 115). The only SWITCH on the Pareto frontier. Surprises:
thinking HURTS GLM-4.6 (Numeric 5.0→4.0); GLM-5 is worse than 4.6; GPT fails the key metrics
(gpt-4.1 omits CPA → Numeric 4.0; gpt-5 reaches for tools on a "change the bid" request).

## Known limitations

- **Case ceiling:** top models max out at 5.0 on Tool/Numeric → "parity" here means "both ace
  THESE tasks". Harder cases are needed to truly separate quality.
- **Judges are secondary:** without a neutral vendor, the primary soft score is the panel mean
  (advisory, self-preference possible). The decision rests on the key metrics.
- gpt/gemini/glm rates and cache multipliers are estimates; verify against billing.
- `glm-5`/`gpt-5`: availability ≠ identity of the expected model — verify.
- `--repeat` is a coarse noise flag, not a comparison count; "wrong number == missing" in
  numeric is a simplification.
- `fixed` mode does not catch robustness on messy API output (use `--mode live` for that).
- The `system prompt` in `core.py` is domain-specific (a Direct analyst); swap it for your case.
