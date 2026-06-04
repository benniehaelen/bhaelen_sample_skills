"""Structural SQL analysis built on ``sqlglot``.

Operating rule 3 in SKILL.md: parse, do not pattern-match, when deciding
statement type, joins, projected columns, and WHERE predicates. This module
turns a raw statement string into a ``ParsedStatement`` that the control
modules read. Comments and string literals are handled by the parser, so a
``DROP TABLE`` mentioned inside a string literal or comment does not trip a
control on its own (the comment-injection control scans raw text separately
and deliberately).

The only third-party dependency in the skill's hot path lives here. Control
modules, scoring, and the renderer are all pure Python over the
``ParsedStatement`` shape, so they need neither ``sqlglot`` nor BigQuery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError

# Read-only statement roots. Everything else is a write/DDL/DCL statement and
# fails SQ-001.
_READ_ONLY = (exp.Select, exp.Union, exp.Subquery)

# Map a non-read-only expression class name to a coarse statement type label.
_TYPE_BY_CLASS = {
    "Insert": "INSERT",
    "Update": "UPDATE",
    "Delete": "DELETE",
    "Merge": "MERGE",
    "Create": "CREATE",
    "Drop": "DROP",
    "Alter": "ALTER",
    "AlterTable": "ALTER",
    "AlterColumn": "ALTER",
    "TruncateTable": "TRUNCATE",
    "Truncate": "TRUNCATE",
    "Grant": "GRANT",
    "Revoke": "REVOKE",
}


@dataclass
class TableRef:
    """One base table reference as written in the statement."""

    name: str
    db: str = ""
    catalog: str = ""
    alias: str = ""

    @property
    def written(self) -> str:
        """The dotted form as it appears in the SQL (project.dataset.table or less)."""
        parts = [p for p in (self.catalog, self.db, self.name) if p]
        return ".".join(parts)

    @property
    def part_count(self) -> int:
        return len([p for p in (self.catalog, self.db, self.name) if p])

    @property
    def is_fully_qualified(self) -> bool:
        return self.part_count >= 3


@dataclass
class ParsedStatement:
    """Structural facts about one statement, consumed by the control modules."""

    raw: str
    statement_type: str = "UNKNOWN"
    parse_error: str = ""
    statement_count: int = 1
    tables: list[TableRef] = field(default_factory=list)
    select_star: bool = False
    projected_columns: set[str] = field(default_factory=set)
    where_columns: set[str] = field(default_factory=set)
    has_where: bool = False
    has_limit: bool = False
    has_order_by: bool = False
    is_aggregation: bool = False
    join_count: int = 0
    cartesian_evidence: str = ""

    @property
    def ok(self) -> bool:
        return not self.parse_error

    @property
    def is_read_only_select(self) -> bool:
        return self.statement_type == "SELECT"

    def references(self, table_id: str) -> bool:
        """True if a base table is written exactly as ``table_id`` (case-insensitive)."""
        target = table_id.lower()
        return any(t.written.lower() == target for t in self.tables)

    def where_constrains(self, column: str) -> bool:
        """True if ``column`` appears in any WHERE predicate (case-insensitive)."""
        return (column or "").lower() in self.where_columns


# ----- statement splitting --------------------------------------------------


def split_statements(text: str) -> list[str]:
    """Split a SQL file on ``;`` at statement boundaries.

    String literals (single/double quoted, with doubled-quote escapes) and
    comments (``--`` line, ``/* */`` block) are skipped so a semicolon inside
    them does not split a statement. Empty / comment-only segments are
    dropped. Used for ``--sql-file`` only: a single ``--sql`` string and each
    usage-log row are treated as one statement so SQ-002 can catch stacking.
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            buf.append(ch)
            i += 1
            while i < n and text[i] != "\n":
                buf.append(text[i])
                i += 1
            continue
        if ch == "/" and nxt == "*":
            buf.append(ch)
            buf.append(nxt)
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                buf.append(text[i])
                i += 1
            if i < n:
                buf.append("*")
                buf.append("/")
                i += 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(text[i])
                if text[i] == quote:
                    if i + 1 < n and text[i + 1] == quote:
                        buf.append(text[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == ";":
            segment = "".join(buf)
            if _has_sql(segment):
                out.append(segment.strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1

    tail = "".join(buf)
    if _has_sql(tail):
        out.append(tail.strip())
    return out


def _has_sql(segment: str) -> bool:
    """True if the segment contains anything beyond whitespace and comments."""
    stripped = _strip_comments(segment).strip()
    return bool(stripped)


def _strip_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ----- parsing --------------------------------------------------------------


def parse_statement(sql: str, dialect: str = "bigquery") -> ParsedStatement:
    """Parse one statement string into a ``ParsedStatement``.

    A parse failure is captured on the result (``parse_error``) rather than
    raised, so a batch can keep going; the CLI still exits 2 when any
    statement fails to parse, per the SKILL's exit-code contract.
    """
    raw = sql or ""
    try:
        expressions = sqlglot.parse(raw, read=dialect)
    except (ParseError, SqlglotError) as exc:
        return ParsedStatement(raw=raw, parse_error=_clean_error(str(exc)))
    except Exception as exc:  # noqa: BLE001  defensive: odd inputs can raise other errors
        return ParsedStatement(raw=raw, parse_error=_clean_error(str(exc)))

    expressions = [e for e in expressions if e is not None]
    if not expressions:
        return ParsedStatement(raw=raw, parse_error="empty statement")

    stmt = ParsedStatement(raw=raw, statement_count=len(expressions))
    expr = expressions[0]
    stmt.statement_type = _statement_type(expr)

    cte_names = {
        (cte.alias_or_name or "").lower()
        for cte in expr.find_all(exp.CTE)
    }
    stmt.tables = _collect_tables(expr, cte_names)

    selects = list(expr.find_all(exp.Select))
    stmt.select_star = _has_star_projection(selects)
    stmt.projected_columns = _projected_columns(selects)
    stmt.where_columns, stmt.has_where, where_has_col_eq = _where_facts(expr)
    stmt.has_limit = expr.find(exp.Limit) is not None
    stmt.has_order_by = expr.find(exp.Order) is not None
    stmt.is_aggregation = _is_aggregation(expr, selects)
    stmt.join_count, stmt.cartesian_evidence = _join_facts(expr, where_has_col_eq)

    return stmt


def _clean_error(message: str) -> str:
    """Single-line, trimmed parser error suitable for evidence."""
    flat = " ".join((message or "").split())
    return flat[:200]


def _statement_type(expr: exp.Expression) -> str:
    if isinstance(expr, _READ_ONLY):
        return "SELECT"
    if isinstance(expr, exp.Command):
        kw = str(expr.this or "").upper().strip()
        return kw or "COMMAND"
    cls = type(expr).__name__
    return _TYPE_BY_CLASS.get(cls, cls.upper())


def _collect_tables(expr: exp.Expression, cte_names: set[str]) -> list[TableRef]:
    seen: set[tuple[str, str, str]] = set()
    out: list[TableRef] = []
    for tbl in expr.find_all(exp.Table):
        name = tbl.name or ""
        # Skip references to CTEs defined in this statement; they are not base tables.
        if name.lower() in cte_names and not tbl.args.get("db"):
            continue
        ref = TableRef(
            name=name,
            db=tbl.text("db"),
            catalog=tbl.text("catalog"),
            alias=tbl.alias or "",
        )
        key = (ref.catalog.lower(), ref.db.lower(), ref.name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _has_star_projection(selects: list[exp.Select]) -> bool:
    """True for a bare ``*`` or ``t.*`` projection, but not ``COUNT(*)``.

    A star nested inside a function call (an aggregate like ``COUNT(*)``) is
    not a wildcard projection and must not trip SQ-005.
    """
    for sel in selects:
        for proj in sel.expressions:
            target = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(target, exp.Star):
                return True
            if isinstance(target, exp.Column) and isinstance(target.this, exp.Star):
                return True
    return False


def _projected_columns(selects: list[exp.Select]) -> set[str]:
    cols: set[str] = set()
    for sel in selects:
        for proj in sel.expressions:
            for col in proj.find_all(exp.Column):
                if col.name:
                    cols.add(col.name.lower())
    return cols


def _where_facts(expr: exp.Expression) -> tuple[set[str], bool, bool]:
    """Collect WHERE columns, whether any WHERE exists, and a col=col equality flag."""
    cols: set[str] = set()
    has_where = False
    has_col_eq = False
    for where in expr.find_all(exp.Where):
        has_where = True
        for col in where.find_all(exp.Column):
            if col.name:
                cols.add(col.name.lower())
        for eq in where.find_all(exp.EQ):
            if isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column):
                has_col_eq = True
    return cols, has_where, has_col_eq


def _is_aggregation(expr: exp.Expression, selects: list[exp.Select]) -> bool:
    """True if the outermost query aggregates (GROUP BY or a pure-aggregate projection)."""
    root = expr if isinstance(expr, exp.Select) else (selects[0] if selects else None)
    if root is None:
        return False
    if root.args.get("group"):
        return True
    projections = root.expressions
    if not projections:
        return False
    saw_agg = False
    for proj in projections:
        if proj.find(exp.AggFunc) is not None:
            saw_agg = True
        elif proj.find(exp.Column) is not None:
            # A plain column alongside aggregates means it is not a pure aggregation.
            return False
    return saw_agg


def _join_facts(expr: exp.Expression, where_has_col_eq: bool) -> tuple[int, str]:
    """Return ``(join_count, cartesian_evidence)``.

    ``cartesian_evidence`` is empty when every join is safe. A CROSS JOIN
    always fails. A predicate-less / comma join fails unless the statement
    carries a column-to-column equality in WHERE (the old-style join form).
    """
    joins = list(expr.find_all(exp.Join))
    problems: list[str] = []
    for join in joins:
        kind = (join.args.get("kind") or "").upper()
        side = (join.args.get("side") or "").upper()
        on = join.args.get("on")
        using = join.args.get("using")
        if kind == "NATURAL" or side == "NATURAL":
            continue
        if on is not None or using:
            continue
        # Predicate-less join. sqlglot normalizes a comma join (``FROM a, b``)
        # into a CROSS join, so both an explicit CROSS JOIN and an implicit
        # comma join land here. Either is tolerated only when the statement
        # carries a column-to-column equality in WHERE (the old-style join).
        if where_has_col_eq:
            continue
        problems.append("CROSS JOIN" if kind == "CROSS" else "join without ON/USING predicate")
    evidence = ""
    if problems:
        unique = sorted(set(problems))
        evidence = "; ".join(unique)
    return len(joins), evidence
