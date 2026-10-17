"""Part category classifier (e5-small embeddings + logistic regression, runs locally).

Text is embedded with intfloat/multilingual-e5-small (ONNX, from the local Hugging Face cache, offline),
then a logistic regression trained on KiCad's own footprint libraries predicts the library name
(Diode_SMD, Connector_Audio, Button_Switch_Keyboard, ...). Training text is each footprint's
(descr ...) plus (tags ...); the label is the .pretty library it ships in.

  uv run --project engine --with onnxruntime --with tokenizers --with scikit-learn \
      python -m agent.classifier train      # writes engine/parts/classifier.npz + classifier.json
  ... python -m agent.classifier "TRRS jack PJ320A"

At run time only numpy + onnxruntime + tokenizers are needed (the regression is a matrix product).
Every number in classifier.json was measured by `train`; nothing is estimated.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from etchlib.kicad import FOOTPRINT_DIR

from . import config

NAME = "local classifier: e5-small embeddings + logistic regression"
HF = (
    Path.home()
    / ".cache/huggingface/hub/models--intfloat--multilingual-e5-small/snapshots"
)
OUT = config.ENGINE / "parts/classifier.npz"
META = config.ENGINE / "parts/classifier.json"
MAX_PER_CLASS = 120
MIN_PER_CLASS = 8


def _snap() -> Path:
    return next(p for p in sorted(HF.iterdir()) if (p / "onnx/model.onnx").exists())


class Embedder:
    def __init__(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        s = _snap()
        self.tok = Tokenizer.from_file(str(s / "tokenizer.json"))
        self.tok.enable_truncation(128)
        self.tok.enable_padding(pad_id=self.tok.token_to_id("<pad>") or 1)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(
            str(s / "onnx/model.onnx"), so, providers=["CPUExecutionProvider"]
        )
        self.inputs = {i.name for i in self.sess.get_inputs()}

    def __call__(self, texts: list[str]) -> np.ndarray:
        enc = self.tok.encode_batch([f"query: {t}" for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        h = self.sess.run(None, feed)[0]  # last hidden state [b, t, 384]
        m = mask[..., None].astype(np.float32)
        v = (h * m).sum(1) / np.maximum(m.sum(1), 1e-9)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def _sexpr_str(text: str, head: str) -> str:
    m = re.search(r"\(" + head + r'\s+"((?:[^"\\]|\\.)*)"', text)
    return m.group(1) if m else ""


def dataset() -> tuple[list[str], list[str]]:
    texts, labels = [], []
    for lib in sorted(FOOTPRINT_DIR.glob("*.pretty")):
        rows = []
        for f in sorted(lib.glob("*.kicad_mod")):
            head = f.read_text(errors="ignore")[:4000]
            t = " ".join(
                x for x in (_sexpr_str(head, "descr"), _sexpr_str(head, "tags")) if x
            ).strip()
            if t:
                rows.append(t)
        rows = list(dict.fromkeys(rows))  # identical texts in one library add nothing
        if len(rows) < MIN_PER_CLASS:
            continue
        rng = np.random.default_rng(0)
        if len(rows) > MAX_PER_CLASS:
            rows = [
                rows[i]
                for i in sorted(rng.choice(len(rows), MAX_PER_CLASS, replace=False))
            ]
        texts += rows
        labels += [lib.stem] * len(rows)
    return texts, labels


def train() -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split

    t0 = time.time()
    texts, labels = dataset()
    emb = Embedder()
    t1 = time.time()
    X = np.concatenate([emb(texts[i : i + 64]) for i in range(0, len(texts), 64)])
    t_embed = time.time() - t1
    Xtr, Xte, ytr, yte = train_test_split(
        X, labels, test_size=0.2, random_state=0, stratify=labels
    )
    clf = LogisticRegression(max_iter=3000, C=20.0)
    t2 = time.time()
    clf.fit(Xtr, ytr)
    t_fit = time.time() - t2
    P = clf.predict_proba(Xte)
    top = np.argsort(-P, axis=1)
    cls = list(clf.classes_)
    yi = np.array([cls.index(y) for y in yte])
    acc1 = float((top[:, 0] == yi).mean())
    acc3 = float((top[:, :3] == yi[:, None]).any(axis=1).mean())
    # final model on all the data, same settings
    clf_all = LogisticRegression(max_iter=3000, C=20.0).fit(X, labels)
    np.savez(
        OUT,
        W=clf_all.coef_.astype(np.float32),
        b=clf_all.intercept_.astype(np.float32),
        labels=np.array(clf_all.classes_),
    )
    meta = {
        "name": NAME,
        "embedder": "intfloat/multilingual-e5-small (ONNX, local cache, mean pooling)",
        "head": "sklearn LogisticRegression (C=20), refit on all rows after the held-out test",
        "data": f"KiCad {FOOTPRINT_DIR.parent.parent.parent.name} footprint libraries: descr + tags -> library name",
        "classes": len(cls),
        "rows": len(texts),
        "rows_test": len(yte),
        "max_per_class": MAX_PER_CLASS,
        "heldout_top1": round(acc1, 4),
        "heldout_top3": round(acc3, 4),
        "embed_s": round(t_embed, 1),
        "fit_s": round(t_fit, 1),
        "total_s": round(time.time() - t0, 1),
    }
    meta["latency"] = bench(Classifier(emb))
    META.write_text(json.dumps(meta, indent=1) + "\n")
    return meta


def bench(c: "Classifier", n: int = 200) -> dict[str, float]:
    qs = [
        "SOD-123 diode",
        "TRRS audio jack 3.5mm",
        "Cherry MX hotswap socket",
        "OLED 128x32 display module",
        "tactile reset switch 6mm",
        "USB-C receptacle",
        "Pro Micro ATmega32U4 module",
        "M2 standoff",
    ]
    for q in qs:
        c.classify(q)  # warm-up
    ts = []
    for i in range(n):
        t = time.perf_counter()
        c.classify(qs[i % len(qs)])
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return {
        "calls": n,
        "p50_ms": round(ts[n // 2], 2),
        "p90_ms": round(ts[int(n * 0.9)], 2),
        "mean_ms": round(sum(ts) / n, 2),
        "threads": 4,
        "machine": "this Mac, CPU (onnxruntime)",
    }


class Classifier:
    def __init__(self, emb: Embedder | None = None) -> None:
        t = time.perf_counter()
        self.emb = emb or Embedder()
        z = np.load(OUT)
        self.W, self.b, self.labels = z["W"], z["b"], [str(x) for x in z["labels"]]
        self.load_ms = round((time.perf_counter() - t) * 1000, 1)
        self.lock = threading.Lock()

    def classify(self, text: str, k: int = 3) -> dict[str, Any]:
        t = time.perf_counter()
        with self.lock:  # one ONNX session; calls are a few ms, so a lock is enough
            v = self.emb([text])[0]
        z = self.W @ v + self.b
        p = np.exp(z - z.max())
        p /= p.sum()
        top = np.argsort(-p)[:k]
        return {
            "label": self.labels[top[0]],
            "conf": round(float(p[top[0]]), 3),
            "top": [[self.labels[i], round(float(p[i]), 3)] for i in top],
            "ms": round((time.perf_counter() - t) * 1000, 2),
            "model": NAME,
        }


_inst: Classifier | None = None
_inst_lock = threading.Lock()


def get() -> Classifier:
    global _inst
    with _inst_lock:
        if _inst is None:
            _inst = Classifier()
        return _inst


def warm() -> threading.Thread:
    """Load the model in the background (the agent starts this before its first LLM call)."""
    th = threading.Thread(target=get, daemon=True)
    th.start()
    return th


def meta() -> dict[str, Any]:
    return json.loads(META.read_text()) if META.exists() else {}


if __name__ == "__main__":
    if sys.argv[1:] == ["train"]:
        print(json.dumps(train(), indent=1))
    else:
        c = get()
        for q in sys.argv[1:]:
            print(json.dumps({"q": q, **c.classify(q)}))
