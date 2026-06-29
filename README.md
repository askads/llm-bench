# llm-bench

**🇷🇺 Русский** · [🇬🇧 English](README.en.md)

Самостоятельный харнесс, чтобы **гонять модели против MCP-тулз askads** (Яндекс Директ /
VK Ads / Метрика) и **бенчить их между собой** на нашем домене (русская рекламная аналитика
+ многошаговый tool-use), а не на чужих кодинг-бенчах.

Два режима:
- **`fixed`** — детерминированные фикстуры (замороженный фейк-кабинет как `tool_result`).
  Воспроизводимое сравнение моделей, гоняется в CI без сети/ключей кабинета.
- **`live`** — спавн РЕАЛЬНЫХ MCP-серверов по stdio (`mcp-yandex-direct` и т.д.) с токенами
  из env. Интеграционные тесты самих тулов на живом кабинете.

Движок и MCP-клиент **развязаны от askads** (вынесены в `llmbench/`), репозиторий автономен.

## Что и кто считает

| Измерение | Кто | Где |
|---|---|---|
| **Tool-Use** (нужные тулы/порядок/кап) | код | `scoring.score_tooluse` |
| **Numeric-Accuracy** (точность чисел, «не выдумывать CPA») | код | `scoring.score_numeric` |
| Интерпретация / Русский / Краевое | панель судей | `judges.py` |

Ключевые метрики — костяк решения; судьи строго вторичны.

## Структура

```
llmbench/
  core.py       # allowlists, конвертеры тулов, обрезка, ставки, system-prompt, retry
  fixtures.py   # замороженный кабинет + FIXTURE_VERSION
  mcp.py        # live stdio-клиент + реестр серверов + fake-сессия + open_session(mode)
  engines.py    # агентные loop'ы: run_anthropic (Claude/GLM), run_openai (GPT)
  scoring.py    # numeric + toolcheck + cost + decision rule
  judges.py     # панель {Claude, GPT, GLM, (опц.) Gemini} + нейтральность
  cases.py      # кейсы (вопрос + trace-спека + golden_facts + рубрика)
  runner.py     # сетка вариантов × кейсы × repeat, отчёт, вердикты
tests/test_fixtures.py   # офлайн self-test (CI)
results/                 # отчёт + сырой per-run лог последнего прогона
```

## Запуск

Офлайн self-test (без сети/денег; CI):
```bash
pip install -r requirements-dev.txt
pytest -q tests/test_fixtures.py
```

Детерминированный model-бенч (нужны ключи моделей):
```bash
RUN_BENCH=1 ANTHROPIC_API_KEY=… ZAI_API_KEY=… OPENAI_API_KEY=… \
  python -m llmbench.runner --mode fixed --repeat 2
```
Опц. `GOOGLE_API_KEY` — добавляет Gemini-судью (нейтрального, когда GPT — кандидат).

Против РЕАЛЬНЫХ тулов (нужны npm-серверы + токены кабинета):
```bash
npm install                       # ставит mcp-yandex-direct и др.
RUN_BENCH=1 ANTHROPIC_API_KEY=… YANDEX_DIRECT_TOKEN=… \
  python -m llmbench.runner --mode live --variants "GLM-4.6 disabled" --judges off
```
Env токенов кабинета: `YANDEX_DIRECT_TOKEN` (+ опц. `YANDEX_DIRECT_LOGIN`),
`YANDEX_METRIKA_TOKEN`, `VK_ADS_TOKEN`. Путь к серверу можно переопределить
`MCP_PATH_YANDEX_DIRECT=/path/to/dist/index.js`.

Флаги: `--variants`, `--cases`, `--repeat`, `--judges panel|neutral|off`, `--dry-run`,
`--out`. Список вариантов (модель × thinking/effort/reasoning) — в `llmbench/runner.py`;
добавить модель = одна строка.

## Правило решения

`scoring.DECISION_RULE` (numeric ≥ 4.5, tool ≥ 4.5, edge ≥ 4.0, score/$ ≥ baseline) фиксируется
ДО прогона; ранер печатает PASS/FAIL по каждому варианту vs baseline (текущий прод —
`Sonnet disabled/high`). Обе оси (качество и score/$) — на shippable-конфиге.

## Результаты последнего прогона

`results/model-comparison-grid.md` — сводка + Pareto-фронт + вердикты;
`results/run-log.txt` — сырой per-run лог (по нему пересчитываются агрегаты).

Кратко (fixed, repeat 2, 16 вариантов): **GLM-4.6 без thinking** — паритет с Claude по кодовым
метрикам (Numeric/Tool 5.0), самый стабильный из не-Claude (σ 0.36), и **~11× дешевле** прода
(score/$ 1630 против 115). Единственный SWITCH на Pareto-фронте. Сюрпризы: thinking ВРЕДИТ
GLM-4.6 (Numeric 5.0→4.0); GLM-5 хуже 4.6; GPT не проходит ключевые метрики (gpt-4.1 не выводит
CPA → Numeric 4.0; gpt-5 лезет в тулы на «измени ставку»).

## Известные ограничения

- **Потолок кейсов:** топ-модели упираются в 5.0 по Tool/Numeric → «паритет» здесь = «оба
  отлично решают ЭТИ задачи». Для строгого различения качества нужны более трудные кейсы.
- **Судьи вторичны:** без нейтрального вендора первичный мягкий балл = среднее панели
  (advisory, возможна self-preference). Вес решения — на ключевых метриках.
- Ставки/кэш-множители gpt/gemini/glm — оценки; сверить с биллингом.
- `glm-5`/`gpt-5`: доступность ≠ идентичность ожидаемой модели — сверить.
- `--repeat` — грубый флаг шума, не число для сравнения; «неверное число == пропущенное» в
  numeric — упрощение.
- `fixed`-режим не ловит robustness на грязном API-выводе (для этого `--mode live`).
- `system-prompt` в `core.py` — доменный (аналитик Директа); замени под свой кейс.
