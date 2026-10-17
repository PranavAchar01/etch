"""Router tests: no torch, no download. The model call is stubbed."""

from agent import task_router as tr

CFG = {
    "target_sizes": {
        "task_type": 3,
        "creativity_scope": 2,
        "reasoning": 2,
        "contextual_knowledge": 2,
        "number_of_few_shots": 2,
        "domain_knowledge": 2,
        "no_label_reason": 2,
        "constraint_ct": 2,
    },
    "task_type_map": {"0": "Code Generation", "1": "Open QA", "2": "Other"},
    "weights_map": {
        k: [0, 1]
        for k in (
            "creativity_scope",
            "reasoning",
            "contextual_knowledge",
            "number_of_few_shots",
            "domain_knowledge",
            "no_label_reason",
            "constraint_ct",
        )
    },
    "divisor_map": {
        k: 1
        for k in (
            "creativity_scope",
            "reasoning",
            "contextual_knowledge",
            "number_of_few_shots",
            "domain_knowledge",
            "no_label_reason",
            "constraint_ct",
        )
    },
}


def test_scores_follow_the_model_card_formula():
    probs = [[0.7, 0.25, 0.05]] + [[0.5, 0.5]] * 7
    out = tr.scores(probs, CFG)
    assert out["task_type_1"] == "Code Generation"
    assert out["task_type_2"] == "Open QA"
    assert out["task_type_prob"] == 0.7
    # every dimension 0.5 -> weights sum to 1.0 -> complexity 0.5
    assert out["prompt_complexity_score"] == 0.5


def test_low_second_task_is_na_and_tiny_few_shot_is_zero():
    probs = [[0.95, 0.04, 0.01]] + [[0.97, 0.03]] * 7
    out = tr.scores(probs, CFG)
    assert out["task_type_2"] == "NA"
    assert out["number_of_few_shots"] == 0.0


def test_decide_routes_recipe_simple_and_complex():
    lo, hi = {"prompt_complexity_score": 0.1}, {"prompt_complexity_score": 0.9}
    assert tr.decide(lo, True, "L", "S")["route"] == "recipe"
    assert tr.decide(lo, False, "L", "S") == {
        "route": "simple",
        "model": "S",
        "why": f"complexity 0.100 < {tr.THRESHOLD}",
    }
    assert tr.decide(hi, False, "L", "S")["model"] == "L"


def test_route_is_off_without_the_env(monkeypatch):
    monkeypatch.delenv("ETCH_CLASSIFIER", raising=False)
    assert tr.route("blink an LED", False, "L", "S") is None


def test_route_uses_the_classifier_when_enabled(monkeypatch):
    monkeypatch.setenv("ETCH_CLASSIFIER", "1")
    monkeypatch.setattr(
        tr,
        "classify",
        lambda ask: {
            "task_type_1": "Text Generation",
            "prompt_complexity_score": 0.05,
            "seconds": 0.1,
            "load_seconds": 3.0,
        },
    )
    r = tr.route("blink an LED", False, "L", "S")
    assert (
        r["route"] == "simple"
        and r["model"] == "S"
        and r["task_type"] == "Text Generation"
    )


def test_route_falls_back_when_the_model_cannot_load(monkeypatch):
    monkeypatch.setenv("ETCH_CLASSIFIER", "1")

    def boom(ask):
        raise ImportError("no torch")

    monkeypatch.setattr(tr, "classify", boom)
    r = tr.route("blink an LED", False, "L", "S")
    assert r["route"] == "off" and r["model"] is None


def test_precomputed_scores_skip_the_model(monkeypatch):
    import json

    monkeypatch.setenv("ETCH_CLASSIFIER", "1")
    monkeypatch.setenv(
        "ETCH_CLASSIFIED",
        json.dumps(
            {
                "task_type_1": "Code Generation",
                "prompt_complexity_score": 0.6,
                "seconds": 0.05,
                "load_seconds": 0.0,
            }
        ),
    )

    def boom(ask):
        raise AssertionError("must not load the model in the build process")

    monkeypatch.setattr(tr, "classify", boom)
    r = tr.route("a weather station", False, "L", "S")
    assert r["route"] == "complex" and r["model"] == "L"


def test_serve_answers_json_lines(monkeypatch):
    import io
    import json

    monkeypatch.setattr(tr, "_load", lambda: {"load_s": 1.5})
    monkeypatch.setattr(
        tr, "classify", lambda a: {"prompt_complexity_score": 0.2, "ask_len": len(a)}
    )
    out = io.StringIO()
    tr.serve(io.StringIO(json.dumps({"ask": "blink"}) + "\n"), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0] == {"ready": True, "load_seconds": 1.5}
    assert lines[1] == {"prompt_complexity_score": 0.2, "ask_len": 5}
