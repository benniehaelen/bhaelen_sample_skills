# SQL Guardrail Report

_Mode `cost_aware` · nl2sql-guardrail-default v1.0 (builtin) · source sql_file · `candidate_queries.sql`_
_Linted: 2026-06-03T16:20:00+00:00_

## Expectations
- **expect_no_violations_at** [fail] (limit high, actual 3): 3 statement(s) violate at or above high.

## Warnings
- Example report: byte estimates are illustrative, not from a live dry-run.

## Summary

- **Statements:** 4
- **Worst score:** 53/100
- **With violations at high+:** 3
- **Total estimated scan:** 55.00 GiB

## Top fixes

| Statement | Control | Severity | Impact | Fix |
| --- | --- | --- | --- | --- |
| `stmt_0003` | `SQ-009` | critical | blocks the statement | Add a zone filter on the tenant column, for example `AND coid = @caller_coid`. |
| `stmt_0002` | `SQ-009` | critical | blocks the statement | Add a zone filter on the tenant column, for example `AND coid = @caller_coid`. |
| `stmt_0002` | `SQ-010` | critical | blocks the statement | Drop the restricted column(s) patient_name, ssn, or add the caller/column to pii_allowlist if the access is justified. |
| `stmt_0004` | `SQ-001` | critical | blocks the statement | Rewrite as a read-only SELECT. Writes and DDL must go through a separate, reviewed path, not the NL2SQL plane. |
| `stmt_0004` | `SQ-009` | critical | blocks the statement | Add a zone filter on the tenant column, for example `AND coid = @caller_coid`. |
| `stmt_0002` | `SQ-008` | high | 33285996544 bytes saved | Prune the scan: add a partition filter, select fewer columns, or narrow the date range so the dry-run estimate drops below the ceiling. |
| `stmt_0003` | `SQ-004` | high | 12884901888 bytes saved | Add a partition filter on admit_date (for example `WHERE admit_date >= @from_date`) to prune the scan. |
| `stmt_0003` | `SQ-008` | high | 2147483648 bytes saved | Prune the scan: add a partition filter, select fewer columns, or narrow the date range so the dry-run estimate drops below the ceiling. |
| `stmt_0003` | `SQ-007` | high | high-severity violation | Give every join an explicit ON/USING predicate. Replace CROSS JOIN or comma joins with an equality on the join keys. |
| `stmt_0002` | `SQ-012` | low | hygiene issue | Pair the ORDER BY with a LIMIT, or drop the ORDER BY if the caller does not need a sorted top-N. |

## Statements (4)

### `stmt_0003` 53/100 (F) · SELECT

`SELECT a.encounter_id, b.facility_name FROM acme-prod.clinical.encounter a CROSS JOIN acme-prod.reference.facility b`

**Tables:** `acme-prod.clinical.encounter`, `acme-prod.reference.facility`
**Estimated scan:** 12.00 GiB
**Violations:** critical 1, high 3

**Tier 1**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-001` read_only_select | [pass] | critical | single read-only SELECT |  |
| `SQ-002` single_statement | [pass] | critical | exactly one statement |  |
| `SQ-003` no_comment_injection | [pass] | high | no comments or hints |  |

**Tier 2**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-004` partition_filter_present | [fail] | high | no WHERE predicate on partition column (admit_date) | Add a partition filter on admit_date (for example `WHERE admit_date >= @from_date`) to prune the scan. |
| `SQ-005` no_select_star | [pass] | medium | columns are enumerated |  |
| `SQ-006` bounded_result | [warn] | medium | non-aggregating query has no LIMIT | Add a LIMIT to bound the row stream, or aggregate. An unbounded scan returns the whole table. |
| `SQ-007` no_cartesian_join | [fail] | high | CROSS JOIN | Give every join an explicit ON/USING predicate. Replace CROSS JOIN or comma joins with an equality on the join keys. |
| `SQ-008` scan_within_ceiling | [fail] | high | 12.00 GiB exceeds ceiling 10.00 GiB | Prune the scan: add a partition filter, select fewer columns, or narrow the date range so the dry-run estimate drops below the ceiling. |

**Tier 3**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-009` tenant_predicate_present | [fail] | critical | no predicate on tenant column (coid) | Add a zone filter on the tenant column, for example `AND coid = @caller_coid`. |
| `SQ-010` pii_access_justified | [n/a] | critical | projection touches no restricted column |  |
| `SQ-011` fully_qualified_tables | [pass] | low | all base tables fully qualified |  |
| `SQ-012` order_by_bounded | [n/a] | low | no ORDER BY |  |

### `stmt_0002` 61/100 (D) · SELECT

`SELECT ssn, patient_name, admit_date FROM acme-prod.clinical.encounter WHERE admit_date >= '2026-05-01' ORDER BY admit_date`

**Tables:** `acme-prod.clinical.encounter`
**Estimated scan:** 41.00 GiB
**Violations:** critical 2, high 1, low 1

**Tier 1**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-001` read_only_select | [pass] | critical | single read-only SELECT |  |
| `SQ-002` single_statement | [pass] | critical | exactly one statement |  |
| `SQ-003` no_comment_injection | [pass] | high | no comments or hints |  |

**Tier 2**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-004` partition_filter_present | [pass] | high | partition column constrained (admit_date) |  |
| `SQ-005` no_select_star | [pass] | medium | columns are enumerated |  |
| `SQ-006` bounded_result | [warn] | medium | non-aggregating query has no LIMIT | Add a LIMIT to bound the row stream, or aggregate. An unbounded scan returns the whole table. |
| `SQ-007` no_cartesian_join | [pass] | high | no joins |  |
| `SQ-008` scan_within_ceiling | [fail] | high | 41.00 GiB exceeds ceiling 10.00 GiB | Prune the scan: add a partition filter, select fewer columns, or narrow the date range so the dry-run estimate drops below the ceiling. |

**Tier 3**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-009` tenant_predicate_present | [fail] | critical | no predicate on tenant column (coid) | Add a zone filter on the tenant column, for example `AND coid = @caller_coid`. |
| `SQ-010` pii_access_justified | [fail] | critical | restricted column projected: patient_name, ssn | Drop the restricted column(s) patient_name, ssn, or add the caller/column to pii_allowlist if the access is justified. |
| `SQ-011` fully_qualified_tables | [pass] | low | all base tables fully qualified |  |
| `SQ-012` order_by_bounded | [fail] | low | ORDER BY without LIMIT | Pair the ORDER BY with a LIMIT, or drop the ORDER BY if the caller does not need a sorted top-N. |

### `stmt_0004` 62/100 (D) · DELETE

`DELETE FROM acme-prod.clinical.encounter WHERE admit_date < '2020-01-01'`

**Tables:** `acme-prod.clinical.encounter`
**Violations:** critical 2

**Tier 1**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-001` read_only_select | [fail] | critical | DELETE statement is not read-only | Rewrite as a read-only SELECT. Writes and DDL must go through a separate, reviewed path, not the NL2SQL plane. |
| `SQ-002` single_statement | [pass] | critical | exactly one statement |  |
| `SQ-003` no_comment_injection | [pass] | high | no comments or hints |  |

**Tier 2**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-004` partition_filter_present | [pass] | high | partition column constrained (admit_date) |  |
| `SQ-005` no_select_star | [pass] | medium | columns are enumerated |  |
| `SQ-006` bounded_result | [warn] | medium | non-aggregating query has no LIMIT | Add a LIMIT to bound the row stream, or aggregate. An unbounded scan returns the whole table. |
| `SQ-007` no_cartesian_join | [pass] | high | no joins |  |
| `SQ-008` scan_within_ceiling | [n/a] | high | no dry-run estimate (static mode) |  |

**Tier 3**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-009` tenant_predicate_present | [fail] | critical | no predicate on tenant column (coid) | Add a zone filter on the tenant column, for example `AND coid = @caller_coid`. |
| `SQ-010` pii_access_justified | [n/a] | critical | projection touches no restricted column |  |
| `SQ-011` fully_qualified_tables | [pass] | low | all base tables fully qualified |  |
| `SQ-012` order_by_bounded | [n/a] | low | no ORDER BY |  |

### `stmt_0001` 100/100 (A) · SELECT

`SELECT coid, COUNT(*) AS encounters FROM acme-prod.clinical.encounter WHERE admit_date >= '2026-01-01' AND coid = @caller_coid GROUP BY coid`

**Tables:** `acme-prod.clinical.encounter`
**Estimated scan:** 2.00 GiB
**Violations:** none

**Tier 1**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-001` read_only_select | [pass] | critical | single read-only SELECT |  |
| `SQ-002` single_statement | [pass] | critical | exactly one statement |  |
| `SQ-003` no_comment_injection | [pass] | high | no comments or hints |  |

**Tier 2**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-004` partition_filter_present | [pass] | high | partition column constrained (admit_date) |  |
| `SQ-005` no_select_star | [pass] | medium | columns are enumerated |  |
| `SQ-006` bounded_result | [pass] | medium | aggregating query (bounded result shape) |  |
| `SQ-007` no_cartesian_join | [pass] | high | no joins |  |
| `SQ-008` scan_within_ceiling | [pass] | high | 2.00 GiB within ceiling 10.00 GiB |  |

**Tier 3**

| Control | Status | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| `SQ-009` tenant_predicate_present | [pass] | critical | tenant predicate present (coid) |  |
| `SQ-010` pii_access_justified | [n/a] | critical | projection touches no restricted column |  |
| `SQ-011` fully_qualified_tables | [pass] | low | all base tables fully qualified |  |
| `SQ-012` order_by_bounded | [n/a] | low | no ORDER BY |  |

---

_Methodology: This report answers whether a statement would be allowed through the control plane, not whether it is well written. The score is a weighted quality lens; the gate is driven by violation severity, because a single blocking violation should stop a statement regardless of how clean the rest of it is. The linter never executes the SQL it audits: byte estimates come from a dry-run only, and are na in static mode._
