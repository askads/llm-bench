# Сетка моделей для askads — Claude / GLM / GPT (fixed-input)

_Сгенерировано 2026-06-29 20:20 UTC · FIXTURE_VERSION `2026-06-29` · repeat=2 · вариантов: 16 · кейсов: 9_

Claude/GLM — настоящий `run_chat` (мок на границе MCP); GPT — отдельный OpenAI-loop (НЕ наш движок). Tool-Use/Numeric — в коде; интерпретация/русский — панель судей. Судьи: ['Claude', 'GPT', 'GLM'] · нейтрален (вендор ∉ кандидатам): **—**.


## Сводка по вариантам (сорт. по composite)

| Вариант | engine | Numeric | Tool | Edge | Rus | $/прог | score/$ s | score/$ m | σ comp | composite |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| Opus disabled/high ⭐ | anthropic | 5.00 | 5.00 | 4.38 | 4.98 | $0.06200 | 79.40 | 75.20 | 0.37 | 4.85 |
| Opus adaptive/high | anthropic | 4.50 | 5.00 | 4.79 | 5.00 | $0.06600 | 74.50 | 73.00 | 0.40 | 4.85 |
| Sonnet adaptive/medium ⭐ | anthropic | 5.00 | 5.00 | 4.38 | 5.00 | $0.03900 | 127.50 | 99.50 | 0.39 | 4.84 |
| Sonnet adaptive/high | anthropic | 5.00 | 5.00 | 4.38 | 4.98 | $0.04400 | 112.70 | 95.60 | 0.40 | 4.84 |
| GLM-4.6 disabled ⭐ | anthropic | 5.00 | 5.00 | 4.38 | 4.89 | $0.00300 | 1605.30 | 1629.70 | 0.36 | 4.82 |
| Sonnet disabled/high | anthropic | 5.00 | 5.00 | 4.29 | 5.00 | $0.03400 | 145.90 | 115.20 | 0.44 | 4.81 |
| Sonnet adaptive/low | anthropic | 5.00 | 5.00 | 4.21 | 5.00 | $0.03500 | 141.20 | 120.80 | 0.49 | 4.81 |
| Sonnet disabled/low | anthropic | 5.00 | 5.00 | 4.25 | 4.96 | $0.03200 | 154.80 | 127.60 | 0.47 | 4.79 |
| GPT-4.1 | openai | 4.00 | 5.00 | 5.00 | 5.00 | $0.01200 | 434.40 | 349.20 | 0.52 | 4.79 |
| GLM-4.6 thinking | anthropic | 4.00 | 5.00 | 3.96 | 4.30 | $0.00400 | 1136.20 | 666.70 | 0.94 | 4.49 |
| Opus adaptive/max | anthropic | 5.00 | 4.44 | 4.78 | 5.00 | $0.11300 | 37.30 | 54.30 | 1.56 | 4.38 |
| GPT-5 reasoning low | openai | 4.50 | 4.39 | 5.00 | 5.00 | $0.01900 | 223.30 | 287.60 | 1.57 | 4.32 |
| GLM-5 disabled | anthropic | 4.00 | 4.44 | 4.28 | 5.00 | $0.00300 | 1330.00 | 1574.30 | 1.56 | 4.07 |
| GPT-5 reasoning medium | openai | 4.50 | 4.17 | 4.80 | 5.00 | $0.04100 | 91.20 | 163.00 | 1.84 | 4.03 |
| GLM-5 thinking | anthropic | 5.00 | 4.17 | 4.00 | 5.00 | $0.00500 | 777.00 | 1208.20 | 1.82 | 3.99 |
| GPT-5 reasoning high | openai | 4.00 | 4.17 | 5.00 | 5.00 | $0.06200 | 59.20 | 122.20 | 1.85 | 3.96 |

⭐ — Pareto-фронт (не доминируется по composite/цене): **GLM-4.6 disabled, Opus disabled/high, Sonnet adaptive/medium**


## Вердикт vs baseline (Sonnet disabled/high = текущий прод)


### Sonnet disabled/low → **SWITCH**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 5.00 | 4.50 | ✅ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 4.25 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 127.60 | 115.20 | ✅ |

### Sonnet adaptive/low → **SWITCH**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 5.00 | 4.50 | ✅ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 4.21 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 120.80 | 115.20 | ✅ |

### Sonnet adaptive/medium → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 5.00 | 4.50 | ✅ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 4.38 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 99.50 | 115.20 | ❌ |

### Sonnet adaptive/high → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 5.00 | 4.50 | ✅ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 4.38 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 95.60 | 115.20 | ❌ |

### Opus disabled/high → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 5.00 | 4.50 | ✅ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 4.38 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 75.20 | 115.20 | ❌ |

### Opus adaptive/high → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 4.50 | 4.50 | ✅ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 4.79 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 73.00 | 115.20 | ❌ |

### Opus adaptive/max → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 5.00 | 4.50 | ✅ |
| tool | 4.44 | 4.50 | ❌ |
| edge | 4.78 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 54.30 | 115.20 | ❌ |

### GLM-4.6 disabled → **SWITCH**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 5.00 | 4.50 | ✅ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 4.38 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 1629.70 | 115.20 | ✅ |

### GLM-4.6 thinking → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 4.00 | 4.50 | ❌ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 3.96 | 4.00 | ❌ |
| score_per_dollar(multi) ≥ baseline×ratio | 666.70 | 115.20 | ✅ |

### GLM-5 disabled → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 4.00 | 4.50 | ❌ |
| tool | 4.44 | 4.50 | ❌ |
| edge | 4.28 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 1574.30 | 115.20 | ✅ |

### GLM-5 thinking → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 5.00 | 4.50 | ✅ |
| tool | 4.17 | 4.50 | ❌ |
| edge | 4.00 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 1208.20 | 115.20 | ✅ |

### GPT-5 reasoning low → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 4.50 | 4.50 | ✅ |
| tool | 4.39 | 4.50 | ❌ |
| edge | 5.00 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 287.60 | 115.20 | ✅ |

### GPT-5 reasoning medium → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 4.50 | 4.50 | ✅ |
| tool | 4.17 | 4.50 | ❌ |
| edge | 4.80 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 163.00 | 115.20 | ✅ |

### GPT-5 reasoning high → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 4.00 | 4.50 | ❌ |
| tool | 4.17 | 4.50 | ❌ |
| edge | 5.00 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 122.20 | 115.20 | ✅ |

### GPT-4.1 → **STAY**

| Проверка | Значение | Порог | PASS |
|---|--:|--:|:--:|
| numeric | 4.00 | 4.50 | ❌ |
| tool | 5.00 | 4.50 | ✅ |
| edge | 5.00 | 4.00 | ✅ |
| score_per_dollar(multi) ≥ baseline×ratio | 349.20 | 115.20 | ✅ |

## Известные ограничения

- GPT прогнан отдельным OpenAI-loop (НЕ наш движок) — tool-use-фиделити не байт-в-байт с Claude/GLM.
- Нейтральных судей нет (все вендоры панели — кандидаты) → первичный мягкий балл = среднее панели (advisory, возможна self-preference; смотри per-judge/stddev). Вес решения — на кодовых метриках Tool-Use/Numeric.
- Кэш-множители и ставки gpt/gemini/glm — оценка; сверить с биллингом.
- glm-5 / gpt-5: curl-200/доступность ≠ идентичность ожидаемой модели — сверить.
- repeat — флаг шума, не сравнение; «неверное число == пропущенное» в numeric — упрощение.