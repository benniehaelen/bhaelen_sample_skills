#!/usr/bin/env python3
"""Render sample scorecards from the bundled example servers.

Run from the skill root:

    python examples/render_sample_scorecard.py

Loads each example server (good / bad / healthcare), scores it with the
built-in rubric, and writes a self-contained HTML scorecard per server in
auto theme, plus light and dark variants of the healthcare example so the
theme handling is visible. Re-run after changing the rubric or renderer to
refresh the committed samples.

Unlike the metadata scorer's sample renderer, this one scores from the
example files rather than synthetic pre-scored data, so it needs
``tiktoken`` installed (the default tokenizer). It needs no network access
and no API key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from _fetchers import load_from_file  # noqa: E402
from _scoring import score_catalog  # noqa: E402
from render_scorecard import make_html  # noqa: E402

_RUBRIC = json.loads((_HERE.parent / "rubric.json").read_text(encoding="utf-8"))

# Example file stem -> sample output stem.
_EXAMPLES = {
    "good_server": "good",
    "bad_server": "bad",
    "healthcare_server": "healthcare",
}


def _score(example_stem: str) -> dict:
    catalog = load_from_file(_HERE / f"{example_stem}.json")
    return score_catalog(catalog, _RUBRIC, tokenizer="cl100k_base", serialization="raw")


def main() -> int:
    # One auto-theme sample per server, illustrating the score range.
    for example_stem, sample_stem in _EXAMPLES.items():
        report = _score(example_stem)
        out = _HERE / f"sample_{sample_stem}.html"
        out.write_text(make_html(report, theme="auto"), encoding="utf-8")
        cat = report["catalog"]
        print(f"Wrote {out.name} (catalog {cat['catalog_score']}/{cat['catalog_grade']}, {cat['total_tokens']} tokens)")

    # Light and dark variants of the healthcare example for theme reference.
    report = _score("healthcare_server")
    for theme in ("light", "dark"):
        out = _HERE / f"sample_healthcare_{theme}.html"
        out.write_text(make_html(report, theme=theme), encoding="utf-8")
        print(f"Wrote {out.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
