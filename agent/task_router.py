"""Optional prompt router in front of the picker (ETCH_CLASSIFIER=1).

Runs NVIDIA's open prompt-task-and-complexity-classifier (DeBERTa-v3 base, NVIDIA Open Model
License, https://huggingface.co/nvidia/prompt-task-and-complexity-classifier) on the CPU and
routes the first pick: a recipe match keeps the recipe path, a low-complexity ask goes to the
sub model (Nemotron 3.5 Lightning), anything else to the lead model (Nemotron 3 Super).
Repairs always stay on the lead model.

torch and transformers are optional. Without them, or without ETCH_CLASSIFIER=1, route()
returns None and Etch behaves exactly as before.

Load once per backend process: every build is its own `python -m agent.live` process, so the backend
(deploy/etch_public.py) keeps one long-lived classifier process (`python -m agent.task_router --serve`, JSON
lines on stdin/stdout), classifies the ask before it starts a build and hands the scores to the build in
ETCH_CLASSIFIED. A build without ETCH_CLASSIFIED (CLI, QA) classifies in its own process.

The weights are never downloaded from inside a build: `python -m agent.task_router --download` fetches them
(run it under the heavy lock); until then the router reports `route: off`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

REPO = "nvidia/prompt-task-and-complexity-classifier"
THRESHOLD = float(os.environ.get("ETCH_CLASSIFIER_THRESHOLD", "0.3"))
MAX_TOKENS = 512
FILES = ["config.json", "model.safetensors", "*.model", "*token*", "README.md"]

# DeBERTa-v3 base backbone shape. Built from this config so only the classifier repo is
# downloaded; the strict state-dict load below fails loudly if the shape ever differs.
_BACKBONE = dict(
    vocab_size=128100,
    hidden_size=768,
    num_hidden_layers=12,
    num_attention_heads=12,
    intermediate_size=3072,
    hidden_act="gelu",
    hidden_dropout_prob=0.1,
    attention_probs_dropout_prob=0.1,
    max_position_embeddings=512,
    type_vocab_size=0,
    initializer_range=0.02,
    layer_norm_eps=1e-7,
    relative_attention=True,
    max_relative_positions=-1,
    position_buckets=256,
    norm_rel_ebd="layer_norm",
    share_att_key=True,
    pos_att_type=["p2c", "c2p"],
    position_biased_input=False,
    pad_token_id=0,
)

_lock = threading.Lock()
_state: dict[str, Any] = {}


def enabled() -> bool:
    return os.environ.get("ETCH_CLASSIFIER", "") == "1"


def scores(probs: list[list[float]], cfg: dict[str, Any]) -> dict[str, Any]:
    """Model-card scoring, from the per-head softmax probabilities (head order = target_sizes)."""
    heads = list(cfg["target_sizes"].keys())
    out: dict[str, Any] = {}
    for name, p in zip(heads, probs):
        if name == "task_type":
            order = sorted(range(len(p)), key=lambda i: -p[i])
            out["task_type_1"] = cfg["task_type_map"][str(order[0])]
            out["task_type_2"] = (
                cfg["task_type_map"][str(order[1])] if p[order[1]] >= 0.1 else "NA"
            )
            out["task_type_prob"] = round(p[order[0]], 3)
        else:
            w = cfg["weights_map"][name]
            v = sum(a * b for a, b in zip(p, w)) / cfg["divisor_map"][name]
            v = round(v, 4)
            if name == "number_of_few_shots" and v < 0.05:
                v = 0.0
            out[name] = v
    out["prompt_complexity_score"] = round(
        0.35 * out["creativity_scope"]
        + 0.25 * out["reasoning"]
        + 0.15 * out["constraint_ct"]
        + 0.15 * out["domain_knowledge"]
        + 0.05 * out["contextual_knowledge"]
        + 0.05 * out["number_of_few_shots"],
        5,
    )
    return out


def decide(result: dict[str, Any], recipe: bool, lead: str, sub: str) -> dict[str, Any]:
    """Pure routing rule, kept apart from the model so it can be tested without torch."""
    c = result["prompt_complexity_score"]
    if recipe:
        return {"route": "recipe", "model": None, "why": "matches a cached recipe"}
    if c < THRESHOLD:
        return {
            "route": "simple",
            "model": sub,
            "why": f"complexity {c:.3f} < {THRESHOLD}",
        }
    return {
        "route": "complex",
        "model": lead,
        "why": f"complexity {c:.3f} >= {THRESHOLD}",
    }


def _load() -> dict[str, Any]:
    """Load tokenizer, backbone and heads once per process. Raises if torch/transformers are missing."""
    with _lock:
        if _state:
            return _state
        t0 = time.time()
        import torch
        from huggingface_hub import snapshot_download
        from safetensors.torch import load_file
        from transformers import AutoTokenizer, DebertaV2Config, DebertaV2Model

        root = Path(
            os.environ.get("ETCH_CLASSIFIER_DIR")
            or snapshot_download(REPO, allow_patterns=FILES, local_files_only=True)
        )
        cfg = json.loads((root / "config.json").read_text())
        torch.set_num_threads(int(os.environ.get("ETCH_CLASSIFIER_THREADS", "2")))

        class Net(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.backbone = DebertaV2Model(DebertaV2Config(**_BACKBONE))
                for i, n in enumerate(cfg["target_sizes"].values()):
                    self.add_module(f"head_{i}", torch.nn.Module())
                    getattr(self, f"head_{i}").fc = torch.nn.Linear(768, n)
                self.n = len(cfg["target_sizes"])

            def forward(self, ids: Any, mask: Any) -> list[Any]:
                h = self.backbone(input_ids=ids, attention_mask=mask).last_hidden_state
                m = mask.unsqueeze(-1).float()
                pooled = (h * m).sum(1) / m.sum(1).clamp(min=1e-9)
                return [getattr(self, f"head_{i}").fc(pooled) for i in range(self.n)]

        net = Net()
        sd = load_file(str(root / "model.safetensors"))
        sd.pop("backbone.embeddings.position_ids", None)
        net.load_state_dict(sd, strict=True)
        net.eval()
        tok = AutoTokenizer.from_pretrained(str(root))
        _state.update(
            torch=torch, net=net, tok=tok, cfg=cfg, load_s=round(time.time() - t0, 2)
        )
        return _state


def classify(ask: str) -> dict[str, Any]:
    s = _load()
    torch = s["torch"]
    t0 = time.time()
    enc = s["tok"]([ask], return_tensors="pt", truncation=True, max_length=MAX_TOKENS)
    with torch.inference_mode():
        logits = s["net"](enc["input_ids"], enc["attention_mask"])
    probs = [torch.softmax(x, dim=1)[0].tolist() for x in logits]
    out = scores(probs, s["cfg"])
    out["seconds"] = round(time.time() - t0, 3)
    out["load_seconds"] = s["load_s"]
    return out


def _precomputed() -> dict[str, Any] | None:
    """Scores the backend's classifier process already computed for this build (ETCH_CLASSIFIED)."""
    raw = os.environ.get("ETCH_CLASSIFIED")
    if not raw:
        return None
    try:
        res = json.loads(raw)
    except ValueError:
        return None
    return res if isinstance(res, dict) and "prompt_complexity_score" in res else None


def route(ask: str, recipe: bool, lead: str, sub: str) -> dict[str, Any] | None:
    """None when the router is off or cannot load; the caller then keeps its default model."""
    if not enabled():
        return None
    try:
        res = _precomputed() or classify(ask)
    except (
        Exception
    ) as e:  # optional dependency or download failure: fall back to the default path
        return {
            "route": "off",
            "model": None,
            "why": f"classifier unavailable: {type(e).__name__}",
        }
    return {
        **decide(res, recipe, lead, sub),
        "task_type": res["task_type_1"],
        "complexity": res["prompt_complexity_score"],
        "seconds": res["seconds"],
        "load_seconds": res["load_seconds"],
    }


def serve(inp: Any = None, out: Any = None) -> None:
    """JSON lines: {"ask": "..."} in, the classify() result (or {"error": ...}) out. Loads the model first and
    prints {"ready": true, "load_seconds": ...} so the caller knows the one-time load is done."""
    import sys

    inp, out = inp or sys.stdin, out or sys.stdout
    try:
        st = _load()
        out.write(json.dumps({"ready": True, "load_seconds": st["load_s"]}) + "\n")
    except Exception as e:  # missing packages or weights: say so once, then answer every ask with the error
        out.write(json.dumps({"ready": False, "error": f"{type(e).__name__}: {e}"[:300]}) + "\n")
    out.flush()
    for line in inp:
        try:
            ask = str(json.loads(line).get("ask", ""))[:4000]
            res: dict[str, Any] = classify(ask)
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {e}"[:300]}
        out.write(json.dumps(res) + "\n")
        out.flush()


def download() -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(REPO, allow_patterns=FILES)


if __name__ == "__main__":
    import sys

    if "--download" in sys.argv:
        print(download())
    elif "--serve" in sys.argv:
        serve()
    else:
        for a in sys.argv[1:]:
            print(json.dumps({"ask": a, **classify(a)}))
