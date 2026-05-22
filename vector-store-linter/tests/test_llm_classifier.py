"""Unit tests for the LLM-assisted failure classifier.

A fake Anthropic client returns scripted text; no network or API key is used.
Covers parsing (including code fences, unknown-mode and low-confidence
filtering), and the three fallback-to-heuristic paths: no API key, missing SDK,
and an API error.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from adapters.base import RetrievalResult
from failure_classifier import llm_assisted
from failure_classifier.heuristic import ClassificationContext, FailureCase


def _case():
    return FailureCase(
        query="readmission penalty methodology",
        expected_ids={"d1"},
        retrieved=[RetrievalResult(doc_id="d2", score=0.3, metadata={}, content="unrelated text")],
        expected_docs={"d1": {"content": "completely different words about apples", "metadata": {}}},
    )


class FakeMessages:
    def __init__(self, text=None, error=None):
        self._text = text
        self._error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class FakeClient:
    def __init__(self, text=None, error=None):
        self.messages = FakeMessages(text, error)


# ----- parsing -------------------------------------------------------------


def test_parse_simple_array():
    client = FakeClient(text='[{"mode": "vocabulary_mismatch", "confidence": 0.8, "explanation": "diff words"}]')
    results = llm_assisted.classify(_case(), ClassificationContext(), client=client)
    assert len(results) == 1
    assert results[0].mode == "vocabulary_mismatch"
    assert results[0].confidence == 0.8


def test_parse_strips_code_fence():
    client = FakeClient(text='```json\n[{"mode":"semantic_distance","confidence":0.7,"explanation":"far"}]\n```')
    results = llm_assisted.classify(_case(), ClassificationContext(), client=client)
    assert results[0].mode == "semantic_distance"


def test_parse_drops_unknown_mode_and_low_confidence():
    text = (
        '[{"mode":"not_a_real_mode","confidence":0.9,"explanation":"x"},'
        '{"mode":"chunking_artifact","confidence":0.2,"explanation":"weak"},'
        '{"mode":"boilerplate_pollution","confidence":0.75,"explanation":"ok"}]'
    )
    results = llm_assisted.classify(_case(), ClassificationContext(), client=client_with(text))
    assert {r.mode for r in results} == {"boilerplate_pollution"}


def test_results_sorted_by_confidence():
    text = (
        '[{"mode":"vocabulary_mismatch","confidence":0.6,"explanation":"a"},'
        '{"mode":"model_drift","confidence":0.9,"explanation":"b"}]'
    )
    results = llm_assisted.classify(_case(), ClassificationContext(), client=client_with(text))
    assert [r.mode for r in results] == ["model_drift", "vocabulary_mismatch"]


def test_caches_system_block():
    client = FakeClient(text="[]")
    llm_assisted.classify(_case(), ClassificationContext(), client=client)
    system = client.messages.calls[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def client_with(text):
    return FakeClient(text=text)


# ----- taxonomy is driven by the rubric (single source) --------------------


def test_context_taxonomy_drives_prompt_and_validation():
    # A custom taxonomy supplied through the context: one mode the default set
    # does not contain. It must appear in the system prompt and validate as a
    # recognized mode, while a default-only mode is now rejected.
    context = ClassificationContext(
        failure_modes=[
            {"id": "tenant_leakage", "name": "Tenant leakage", "description": "results crossed a namespace boundary."},
        ]
    )
    text = (
        '[{"mode":"tenant_leakage","confidence":0.8,"explanation":"crossed namespaces"},'
        '{"mode":"vocabulary_mismatch","confidence":0.9,"explanation":"default mode, now out of taxonomy"}]'
    )
    client = client_with(text)
    results = llm_assisted.classify(_case(), context, client=client)
    assert {r.mode for r in results} == {"tenant_leakage"}
    system_text = client.messages.calls[0]["system"][0]["text"]
    assert "tenant_leakage: results crossed a namespace boundary." in system_text
    assert "vocabulary_mismatch" not in system_text


def test_empty_context_uses_default_taxonomy_in_prompt():
    client = FakeClient(text="[]")
    llm_assisted.classify(_case(), ClassificationContext(), client=client)
    system_text = client.messages.calls[0]["system"][0]["text"]
    assert "vocabulary_mismatch:" in system_text
    assert "model_drift:" in system_text


# ----- fallback to heuristic -----------------------------------------------


def test_fallback_when_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # No client injected, so it tries to build one and fails over to the heuristic.
    results = llm_assisted.classify(_case(), ClassificationContext())
    # The heuristic sees a vocabulary gap for this case.
    assert "vocabulary_mismatch" in {r.mode for r in results}


def test_fallback_when_sdk_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    results = llm_assisted.classify(_case(), ClassificationContext())
    assert "vocabulary_mismatch" in {r.mode for r in results}


def test_fallback_on_api_error():
    client = FakeClient(error=RuntimeError("rate limited"))
    results = llm_assisted.classify(_case(), ClassificationContext(), client=client)
    # Fell back to the heuristic, which still detects the vocabulary gap.
    assert "vocabulary_mismatch" in {r.mode for r in results}


def test_fallback_on_unparseable_response():
    client = FakeClient(text="I think the problem is vocabulary, not JSON at all.")
    results = llm_assisted.classify(_case(), ClassificationContext(), client=client)
    assert "vocabulary_mismatch" in {r.mode for r in results}
