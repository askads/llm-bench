"""Тесты агрегации и сборки отчёта (llmbench.report).

Раньше эта логика (производящая публикуемые числа) вообще не импортировалась в CI —
синтаксическая ошибка здесь проходила зелёной (REVIEW.md R37).
"""
from llmbench import report


def _rec(case, *, tool=5.0, numeric=5.0, has_golden=True, soft_q=5.0, soft_r=5.0,
         dimension="numeric", turn_type="single", cost=0.01, error=None,
         cost_wasted=0.0, retried=False):
    r = {"case": case, "dimension": dimension, "turn_type": turn_type,
         "tool": tool, "numeric": numeric, "has_golden": has_golden,
         "soft_quality": soft_q, "soft_russian": soft_r, "cost": cost,
         "cost_wasted": cost_wasted, "retried": retried, "error": error}
    r["composite"] = report.composite(r)
    return r


def test_composite_includes_lang_and_excludes_errors():
    # все четыре компоненты входят в среднее
    r = _rec("c", tool=5.0, numeric=3.0, soft_q=4.0, soft_r=2.0)
    assert r["composite"] == round((5 + 3 + 4 + 2) / 4, 3)
    # russian реально влияет (раньше не входил вовсе — REVIEW.md R5)
    hi = report.composite({**r, "soft_russian": 5.0})
    lo = report.composite({**r, "soft_russian": 1.0})
    assert hi != lo
    # упавший прогон → composite None
    assert report.composite({**r, "error": "APIError: 529"}) is None


def test_composite_variable_components():
    # нет golden-фактов → numeric не в составе
    r = _rec("c", has_golden=False, tool=4.0, soft_q=2.0, soft_r=2.0)
    assert r["composite"] == round((4 + 2 + 2) / 3, 3)
    # нет судей → только code-метрики
    r2 = _rec("c", soft_q=None, soft_r=None, tool=4.0, numeric=2.0)
    assert r2["composite"] == round((4 + 2) / 2, 3)


def test_errored_runs_excluded_but_counted():
    recs = [_rec("a"), _rec("a", error="APIError: 529"), _rec("b", tool=0.0, numeric=0.0, soft_q=0.0, soft_r=0.0)]
    a = report.agg(recs)
    assert a["errors"] == 1 and a["n_runs"] == 3
    # ошибочный прогон не тянет средние вниз: tool = mean(5.0, 0.0) без ошибочного
    assert a["tool"] == round((5.0 + 0.0) / 2, 3)
    # его метрики (None) не участвуют
    assert a["numeric"] == round((5.0 + 0.0) / 2, 3)


def test_stability_is_within_case_variance():
    # Вариант A: стабилен внутри кейсов, но кейсы разной сложности (лёгкий 5.0, трудный 3.0).
    # Старая формула (pooled σ по всем записям) штрафовала бы за это; новая — нет.
    stable = [_rec("easy", tool=5, numeric=5, soft_q=5, soft_r=5),
              _rec("easy", tool=5, numeric=5, soft_q=5, soft_r=5),
              _rec("hard", tool=3, numeric=3, soft_q=3, soft_r=3),
              _rec("hard", tool=3, numeric=3, soft_q=3, soft_r=3)]
    a_stable = report.agg(stable)
    assert a_stable["stddev_composite"] == 0.0  # 0 разброса между повторами каждого кейса

    # Вариант B: те же средние по кейсам, но пляшет между повторами.
    flaky = [_rec("easy", tool=5, numeric=5, soft_q=5, soft_r=5),
             _rec("easy", tool=1, numeric=1, soft_q=1, soft_r=1),
             _rec("hard", tool=3, numeric=3, soft_q=3, soft_r=3),
             _rec("hard", tool=3, numeric=3, soft_q=3, soft_r=3)]
    a_flaky = report.agg(flaky)
    assert a_flaky["stddev_composite"] > a_stable["stddev_composite"]


def test_score_per_dollar_and_zero_composite():
    # композит 0.0 при cost>0 должен давать 0.0 (число), а не None/«—» (REVIEW.md R30)
    recs = [_rec("a", tool=0, numeric=0, soft_q=0, soft_r=0, turn_type="single", cost=0.01)]
    a = report.agg(recs)
    assert a["score_per_dollar"]["single"] == 0.0


def test_pareto_ignores_none_and_zero_cost():
    aggs = {
        "cheap_good": {"composite": 4.0, "cost_avg": 0.01},
        "dear_good": {"composite": 4.0, "cost_avg": 0.05},
        "no_data": {"composite": None, "cost_avg": 0.01},
        "zero_cost": {"composite": 3.0, "cost_avg": 0.0},
    }
    front = report.pareto(aggs)
    assert "cheap_good" in front and "dear_good" not in front
    assert "no_data" not in front and "zero_cost" not in front


def test_cost_total_includes_wasted_and_errored():
    recs = [_rec("a", cost=0.02),
            _rec("a", cost=0.0, error="APIError: 529", cost_wasted=0.03, retried=True)]
    a = report.agg(recs)
    assert abs(a["cost_total"] - (0.02 + 0.03)) < 1e-9
    assert a["retried"] == 1


def test_build_md_renders_err_column_and_metadata():
    aggs = {"V": report.agg([_rec("a"), _rec("a", error="boom")])}
    meta = {"ts": "2026-07-03 12:00 UTC", "mode": "fixed", "repeat": 2, "n_cases": 1,
            "variants": [{"label": "V", "model": "claude-opus-4-8", "engine": "anthropic",
                          "vendor": "anthropic", "thinking": "adaptive", "effort": "high"}],
            "judges": ["Claude"], "neutral": [], "fixture_version": "2026-07-03",
            "git_commit": "abc1234", "jsonl": "results/runs-x.jsonl", "baseline_desc": None,
            "caveats": ["тест"]}
    md = report.build_md(aggs, meta)
    assert "Err" in md and "1/2" in md          # колонка ошибок
    assert "abc1234" in md                        # git-коммит в метаданных
    assert "runs-x.jsonl" in md                   # ссылка на сырые данные
    assert "average of the four" not in md.lower()  # старое ложное определение Score ушло
