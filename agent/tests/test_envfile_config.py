"""Self-hosting: .env loading (agent/envfile.py) and which key goes to which endpoint (agent/config.py)."""

import importlib
import os

from agent import config, envfile


def test_envfile_parses_and_never_overrides(tmp_path, monkeypatch):
    f = tmp_path / "sample.env"
    f.write_text(
        "# comment\n"
        "ETCH_T_A=1\n"
        'export ETCH_T_B="two words"\n'
        "ETCH_T_C=three  # trailing comment\n"
        "ETCH_T_D=\n"
        "ETCH_T_E='from file'\n"
        "not a setting\n"
    )
    for k in ("ETCH_T_A", "ETCH_T_B", "ETCH_T_C", "ETCH_T_D"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ETCH_T_E", "from env")
    monkeypatch.setenv("ETCH_T_BLANK", "")
    envfile.load(f)
    assert os.environ["ETCH_T_A"] == "1"
    assert os.environ["ETCH_T_B"] == "two words"
    assert os.environ["ETCH_T_C"] == "three"
    assert "ETCH_T_D" not in os.environ  # blank means "use the default"
    assert os.environ["ETCH_T_E"] == "from env"  # the environment wins
    assert "ETCH_T_BLANK" not in os.environ  # docker compose passes blanks as ""


def test_missing_envfile_is_fine(tmp_path):
    envfile.load(tmp_path / "nope.env")


def _reload(monkeypatch, **env):
    for k in ("ETCH_BASE_URL", "ETCH_API_KEY", "ETCH_API_KEY_SOURCE", "ETCH_FALLBACK_BASE_URL",
              "ETCH_FALLBACK_API_KEY", "ETCH_FALLBACK_KEY_SOURCE", "CRUSOE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ETCH_ENV_FILE", "/nonexistent/.env")
    c = importlib.reload(config)
    c.key_for.cache_clear()
    monkeypatch.setattr(c, "keychain", lambda source: "")
    return c


def test_local_endpoint_needs_no_key(monkeypatch):
    c = _reload(monkeypatch, ETCH_BASE_URL="http://127.0.0.1:11434/v1")
    assert c.api_key() == "local"


def test_crusoe_key_never_goes_to_another_endpoint(monkeypatch):
    c = _reload(monkeypatch, ETCH_BASE_URL="https://example.com/v1", CRUSOE_API_KEY="crusoe-secret")
    try:
        c.api_key()
        raise AssertionError("expected no key")
    except RuntimeError:
        pass


def test_openai_endpoint_uses_openai_key(monkeypatch):
    c = _reload(monkeypatch, ETCH_BASE_URL="https://api.openai.com/v1", OPENAI_API_KEY="oa", CRUSOE_API_KEY="cr")
    assert c.api_key() == "oa"


def test_default_crusoe_and_explicit_key(monkeypatch):
    c = _reload(monkeypatch, CRUSOE_API_KEY="cr")
    assert c.api_key() == "cr"
    c = _reload(monkeypatch, ETCH_BASE_URL="https://example.com/v1", ETCH_API_KEY="mine")
    assert c.api_key() == "mine"


def test_openai_key_not_sent_to_other_fallback(monkeypatch):
    c = _reload(monkeypatch, ETCH_FALLBACK_BASE_URL="https://example.com/v1", OPENAI_API_KEY="oa")
    assert c.fallback_key() == ""
    c = _reload(monkeypatch, OPENAI_API_KEY="oa")
    assert c.fallback_key() == "oa"
