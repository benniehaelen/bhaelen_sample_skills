"""Unit tests for the CLI scaffold.

Verifies that the parser builds, each subcommand parses its required flags,
help works, and invalid flag combinations are rejected with the usage exit
code. The subcommand bodies are stubs, so valid invocations return success.
"""

from __future__ import annotations

import pytest

import lint


# ----- help and dispatch ---------------------------------------------------


def test_top_level_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        lint.main(["--help"])
    assert exc.value.code == 0


@pytest.mark.parametrize(
    "command", ["audit-config", "audit-content", "evaluate", "generate-ground-truth"]
)
def test_subcommand_help_exits_zero(command):
    with pytest.raises(SystemExit) as exc:
        lint.main([command, "--help"])
    assert exc.value.code == 0


def test_no_command_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        lint.main([])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


# ----- valid invocations (stubs return success) ----------------------------


def _validates_ok(argv):
    """Parse argv and run cross-flag validation, returning the namespace.

    Exercises the Task 1.3 surface (a valid store and selector combination
    passes validation) without dispatching, which now does real work.
    """
    parser = lint.build_parser()
    args = parser.parse_args(argv)
    lint.validate_args(args, parser)  # raises SystemExit on an invalid combination
    return args


def test_audit_config_pinecone_validates():
    args = _validates_ok(["audit-config", "--store", "pinecone", "--index", "kb"])
    assert args.store == "pinecone" and args.index == "kb"


def test_audit_config_bigquery_validates():
    args = _validates_ok(["audit-config", "--store", "bigquery", "--table", "proj.ds.embeddings"])
    assert args.store == "bigquery" and args.table == "proj.ds.embeddings"


def test_audit_config_pgvector_validates():
    args = _validates_ok(
        ["audit-config", "--store", "pgvector",
         "--connection-string", "postgresql://u@h/db", "--table", "embeddings"]
    )
    assert args.store == "pgvector" and args.table == "embeddings"


def test_audit_content_sample_validates():
    args = _validates_ok(
        ["audit-content", "--store", "pinecone", "--index", "kb", "--sample-size", "5000"]
    )
    assert args.sample_size == 5000 and args.full is False


def test_evaluate_validates():
    args = _validates_ok(
        ["evaluate", "--store", "pinecone", "--index", "kb",
         "--ground-truth", "queries.csv", "--k", "5,10"]
    )
    assert args.command == "evaluate" and args.ground_truth == "queries.csv"


def test_generate_ground_truth_validates():
    args = _validates_ok(
        ["generate-ground-truth", "--store", "pinecone", "--index", "kb", "--output", "q.csv"]
    )
    assert args.command == "generate-ground-truth" and args.output == "q.csv"


# ----- store / selector validation -----------------------------------------


def test_pinecone_requires_index():
    with pytest.raises(SystemExit) as exc:
        lint.main(["audit-config", "--store", "pinecone"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


def test_pinecone_forbids_connection_string():
    with pytest.raises(SystemExit) as exc:
        lint.main(["audit-config", "--store", "pinecone", "--index", "kb",
                   "--connection-string", "postgresql://u@h/db"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


def test_bigquery_requires_table():
    with pytest.raises(SystemExit) as exc:
        lint.main(["audit-config", "--store", "bigquery"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


def test_pgvector_requires_connection_string_and_table():
    # connection-string present but table missing.
    with pytest.raises(SystemExit) as exc:
        lint.main(["audit-config", "--store", "pgvector",
                   "--connection-string", "postgresql://u@h/db"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


# ----- sampling conflict ----------------------------------------------------


def test_full_and_sample_size_conflict():
    with pytest.raises(SystemExit) as exc:
        lint.main(["audit-content", "--store", "pinecone", "--index", "kb",
                   "--full", "--sample-size", "5000"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


def test_full_alone_validates():
    args = _validates_ok(["audit-content", "--store", "pinecone", "--index", "kb", "--full"])
    assert args.full is True


# ----- evaluate-specific validation -----------------------------------------


def test_evaluate_requires_ground_truth():
    with pytest.raises(SystemExit) as exc:
        lint.main(["evaluate", "--store", "pinecone", "--index", "kb"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


def test_evaluate_rejects_malformed_k():
    with pytest.raises(SystemExit) as exc:
        lint.main(["evaluate", "--store", "pinecone", "--index", "kb",
                   "--ground-truth", "q.csv", "--k", "5,foo,10"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


def test_evaluate_rejects_zero_k():
    with pytest.raises(SystemExit) as exc:
        lint.main(["evaluate", "--store", "pinecone", "--index", "kb",
                   "--ground-truth", "q.csv", "--k", "0,5"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


# ----- helpers --------------------------------------------------------------


def test_namespace_pinecone_validates():
    args = _validates_ok(["audit-config", "--store", "pinecone", "--index", "kb", "--namespace", "tenant-a"])
    assert args.namespace == "tenant-a"


def test_namespace_rejected_for_bigquery():
    with pytest.raises(SystemExit) as exc:
        lint.main(["audit-config", "--store", "bigquery", "--table", "p.d.t", "--namespace", "x"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


def test_build_adapter_passes_namespace_to_pinecone():
    parser = lint.build_parser()
    args = parser.parse_args(["audit-config", "--store", "pinecone", "--index", "kb", "--namespace", "tenant-a"])
    adapter = lint._build_adapter(args)
    assert adapter._namespace == "tenant-a"


def test_parse_k_values_sorted_unique():
    assert lint.parse_k_values("10,5,10,20") == [5, 10, 20]


def test_unknown_flag_is_usage_error():
    with pytest.raises(SystemExit) as exc:
        lint.main(["audit-config", "--store", "pinecone", "--index", "kb", "--bogus"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR
