"""Command-line entry point for vector-store-linter.

Four subcommands:

    audit-config            Score Tier 1 (configuration). Fast, no data read.
    audit-content           Score Tier 1 and Tier 2. Samples vectors by default.
    evaluate                Score all three tiers. Requires a ground-truth CSV.
    generate-ground-truth   Generate a synthetic ground-truth CSV from the store.

This module owns argument parsing, cross-flag validation, and dispatch. The
subcommand bodies are stubbed for now and are filled in by later tasks
(audit-config wires up in Task 2.4). Argparse is used rather than Click to
match the sibling skills, which avoids adding a dependency.

Exit codes (per IMPLEMENTATION_PLAN.md Task 1.3):

    0   success
    1   expectation not met (a CI gate flag failed)
    2   usage error (bad flags or an invalid combination)
    3   store connection error

Note: this mapping differs from the three sibling skills, which use 1 for
upstream-fetch errors and 3 for expectation failures. The divergence is
intentional here because the plan specifies it; it is worth reconciling across
the collection at some point.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from adapters import StoreConnectionError, get_adapter_class
from checks.tier1_config import run_tier1
from checks.tier2_content import run_tier2
from checks.tier3_retrieval import QueryEvaluation, hit_at_k, run_tier3
from failure_classifier import ClassificationContext, FailureCase, newest_timestamp
from failure_classifier import classify as heuristic_classify
from failure_classifier import llm_classify
from ground_truth import GroundTruth, GroundTruthError
from ground_truth.generator import GenerationError, generate_queries, to_csv
from renderers import html_renderer, json_renderer, markdown_renderer
from rubric import Rubric, RubricError
from scoring import Scorecard, compute_score

logger = logging.getLogger("vector_store_linter")

_DEFAULT_RUBRIC = Path(__file__).resolve().parent / "rubric.yaml"
# Output format to renderer. All three formats are produced from the same
# scorecard; the format flag selects which is written.
_RENDERERS = {
    "json": json_renderer.render,
    "markdown": markdown_renderer.render,
    "html": html_renderer.render,
}

EXIT_SUCCESS = 0
EXIT_EXPECTATION_NOT_MET = 1
EXIT_USAGE_ERROR = 2
EXIT_STORE_CONNECTION_ERROR = 3

STORES = ("pinecone", "bigquery", "pgvector")
OUTPUT_FORMATS = ("json", "markdown", "html")
DEFAULT_SAMPLE_SIZE = 10000

# Top-k that defines a query "failure" for classification and concentration.
_FAILURE_K = 10
# Whether a store's similarity score is a distance (lower is closer). Pinecone
# returns a similarity; BigQuery and pgvector return a distance.
_SCORE_IS_DISTANCE = {"pinecone": False, "bigquery": True, "pgvector": True}

# Which connection selector each store requires, and which it forbids.
_STORE_SELECTOR_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "pinecone": {"required": ("index",), "forbidden": ("table", "connection_string")},
    "bigquery": {"required": ("table",), "forbidden": ("index", "connection_string")},
    "pgvector": {"required": ("connection_string", "table"), "forbidden": ("index",)},
}

# Human-readable flag names for error messages.
_SELECTOR_FLAGS: dict[str, str] = {
    "index": "--index",
    "table": "--table",
    "connection_string": "--connection-string",
}


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser with all four subcommands."""
    parser = argparse.ArgumentParser(
        prog="lint.py",
        description="Audit and score a production vector store against a quality rubric.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    connection_parent = argparse.ArgumentParser(add_help=False)
    connection_parent.add_argument(
        "--store", choices=STORES, required=True, help="Vector store type."
    )
    connection_parent.add_argument("--index", help="Index name (Pinecone).")
    connection_parent.add_argument("--table", help="Table name (BigQuery, pgvector).")
    connection_parent.add_argument(
        "--connection-string", help="Postgres connection string (pgvector)."
    )
    connection_parent.add_argument(
        "--namespace", help="Namespace to read (Pinecone only). Defaults to the default namespace."
    )

    rubric_parent = argparse.ArgumentParser(add_help=False)
    rubric_parent.add_argument(
        "--rubric-config", help="Path to a custom rubric YAML. Defaults to scripts/rubric.yaml."
    )

    output_parent = argparse.ArgumentParser(add_help=False)
    output_parent.add_argument(
        "--output-format", choices=OUTPUT_FORMATS, default="json", help="Output format."
    )
    output_parent.add_argument(
        "--output", help="Output file path. Writes to stdout when omitted."
    )

    expectation_parent = argparse.ArgumentParser(add_help=False)
    expectation_parent.add_argument(
        "--expect-min-score", type=int, metavar="N", help="Exit 1 if the overall score is below N."
    )
    expectation_parent.add_argument(
        "--expect-min-recall-at-10", type=float, metavar="R",
        help="Exit 1 if recall at 10 is below R.",
    )
    expectation_parent.add_argument(
        "--expect-max-duplicate-rate", type=float, metavar="R",
        help="Exit 1 if the duplicate rate exceeds R.",
    )
    expectation_parent.add_argument(
        "--expect-zero-orphans", action="store_true",
        help="Exit 1 if any orphan source references are found.",
    )
    expectation_parent.add_argument(
        "--expect-zero-dimension-mismatches", action="store_true",
        help="Exit 1 if any vectors have a mismatched dimension.",
    )

    def add_sampling(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--sample-size", type=int, default=None, metavar="N",
            help=f"Number of vectors to sample (default: {DEFAULT_SAMPLE_SIZE}). "
                 "Cannot be combined with --full.",
        )
        target.add_argument(
            "--full", action="store_true",
            help="Read every vector instead of sampling. Slow on large stores.",
        )

    # audit-config
    subparsers.add_parser(
        "audit-config",
        parents=[connection_parent, rubric_parent, output_parent, expectation_parent],
        help="Score Tier 1 (configuration). Fast, no data read.",
    )

    # audit-content
    p_content = subparsers.add_parser(
        "audit-content",
        parents=[connection_parent, rubric_parent, output_parent, expectation_parent],
        help="Score Tier 1 and Tier 2. Samples vectors by default.",
    )
    add_sampling(p_content)

    # evaluate
    p_eval = subparsers.add_parser(
        "evaluate",
        parents=[connection_parent, rubric_parent, output_parent, expectation_parent],
        help="Score all three tiers. Requires a ground-truth CSV.",
    )
    add_sampling(p_eval)
    p_eval.add_argument(
        "--ground-truth", required=True, metavar="CSV",
        help="Path to a ground-truth CSV (query, relevant_doc_ids, ...).",
    )
    p_eval.add_argument(
        "--query-embeddings", metavar="JSON",
        help="Path to a JSON object mapping each query string to its embedding vector. "
             "Required for stores whose embed_text returns None (all current adapters).",
    )
    p_eval.add_argument(
        "--k", default="5,10,20", metavar="K[,K...]",
        help="Comma-separated cutoffs for retrieval metrics (default: 5,10,20).",
    )
    p_eval.add_argument(
        "--llm-assist", action="store_true",
        help="Use an LLM to classify retrieval failures. Requires ANTHROPIC_API_KEY.",
    )

    # generate-ground-truth
    p_gen = subparsers.add_parser(
        "generate-ground-truth",
        parents=[connection_parent],
        help="Generate a synthetic ground-truth CSV from the store. Uses an LLM.",
    )
    p_gen.add_argument(
        "--output", required=True, metavar="CSV", help="Path to write the generated CSV."
    )
    p_gen.add_argument(
        "--queries-per-doc", type=int, default=2, metavar="N",
        help="Number of queries to generate per sampled document (default: 2).",
    )
    p_gen.add_argument(
        "--sample-size", type=int, default=100, metavar="N",
        help="Number of documents to sample for generation (default: 100).",
    )

    return parser


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Reject invalid flag combinations.

    Calls parser.error (which exits 2) on the first problem found. Checks the
    store-to-selector match for every command, the --full / --sample-size
    conflict for the sampling commands, and the --k format for evaluate.
    """
    _validate_store_selectors(args, parser)
    _validate_expectation_applicability(args, parser)

    if getattr(args, "full", False) and getattr(args, "sample_size", None) is not None:
        parser.error("--full and --sample-size cannot be combined; --full reads every vector.")

    if args.command == "evaluate":
        _validate_k(args.k, parser)


def _validate_expectation_applicability(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Reject expectation flags a subcommand cannot evaluate.

    Tier 2 gates need a content audit; the Tier 3 recall gate needs evaluate.
    Using one on a subcommand that does not reach that tier is a usage error.
    """
    tier2 = (
        ("expect_max_duplicate_rate", "--expect-max-duplicate-rate"),
        ("expect_zero_orphans", "--expect-zero-orphans"),
    )
    tier3 = (("expect_min_recall_at_10", "--expect-min-recall-at-10"),)

    def is_set(attr: str) -> bool:
        value = getattr(args, attr, None)
        return value if isinstance(value, bool) else value is not None

    if args.command == "audit-config":
        for attr, flag in tier2 + tier3:
            if is_set(attr):
                parser.error(f"{flag} requires the audit-content or evaluate subcommand.")
    elif args.command == "audit-content":
        for attr, flag in tier3:
            if is_set(attr):
                parser.error(f"{flag} requires the evaluate subcommand.")


def _validate_store_selectors(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    rules = _STORE_SELECTOR_RULES[args.store]
    for required in rules["required"]:
        if not getattr(args, required, None):
            parser.error(f"--store {args.store} requires {_SELECTOR_FLAGS[required]}.")
    for forbidden in rules["forbidden"]:
        if getattr(args, forbidden, None):
            parser.error(
                f"--store {args.store} does not use {_SELECTOR_FLAGS[forbidden]}."
            )
    if args.store != "pinecone" and getattr(args, "namespace", None):
        parser.error("--namespace is only supported for --store pinecone.")


def _validate_k(raw: str, parser: argparse.ArgumentParser) -> None:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        parser.error("--k must list at least one positive integer, for example 5,10,20.")
    for part in parts:
        if not part.isdigit() or int(part) < 1:
            parser.error(f"--k values must be positive integers, got {part!r}.")


def parse_k_values(raw: str) -> list[int]:
    """Parse the --k flag into a sorted list of unique positive integers."""
    return sorted({int(p.strip()) for p in raw.split(",") if p.strip()})


# ----- subcommand stubs (filled in by later tasks) -------------------------


def _not_implemented(command: str) -> int:
    logger.warning("Subcommand %r is parsed and validated but not yet implemented.", command)
    return EXIT_SUCCESS


def cmd_audit_config(args: argparse.Namespace) -> int:
    """Run a Tier 1 configuration audit and render the scorecard.

    Reads only store metadata, so no vector sample is taken; the
    distance-metric normalization sub-check is deferred to audit-content.
    """
    try:
        rubric, rubric_source = _load_rubric(args)
    except RubricError as exc:
        logger.error("Rubric error: %s", exc)
        return EXIT_USAGE_ERROR

    try:
        adapter = _build_adapter(args)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE_ERROR

    try:
        with adapter:
            config = adapter.describe()
    except StoreConnectionError as exc:
        logger.error("%s", exc)
        return EXIT_STORE_CONNECTION_ERROR

    results = run_tier1(rubric, config, vectors=None)
    metadata = _build_metadata(args, modes=["audit-config"], rubric_source=rubric_source)
    scorecard = compute_score(rubric, results, metadata=metadata)

    _write_output(scorecard, args)

    failures = _evaluate_gates(scorecard, args)
    if failures:
        for message in failures:
            logger.error("Expectation not met: %s", message)
        return EXIT_EXPECTATION_NOT_MET
    return EXIT_SUCCESS


def _load_rubric(args: argparse.Namespace) -> tuple[Rubric, dict]:
    """Load the rubric (custom path or the bundled default) and its provenance."""
    source = args.rubric_config or str(_DEFAULT_RUBRIC)
    rubric = Rubric.load(source)
    provenance = {"source": args.rubric_config if args.rubric_config else "builtin", "path": str(source)}
    return rubric, provenance


def _build_adapter(args: argparse.Namespace):
    """Construct the adapter for the chosen store from the connection flags."""
    adapter_cls = get_adapter_class(args.store)
    if args.store == "pinecone":
        return adapter_cls(index_name=args.index, host=None, namespace=getattr(args, "namespace", None))
    if args.store == "bigquery":
        return adapter_cls(table=args.table)
    if args.store == "pgvector":
        return adapter_cls(connection_string=args.connection_string, table=args.table)
    raise ValueError(f"Unsupported store {args.store!r}.")


def _build_metadata(
    args: argparse.Namespace,
    modes: list[str],
    rubric_source: dict,
    extra: dict | None = None,
) -> dict:
    """Assemble run metadata for the scorecard.

    The connection string is intentionally omitted; it is a secret and must not
    appear in output. Only the store type and the index or table name are kept.
    extra carries mode-specific fields such as the sample size.
    """
    store = {"store_type": args.store}
    for attr in ("index", "table"):
        value = getattr(args, attr, None)
        if value:
            store[attr] = value
    metadata = {
        "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "store": store,
        "modes": modes,
        "rubric": rubric_source,
    }
    if extra:
        metadata.update(extra)
    return metadata


def _render(scorecard: Scorecard, output_format: str, theme: str = "auto") -> str:
    renderer = _RENDERERS.get(output_format)
    if renderer is None:
        logger.warning(
            "The %s renderer is not available; writing JSON instead.", output_format
        )
        return json_renderer.render(scorecard)
    return renderer(scorecard, theme=theme)


def _write_output(scorecard: Scorecard, args: argparse.Namespace) -> None:
    text = _render(scorecard, args.output_format, getattr(args, "theme", "auto"))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        logger.info("Wrote %s", args.output)
    else:
        sys.stdout.write(text)


def _evaluate_gates(scorecard: Scorecard, args: argparse.Namespace) -> list[str]:
    """Evaluate the applicable CI gate flags. Returns failure messages.

    Only the gates valid for the subcommand reach here (others are rejected at
    parse time). Tier 2 and Tier 3 gates are evaluated in their phases.
    """
    failures: list[str] = []
    if getattr(args, "expect_min_score", None) is not None:
        if scorecard.overall_score < args.expect_min_score:
            failures.append(
                f"overall score {scorecard.overall_score} is below the required {args.expect_min_score}"
            )
    if getattr(args, "expect_zero_dimension_mismatches", False):
        result = _result_by_id(scorecard, "c_dimension_consistency")
        if result is not None and not result.passed:
            failures.append("dimension consistency check did not pass")
    if getattr(args, "expect_max_duplicate_rate", None) is not None:
        result = _result_by_id(scorecard, "c_no_exact_duplicates")
        rate = result.evidence.get("duplicate_rate") if result else None
        if rate is not None and rate > args.expect_max_duplicate_rate:
            failures.append(
                f"exact-duplicate rate {rate} exceeds the allowed {args.expect_max_duplicate_rate}"
            )
    if getattr(args, "expect_zero_orphans", False):
        result = _result_by_id(scorecard, "c_no_orphan_references")
        missing = result.evidence.get("missing_source_count", 0) if result else 0
        if missing:
            failures.append(f"{missing} vector(s) lack a source reference")
    if getattr(args, "expect_min_recall_at_10", None) is not None:
        result = _result_by_id(scorecard, "c_recall_at_10")
        value = result.evidence.get("value") if result else None
        if value is not None and value < args.expect_min_recall_at_10:
            failures.append(
                f"recall@10 {value} is below the required {args.expect_min_recall_at_10}"
            )
    return failures


def _result_by_id(scorecard: Scorecard, criterion_id: str):
    for result in scorecard.results:
        if result.criterion_id == criterion_id:
            return result
    return None


def cmd_audit_content(args: argparse.Namespace) -> int:
    """Run a Tier 1 plus Tier 2 audit and render the scorecard.

    Reads a sample of vectors (or the whole store with --full) once, then runs
    the Tier 1 checks against that sample (so the distance-metric normalization
    sub-check runs here) and the Tier 2 content checks.
    """
    try:
        rubric, rubric_source = _load_rubric(args)
    except RubricError as exc:
        logger.error("Rubric error: %s", exc)
        return EXIT_USAGE_ERROR

    try:
        adapter = _build_adapter(args)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE_ERROR

    sample_size = None if args.full else (args.sample_size if args.sample_size is not None else DEFAULT_SAMPLE_SIZE)
    try:
        with adapter:
            config = adapter.describe()
            vectors = list(adapter.iter_vectors(sample_size=sample_size))
    except StoreConnectionError as exc:
        logger.error("%s", exc)
        return EXIT_STORE_CONNECTION_ERROR

    if not vectors:
        logger.warning("No vectors were read; Tier 2 checks will report unknowns.")

    results = run_tier1(rubric, config, vectors=vectors) + run_tier2(rubric, config, vectors)
    extra = {"sample_size": sample_size, "sampled_count": len(vectors), "full_read": bool(args.full)}
    metadata = _build_metadata(args, modes=["audit-content"], rubric_source=rubric_source, extra=extra)
    scorecard = compute_score(rubric, results, metadata=metadata)

    _write_output(scorecard, args)

    failures = _evaluate_gates(scorecard, args)
    if failures:
        for message in failures:
            logger.error("Expectation not met: %s", message)
        return EXIT_EXPECTATION_NOT_MET
    return EXIT_SUCCESS


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Score all three tiers, including retrieval against ground truth.

    Reads a sample for Tier 1 and Tier 2, runs a search per ground-truth query
    for Tier 3, classifies failing queries, and renders the scorecard. Query
    vectors come from --query-embeddings (the adapters do not embed text).
    """
    try:
        rubric, rubric_source = _load_rubric(args)
    except RubricError as exc:
        logger.error("Rubric error: %s", exc)
        return EXIT_USAGE_ERROR

    try:
        ground_truth = GroundTruth.load(args.ground_truth)
    except GroundTruthError as exc:
        logger.error("Ground-truth error: %s", exc)
        return EXIT_USAGE_ERROR

    embeddings: dict[str, list[float]] = {}
    if args.query_embeddings:
        try:
            embeddings = _load_query_embeddings(args.query_embeddings)
        except ValueError as exc:
            logger.error("%s", exc)
            return EXIT_USAGE_ERROR

    try:
        adapter = _build_adapter(args)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE_ERROR

    k_values = parse_k_values(args.k)
    max_k = max(k_values) if k_values else _FAILURE_K
    sample_size = None if args.full else (args.sample_size if args.sample_size is not None else DEFAULT_SAMPLE_SIZE)

    try:
        with adapter:
            config = adapter.describe()
            sample = list(adapter.iter_vectors(sample_size=sample_size))
            searched = _run_searches(adapter, ground_truth, embeddings, max_k)
            expected_docs = _fetch_expected_docs(adapter, searched)
    except StoreConnectionError as exc:
        logger.error("%s", exc)
        return EXIT_STORE_CONNECTION_ERROR

    if not searched:
        logger.error(
            "No ground-truth queries could be evaluated. Provide --query-embeddings; "
            "this store's embed_text returns None."
        )
        return EXIT_USAGE_ERROR

    evaluations = [
        QueryEvaluation(q.query, q.relevant_doc_ids, [r.doc_id for r in results], q.query_type)
        for q, results in searched
    ]
    results = (
        run_tier1(rubric, config, vectors=sample)
        + run_tier2(rubric, config, sample)
        + run_tier3(rubric, evaluations)
    )

    failing_queries = _classify_failures(searched, config, args, expected_docs, sample, rubric)
    warnings: list[str] = []
    if ground_truth.is_synthetic:
        warnings.append(
            "Tier 3 results are derived from synthetic ground truth and are likely inflated; "
            "generated queries tend to reuse source-document vocabulary."
        )

    extra = {
        "sample_size": sample_size,
        "sampled_count": len(sample),
        "full_read": bool(args.full),
        "k": k_values,
        "ground_truth": {
            "path": args.ground_truth,
            "query_count": len(ground_truth.queries),
            "evaluated": len(searched),
            "is_synthetic": ground_truth.is_synthetic,
        },
        "llm_assist": bool(args.llm_assist),
        "failing_queries": failing_queries,
        "warnings": warnings,
    }
    metadata = _build_metadata(args, modes=["evaluate"], rubric_source=rubric_source, extra=extra)
    scorecard = compute_score(rubric, results, metadata=metadata)

    _write_output(scorecard, args)

    failures = _evaluate_gates(scorecard, args)
    if failures:
        for message in failures:
            logger.error("Expectation not met: %s", message)
        return EXIT_EXPECTATION_NOT_MET
    return EXIT_SUCCESS


def _run_searches(adapter, ground_truth, embeddings, max_k):
    """Run one search per ground-truth query, returning (query, results) pairs.

    A query's vector comes from the supplied embeddings, then from the adapter's
    embed_text, and if neither yields one the query is skipped with a warning.
    """
    searched = []
    for q in ground_truth.queries:
        embedding = embeddings.get(q.query)
        if embedding is None:
            embedding = adapter.embed_text(q.query)
        if embedding is None:
            logger.warning("No embedding for query %r; skipping it.", q.query)
            continue
        searched.append((q, adapter.search(embedding, k=max_k)))
    return searched


def _fetch_expected_docs(adapter, searched):
    """Fetch the expected documents for the evaluated queries, by id.

    Returns a mapping of doc id to {content, metadata}. Empty when the store
    cannot fetch by id, in which case the expected-doc-dependent detectors stay
    quiet. Must be called while the adapter is connected.
    """
    ids: set[str] = set()
    for q, _results in searched:
        ids |= q.relevant_doc_ids
    if not ids:
        return {}
    try:
        fetched = adapter.fetch(sorted(ids))
    except StoreConnectionError:
        logger.warning("Could not fetch expected documents; failure classification will be limited.")
        return {}
    return {vid: {"content": rec.content, "metadata": rec.metadata} for vid, rec in fetched.items()}


def _classify_failures(searched, config, args, expected_docs, sample, rubric):
    """Classify queries with no relevant result in the top k.

    The rubric's failure-mode taxonomy is threaded into the context so the
    LLM-assisted classifier scores against the same modes the heuristic uses and
    the rubric reports, keeping the rubric the single source of the taxonomy.
    """
    appearance: Counter = Counter()
    for _q, results in searched:
        for doc in {r.doc_id for r in results[:_FAILURE_K]}:
            appearance[doc] += 1
    context = ClassificationContext(
        appearance_counts=dict(appearance),
        query_count=len(searched),
        k=_FAILURE_K,
        metric=config.distance_metric,
        score_is_distance=_SCORE_IS_DISTANCE.get(config.store_type, False),
        newest_timestamp=newest_timestamp(v.metadata for v in sample),
        failure_modes=[
            {"id": fm.id, "name": fm.name, "description": fm.description}
            for fm in rubric.failure_modes
        ],
    )
    classifier = llm_classify if args.llm_assist else heuristic_classify

    failing = []
    for q, results in searched:
        if hit_at_k(q.relevant_doc_ids, [r.doc_id for r in results], _FAILURE_K) != 0.0:
            continue
        case = FailureCase(
            query=q.query,
            expected_ids=q.relevant_doc_ids,
            retrieved=results,
            expected_docs={eid: expected_docs[eid] for eid in q.relevant_doc_ids if eid in expected_docs},
            query_type=q.query_type,
        )
        classifications = classifier(case, context)
        failing.append({
            "query": q.query,
            "query_type": q.query_type,
            "expected_ids": sorted(q.relevant_doc_ids),
            "retrieved": [{"id": r.doc_id, "score": round(float(r.score), 4)} for r in results[:_FAILURE_K]],
            "classifications": [
                {"mode": c.mode, "confidence": c.confidence, "explanation": c.explanation}
                for c in classifications
            ],
        })
    return failing


def _load_query_embeddings(path: str) -> dict[str, list[float]]:
    """Load a JSON object mapping query string to embedding vector."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"Query-embeddings file not found: {file_path}")
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Query-embeddings file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Query-embeddings file must be a JSON object mapping query text to a vector.")
    out: dict[str, list[float]] = {}
    for query, vector in data.items():
        if not isinstance(vector, list):
            raise ValueError(f"Embedding for query {query!r} must be a list of numbers.")
        out[str(query)] = [float(x) for x in vector]
    return out


def cmd_generate_ground_truth(args: argparse.Namespace) -> int:
    """Generate a synthetic ground-truth CSV from the store's content.

    Samples documents, asks an LLM for queries per document, and writes a CSV
    marked synthetic. Requires the anthropic SDK and an API key.
    """
    try:
        adapter = _build_adapter(args)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE_ERROR

    try:
        with adapter:
            vectors = list(adapter.iter_vectors(sample_size=args.sample_size))
    except StoreConnectionError as exc:
        logger.error("%s", exc)
        return EXIT_STORE_CONNECTION_ERROR

    documents = [(v.id, v.content) for v in vectors if v.content]
    if not documents:
        logger.error("No document content available to generate from; the store carries no text content.")
        return EXIT_USAGE_ERROR

    try:
        pairs = generate_queries(documents, args.queries_per_doc)
    except GenerationError as exc:
        logger.error("%s", exc)
        return EXIT_USAGE_ERROR

    if not pairs:
        logger.error("Generation produced no queries.")
        return EXIT_USAGE_ERROR

    metadata = {
        "store": args.store,
        "index_or_table": getattr(args, "index", None) or getattr(args, "table", None),
        "queries_per_doc": args.queries_per_doc,
        "documents_sampled": len(documents),
    }
    Path(args.output).write_text(to_csv(pairs, metadata), encoding="utf-8")
    message = f"Wrote {len(pairs)} synthetic queries from {len(documents)} document(s) to {args.output}"
    logger.info(message)
    print(message)
    return EXIT_SUCCESS


_DISPATCH = {
    "audit-config": cmd_audit_config,
    "audit-content": cmd_audit_content,
    "evaluate": cmd_evaluate,
    "generate-ground-truth": cmd_generate_ground_truth,
}


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, validate, and dispatch to the chosen subcommand."""
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args, parser)
    handler = _DISPATCH[args.command]
    return handler(args)


def _configure_logging() -> None:
    """Configure root logging once. INFO to stderr, plain format."""
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


if __name__ == "__main__":
    sys.exit(main())
