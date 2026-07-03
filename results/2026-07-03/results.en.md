# Model comparison for AskAds (Claude / GLM / GPT)

[🇷🇺 Русский](results.ru.md) · 🇬🇧 English

_Run from 2026-07-03 12:39 UTC × **16 variants** (model × thinking/effort) × **9 test cases** × **3 repeats** = 432 runs · mode fixed · identical input for all (fixtures version `2026-07-03`) · code `f29dce9`._

**How it was measured.** Claude/GLM — our agentic engine; GPT — a separate OpenAI loop (askads is on Anthropic, GPT can't be plugged into the same engine) → its tool-use isn't 100% comparable. **Tools Use/Accuracy** are computed in code; **Edge Cases/Lang quality** — LLM judges (Claude, GPT, GLM; neutral: **—**). Judges are secondary — weight is on the key metrics.

## Terms (how to read the table)

- **Accuracy** (0–5) — numeric correctness: are CTR/CPC/CPA/spend computed right, nothing made
  up, and are the numbers attributed to the right campaign (entity anchoring). **In code**
  (deterministic).
- **Tools Use** (0–5) — tool correctness: called the right tools (successfully) in the right
  order, nothing extra/forbidden. **Code**.
- **Edge Cases** (0–5) — behavior in edge cases (empty report, refusing to change a bid,
  clarifying). **LLM judges** — they also score runs with tool violations.
- **Lang quality** (0–5) — naturalness and clarity of the Russian. Judges.
- **Score** (0–5) — a run's overall score = mean of the available components: Tools Use
  (always), Accuracy (if the case has golden facts), Edge Cases/Lang quality (if judges ran).
  The component set depends on the case, so Score is comparable across variants (everyone runs
  the same cases) but is NOT equal to the mean of the four left columns. Failed runs are
  excluded from Score — see Err.
- **Cost per Answer** — mean cost of a successful run (USD); **Score per USD (s/m)** — "quality
  per dollar" (Score ÷ cost) for single-/multi-step dialogs; higher = better value.
- **Stability** (0–5) — `5 − mean spread (σ) of Score between repeats of the same case`:
  higher = more stable. Meaningful at repeat ≥ 2.
- **Err** — `failed/all runs` (API errors, token-limit truncation); suffix `·NR` — N runs
  succeeded only after a retry with the same config. Failed runs are excluded from all metrics,
  but their cost is included in the total run cost.
- **Thinking** — whether the model thinks before answering: `adaptive` (Claude/GLM),
  `reasoning` (GPT-5), `no`.
- **Effort** — the "effort" budget per answer (`low/medium/high/max`); separate from thinking
  (weak effect when thinking is off). Not configurable for GLM (`—`).
- **⭐** — **best quality/price balance**: a variant that can't be beaten — no other is both
  better and cheaper. _(In optimization — the "Pareto frontier".)_

## Top-3 (best quality/price balance)

| LLM | Thinking | Effort | Accuracy | Tools<br>Use | Edge<br>Cases | Cost<br>per Answer | Score<br>per USD (m) | Stability | Score |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|
| GLM-4.6 | no | — | 4.67 | 4.63 | 4.11 | $0.00300 | 1657.30 | 4.66 | 4.62 |
| GPT-4.1 | no | — | 5.00 | 4.96 | 4.61 | $0.01300 | 378.20 | 4.94 | 4.93 |
| Opus 4.8 | adaptive | high | 5.00 | 5.00 | 4.78 | $0.08100 | 61.70 | 4.96 | 4.96 |

### Key takeaways
- **GLM-4.6 (no thinking)** — the price champion: $0.003 per answer (~27× cheaper than Opus, ~4× cheaper than GPT-4.1) at Score 4.62. Accuracy 4.67 / Tools Use 4.63 are just below top Claude, but Score per USD 1657 is in a league of its own.
- **GPT-4.1** — the surprise of this run: Accuracy 5.0, Tools Use 4.96, Edge 4.61, Score 4.93 at $0.013 — nearly top-Opus quality at ~6× lower cost. (Last run it was penalized "for a non-invented CPA"; a manual check confirms it computes correctly.)
- **Opus 4.8 (adaptive, high)** — the quality ceiling: Score 4.96, Tools Use 5.0, Edge 4.78, Stability 4.96 — but $0.081, ~27× pricier than GLM. Pick it when quality outweighs cost.

All three are the Pareto frontier (⭐): none can be both beaten on quality and undercut on price.

## All variants (sorted by Score)

| LLM | Thinking | Effort | Accuracy | Tools<br>Use | Edge<br>Cases | Lang<br>quality | Cost<br>per Answer | Score<br>per USD (s) | Score<br>per USD (m) | Stability | Err | Score |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| Opus 4.8 ⭐ | adaptive | high | 5.00 | 5.00 | 4.78 | 5.00 | $0.08100 | 61.20 | 61.70 | 4.96 | 0/27 | 4.96 |
| GPT-4.1 ⭐ | no | — | 5.00 | 4.96 | 4.61 | 5.00 | $0.01300 | 379.60 | 378.20 | 4.94 | 0/27 | 4.93 |
| Opus 4.8 | adaptive | max | 5.00 | 5.00 | 4.58 | 5.00 | $0.12000 | 40.30 | 49.50 | 4.97 | 0/27 | 4.93 |
| Opus 4.8 | no | high | 5.00 | 5.00 | 4.39 | 5.00 | $0.07900 | 61.90 | 62.50 | 4.97 | 0/27 | 4.90 |
| GPT-5 | reasoning | medium | 4.67 | 4.33 | 4.97 | 4.99 | $0.03900 | 121.00 | 134.30 | 4.90 | 0/27 | 4.73 |
| GPT-5 | reasoning | high | 5.00 | 4.11 | 4.45 | 5.00 | $0.06400 | 71.80 | 79.30 | 4.74 | 0/27 | 4.63 |
| GLM-4.6 ⭐ | no | — | 4.67 | 4.63 | 4.11 | 4.95 | $0.00300 | 1524.70 | 1657.30 | 4.66 | 0/27 | 4.62 |
| Sonnet 4.6 | adaptive | medium | 5.00 | 4.44 | 3.33 | 5.00 | $0.05300 | 86.80 | 79.40 | 4.98 | 0/27 | 4.57 |
| Sonnet 4.6 | no | low | 5.00 | 4.44 | 3.31 | 4.99 | $0.04500 | 102.40 | 98.00 | 4.97 | 0/27 | 4.56 |
| Sonnet 4.6 | adaptive | low | 5.00 | 4.44 | 3.28 | 5.00 | $0.04800 | 95.80 | 89.30 | 4.97 | 0/27 | 4.56 |
| Sonnet 4.6 | no | high | 5.00 | 4.44 | 3.28 | 4.99 | $0.04600 | 100.00 | 92.60 | 4.96 | 0/27 | 4.56 |
| Sonnet 4.6 | adaptive | high | 4.67 | 4.44 | 3.58 | 4.99 | $0.05600 | 81.90 | 76.50 | 4.89 | 0/27 | 4.56 |
| GPT-5 | reasoning | low | 4.00 | 4.26 | 4.53 | 5.00 | $0.01900 | 236.00 | 312.50 | 4.70 | 0/27 | 4.54 |
| GLM-5 | adaptive | — | 4.67 | 4.33 | 3.44 | 5.00 | $0.00600 | 880.80 | 833.30 | 4.59 | 0/27 | 4.47 |
| GLM-5 | no | — | 4.67 | 4.41 | 3.33 | 4.97 | $0.00500 | 875.40 | 1000.00 | 4.83 | 0/27 | 4.45 |
| GLM-4.6 | adaptive | — | 3.67 | 4.44 | 3.17 | 4.01 | $0.00500 | 757.00 | 607.10 | 4.18 | 0/27 | 3.84 |

⭐ — **best quality/price balance** (can't become both better and cheaper at once): **GLM-4.6 disabled, GPT-4.1, Opus adaptive/high**.

_For reference: current askads production — Sonnet 4.6 (thinking no, effort high)._

## Bottom line

Stricter scoring (Accuracy entity-anchoring + a 0–5 judge scale) finally separated the models — last time almost everyone hit the 5.0 ceiling. Real spread now shows on **Tools Use** and **Edge Cases**.

What matters and what surprised us:
- **Sonnet 4.6 drops on edge cases** (Edge ~3.3 vs 4.4–4.8 for Opus/GPT-4.1) — and it's NOT an artifact: on an empty slice it invents causes (start date, campaign status), and on an ambiguous question it dumps a full report and needlessly reaches for tools instead of asking to clarify. Manually verified (2 of 3 judges catch the rubric violation). And production today is exactly Sonnet without thinking — worth a closer look at its behavior on empty/ambiguous scenarios.
- **Thinking hurts GLM-4.6**: Score 4.62 → 3.84, Accuracy 4.67 → 3.67, Lang 4.95 → 4.01. Better run it without thinking — same as the previous run.
- **GPT-5 is unstable on tools** (Tools Use 4.1–4.3): reasoning mode reaches for tools more than needed. Best GPT-5 is medium (Score 4.73); high costs more and isn't better.
- **GLM-5 is no better than GLM-4.6** on these tasks (Score 4.45–4.47 vs 4.62).

Same caveat as before: these tasks are still easy for the top models (Accuracy/Tools Use near 5.0 for the leaders) — to tell them apart more strictly you need harder cases and more repeats.

## Known limitations

- **GPT was run via a separate wrapper** (askads is on Anthropic, GPT can't be plugged into its engine) — GPT's tool-use accuracy isn't perfectly comparable to Claude/GLM (a different tool-call format).
- **Prices and cache-read discounts** — from price lists; verify against real bills.
- **The model may have been substituted**: the API answering to `glm-5`/`gpt-5` doesn't guarantee that's the model under the hood.
- **No independent judge**: answers are scored by the same companies whose models are compared — possible self-model inflation; judge scores are auxiliary, weight is on the key metrics Tools Use/Accuracy (computed in code).
- Mode `fixed`: models see clean test data (fixtures), not the "messy" real API output (use `--mode live` for that).

_Raw per-run data: `results/2026-07-03/runs.jsonl` — the report is rebuilt from it with `python -m llmbench.runner --report-from <file>`._