# Model comparison for AskAds (Claude / GLM / GPT) — run from 2026-06-29 UTC

[🇷🇺 Русский](model-comparison-grid.ru.md) · 🇬🇧 English

**16 combinations** (model + settings) × **9 test cases** × **2 repeats** = 288 answers. All models received the same data — a dump of a test ad account.

All models answered the same questions about a Yandex Direct ad account and used the same set of tools. **Tools Use** and **Accuracy** are checked in code (identically and precisely for all); **Edge Cases** and **Lang quality** are scored by LLM judges.

## Legend

- **Accuracy** [0-5] — numeric correctness: are CTR / CPC / CPA / spend computed right, nothing made up.
- **Tools Use** [0-5] — did the model call the right tools correctly.
- **Edge Cases** [0-5] — behavior in tricky cases: no data, ambiguous question.
- **Lang quality** [0-5] — naturalness and clarity of the output text.
- **Score** [0-5] — overall row score (average of the four metrics above).
- **Cost per Answer** — average price of one answer.
- **Score per USD** — "quality per dollar" (Score ÷ price): `s` — for short answers, `m` — for multi-step dialogs; higher is better.
- **Stability** [0-5] — how stable answers are across repeats (computed as `5 − spread`): higher is more stable/predictable.
- **Thinking / Effort** — whether the model thinks before answering (`adaptive` / `reasoning` / `no`) and how much "effort" it spends (`low…max`; not configurable for GLM, `—`).
- **⭐** — best quality/price: no other model is both better and cheaper.

## Top-3 (best quality/price)

| LLM | Thinking | Effort | Accuracy | Tools<br>Use | Edge<br>Cases | Cost<br>per Answer | Score<br>per USD (m) | Stability | Score |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|
| GLM-4.6 | no | — | 5.00 | 5.00 | 4.38 | $0.00300 | 1629.70 | 4.64 | 4.82 |
| Sonnet 4.6 | adaptive | medium | 5.00 | 5.00 | 4.38 | $0.03900 | 99.50 | 4.61 | 4.84 |
| Opus 4.8 | no | high | 5.00 | 5.00 | 4.38 | $0.06200 | 75.20 | 4.63 | 4.85 |

### Key takeaways
- **GLM-4.6 (no thinking)** — best quality/price. Accuracy/Tools Use 5.0, same as Claude; the highest stability (Stability 4.64 — most stable non-Claude) and **many times cheaper** than the other leaders (Score per USD 1630 vs 75–100 for Claude).
- **Sonnet 4.6 (adaptive, medium)** — best among Claude: same quality, mid-range price, high stability.
- **Opus 4.8 (no thinking)** — highest Score (4.85), but ~20× more expensive than GLM for the same quality.

All three are best-balance (⭐): none can be beaten on both quality and price at once. The difference between them is almost entirely cost.

## All 16 variants (by Score)

_Bold — best value in each column._

| LLM | Thinking | Effort | Accuracy | Tools<br>Use | Edge<br>Cases | Lang<br>quality | Cost<br>per Answer | Score<br>per USD (s) | Score<br>per USD (m) | Stability | Score |
|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| Opus 4.8 ⭐ | no | high | **5.00** | **5.00** | 4.38 | 4.98 | $0.06200 | 79.40 | 75.20 | 4.63 | **4.85** |
| Opus 4.8 | adaptive | high | 4.50 | **5.00** | 4.79 | **5.00** | $0.06600 | 74.50 | 73.00 | 4.60 | **4.85** |
| Sonnet 4.6 ⭐ | adaptive | medium | **5.00** | **5.00** | 4.38 | **5.00** | $0.03900 | 127.50 | 99.50 | 4.61 | 4.84 |
| Sonnet 4.6 | adaptive | high | **5.00** | **5.00** | 4.38 | 4.98 | $0.04400 | 112.70 | 95.60 | 4.60 | 4.84 |
| GLM-4.6 ⭐ | no | — | **5.00** | **5.00** | 4.38 | 4.89 | **$0.00300** | **1605.30** | **1629.70** | **4.64** | 4.82 |
| Sonnet 4.6 | no | high | **5.00** | **5.00** | 4.29 | **5.00** | $0.03400 | 145.90 | 115.20 | 4.56 | 4.81 |
| Sonnet 4.6 | adaptive | low | **5.00** | **5.00** | 4.21 | **5.00** | $0.03500 | 141.20 | 120.80 | 4.51 | 4.81 |
| Sonnet 4.6 | no | low | **5.00** | **5.00** | 4.25 | 4.96 | $0.03200 | 154.80 | 127.60 | 4.53 | 4.79 |
| GPT-4.1 | no | — | 4.00 | **5.00** | **5.00** | **5.00** | $0.01200 | 434.40 | 349.20 | 4.48 | 4.79 |
| GLM-4.6 | adaptive | — | 4.00 | **5.00** | 3.96 | 4.30 | $0.00400 | 1136.20 | 666.70 | 4.06 | 4.49 |
| Opus 4.8 | adaptive | max | **5.00** | 4.44 | 4.78 | **5.00** | $0.11300 | 37.30 | 54.30 | 3.44 | 4.38 |
| GPT-5 | reasoning | low | 4.50 | 4.39 | **5.00** | **5.00** | $0.01900 | 223.30 | 287.60 | 3.43 | 4.32 |
| GLM-5 | no | — | 4.00 | 4.44 | 4.28 | **5.00** | **$0.00300** | 1330.00 | 1574.30 | 3.44 | 4.07 |
| GPT-5 | reasoning | medium | 4.50 | 4.17 | 4.80 | **5.00** | $0.04100 | 91.20 | 163.00 | 3.16 | 4.03 |
| GLM-5 | adaptive | — | **5.00** | 4.17 | 4.00 | **5.00** | $0.00500 | 777.00 | 1208.20 | 3.18 | 3.99 |
| GPT-5 | reasoning | high | 4.00 | 4.17 | **5.00** | **5.00** | $0.06200 | 59.20 | 122.20 | 3.15 | 3.96 |

⭐ — best quality/price: **GLM-4.6 (no thinking), Opus 4.8 (no thinking), Sonnet 4.6 (adaptive, medium)**.

For reference: currently in AskAds production — **Sonnet 4.6 (no thinking)**.

## Bottom line

Best quality/price — **GLM-4.6 without thinking**: on par with Claude on the precise metrics (Accuracy / Tools Use 5.0), the most stable non-Claude, and many times cheaper.

What surprised us: **thinking hurts GLM-4.6** (accuracy drops 5.0 → 4.0) — better run it without; **GLM-5 is worse than GLM-4.6** on our tasks; **GPT is weaker on the precise metrics** (GPT-4.1 doesn't compute CPA; GPT-5 reaches for tools on a "change the bid" request and is unstable). Opus gives the highest score but isn't justified by its price.

Important: our tasks are easy for the top models — almost all hit the 5.0 ceiling, so "on par" here means "both ace THESE particular tasks". To tell them apart more strictly, harder tasks and more repeats are needed.

## Caveats

- **GPT was tested via a separate wrapper** (AskAds is built on Anthropic, GPT can't be plugged into its engine) — so GPT's tool-use accuracy isn't perfectly comparable to Claude/GLM.
- **No independent judge.** Answers are scored by the participants themselves — the same companies (Claude judges Claude's answers too, and so on), so a judge may inflate its own model. The judge score is auxiliary; the comparison is decided by the precise metrics **Tools Use** and **Accuracy**, computed in code.
- **Prices are approximate.** The cost and cache-reuse discounts for GPT/Gemini/GLM are taken from price lists, not real bills — worth verifying.
- **The model may have been substituted.** The API answering to the name `glm-5` or `gpt-5` doesn't guarantee it's actually that model — worth checking.
- **Few repeats.** Each variant ran only 2 times — too few to trust Stability; more are needed for reliable numbers.
