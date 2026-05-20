"""Unit tests for the 15 rule modules.

Each rule is exercised against a small synthetic tool covering its pass,
partial, fail, and (where applicable) N/A branches. Rules that read the
precomputed ``tool["tokens"]`` block get a synthetic tokens dict so the
tests do not depend on tiktoken. Rules that tokenize on the fly (via
``count_in``) are marked ``needs_tiktoken``.
"""

from __future__ import annotations

import importlib.util

import pytest

from _rules import (
    boilerplate,
    concise_parameter_names,
    description_length,
    destructive_action_annotated,
    enum_values_documented,
    every_param_has_description,
    has_when_not_to_use,
    has_when_to_use,
    name_is_unique_in_catalog,
    name_is_verb_oriented,
    no_overlap_with_siblings,
    param_format_specified,
    redundant_examples,
    schema_compact,
    units_specified,
)

_HAS_TIKTOKEN = importlib.util.find_spec("tiktoken") is not None
needs_tiktoken = pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")


def make_tool(*, name="do_thing", description="Does the thing.", schema=None,
              annotations=None, tokens=None):
    """Build a synthetic tool dict with a tokens block attached."""
    return {
        "name": name,
        "description": description,
        "inputSchema": schema or {},
        "annotations": annotations or {},
        "tokens": tokens or {"name": 2, "description": 5, "schema": 0, "annotations": 0, "total": 7},
    }


def catalog(*tools):
    return {"server": {}, "tools": list(tools), "extras": {}}


# ----- Tier 1: description_length_appropriate ------------------------------


def test_description_length_pass_in_band(rubric):
    tool = make_tool(tokens={"description": 50})
    r = description_length.check(tool, catalog(), rubric)
    assert (r.points, r.max, r.passed) == (2, 2, True)


def test_description_length_partial_over_band(rubric):
    tool = make_tool(tokens={"description": 220})
    r = description_length.check(tool, catalog(), rubric)
    assert r.points == 1 and not r.passed
    assert r.tokens_saved == 220 - 150  # measured excess over good_max


def test_description_length_fail_bloated(rubric):
    tool = make_tool(tokens={"description": 400})
    r = description_length.check(tool, catalog(), rubric)
    assert r.points == 0
    assert r.tokens_saved == 400 - 150


def test_description_length_partial_under_band(rubric):
    tool = make_tool(tokens={"description": 15})
    r = description_length.check(tool, catalog(), rubric)
    assert r.points == 1
    assert r.tokens_saved == 0  # under-description costs the router, not the catalog


def test_description_length_fail_severely_under(rubric):
    tool = make_tool(tokens={"description": 4})
    r = description_length.check(tool, catalog(), rubric)
    assert r.points == 0


# ----- Tier 1: no_boilerplate_opener ---------------------------------------


def test_boilerplate_pass(rubric):
    tool = make_tool(description="Searches the invoice catalog by status.")
    r = boilerplate.check(tool, catalog(), rubric)
    assert r.passed and r.points == 2


@needs_tiktoken
def test_boilerplate_fail_hard_opener(rubric):
    tool = make_tool(description="This tool allows you to search invoices.")
    r = boilerplate.check(tool, catalog(), rubric)
    assert r.points == 0
    assert r.tokens_saved > 0  # tokens in the detected boilerplate phrase


@needs_tiktoken
def test_boilerplate_partial_soft_opener(rubric):
    tool = make_tool(description="This retrieves the current user record.")
    r = boilerplate.check(tool, catalog(), rubric)
    assert r.points == 1


# ----- Tier 1: schema_compact ----------------------------------------------


def test_schema_compact_pass(rubric):
    tool = make_tool(tokens={"schema": 100})
    r = schema_compact.check(tool, catalog(), rubric)
    assert r.passed and r.points == 2


def test_schema_compact_partial(rubric):
    tool = make_tool(tokens={"schema": 300})
    r = schema_compact.check(tool, catalog(), rubric)
    assert r.points == 1 and r.tokens_saved == 300 - 200


def test_schema_compact_fail(rubric):
    tool = make_tool(tokens={"schema": 600})
    r = schema_compact.check(tool, catalog(), rubric)
    assert r.points == 0 and r.tokens_saved == 600 - 200


# ----- Tier 1: no_redundant_examples ---------------------------------------


def test_redundant_examples_pass_no_examples(rubric):
    tool = make_tool(schema={"type": "object", "properties": {}})
    r = redundant_examples.check(tool, catalog(), rubric)
    assert r.passed


def test_redundant_examples_pass_not_duplicated(rubric):
    tool = make_tool(
        description="Searches the catalog.",
        schema={"type": "object", "examples": ["unrelated sample value"]},
    )
    r = redundant_examples.check(tool, catalog(), rubric)
    assert r.passed


@needs_tiktoken
def test_redundant_examples_fail_duplicated(rubric):
    tool = make_tool(
        description="Searches the catalog. For example pass query foobar widget here.",
        schema={"type": "object", "properties": {"q": {"default": "foobar widget"}}},
    )
    r = redundant_examples.check(tool, catalog(), rubric)
    assert r.points == 0 and r.tokens_saved > 0


# ----- Tier 1: concise_parameter_names -------------------------------------


@needs_tiktoken
def test_concise_names_pass(rubric):
    tool = make_tool(schema={"type": "object", "properties": {"customer_id": {}, "status": {}}})
    r = concise_parameter_names.check(tool, catalog(), rubric)
    assert r.passed


@needs_tiktoken
def test_concise_names_partial_one_over(rubric):
    tool = make_tool(schema={"type": "object", "properties": {
        "id": {}, "the_fully_qualified_resource_identifier_string": {}}})
    r = concise_parameter_names.check(tool, catalog(), rubric)
    assert r.points == 1


@needs_tiktoken
def test_concise_names_fail_multiple_over(rubric):
    tool = make_tool(schema={"type": "object", "properties": {
        "the_fully_qualified_resource_identifier_string": {},
        "the_secondary_optional_filter_expression_value": {}}})
    r = concise_parameter_names.check(tool, catalog(), rubric)
    assert r.points == 0 and r.tokens_saved > 0


# ----- Tier 2: has_when_to_use ---------------------------------------------


def test_has_when_to_use_pass(rubric):
    tool = make_tool(description="Searches invoices. Use this when you need to find matches.")
    r = has_when_to_use.check(tool, catalog(), rubric)
    assert r.passed and r.points == 2


def test_has_when_to_use_partial(rubric):
    tool = make_tool(description="Searches invoices if you need to find matches.")
    r = has_when_to_use.check(tool, catalog(), rubric)
    assert r.points == 1 and r.estimated


def test_has_when_to_use_fail(rubric):
    tool = make_tool(description="Returns a list of invoices.")
    r = has_when_to_use.check(tool, catalog(), rubric)
    assert r.points == 0 and r.estimated


# ----- Tier 2: has_when_not_to_use (conditional) ---------------------------


def test_has_when_not_to_use_na_without_siblings(rubric):
    tool = make_tool(name="search_invoices", description="Searches invoices.")
    r = has_when_not_to_use.check(tool, catalog(tool), rubric)
    assert r.n_a and r.max == 0


def test_has_when_not_to_use_pass_with_siblings(rubric):
    a = make_tool(name="get_user", description="Fetch a user. Do not use for bulk reads; use list_users instead.")
    b = make_tool(name="fetch_user", description="Fetch a user record by id.")
    r = has_when_not_to_use.check(a, catalog(a, b), rubric)
    assert r.passed and r.points == 2


def test_has_when_not_to_use_fail_with_siblings(rubric):
    a = make_tool(name="get_user", description="Fetch a user record.")
    b = make_tool(name="fetch_user", description="Fetch a user record by id.")
    r = has_when_not_to_use.check(a, catalog(a, b), rubric)
    assert r.points == 0 and r.estimated


# ----- Tier 2: no_overlap_with_siblings ------------------------------------


def test_no_overlap_pass_distinct(rubric):
    a = make_tool(name="create_invoice", description="Creates a brand new billing invoice for a customer account.")
    b = make_tool(name="search_orders", description="Finds purchase orders matching delivery status filters.")
    r = no_overlap_with_siblings.check(a, catalog(a, b), rubric)
    assert r.passed


def test_no_overlap_fail_overlapping(rubric):
    desc = "Fetch a single user record by their unique identifier from the directory service."
    a = make_tool(name="get_user", description=desc)
    b = make_tool(name="fetch_user", description=desc)
    r = no_overlap_with_siblings.check(a, catalog(a, b), rubric)
    assert r.points < 2 and r.estimated


# ----- Tier 2: param_format_specified (conditional) ------------------------


def test_param_format_na_no_applicable(rubric):
    tool = make_tool(schema={"type": "object", "properties": {"name": {"type": "string"}}})
    r = param_format_specified.check(tool, catalog(), rubric)
    assert r.n_a


def test_param_format_pass(rubric):
    tool = make_tool(schema={"type": "object", "properties": {
        "customer_id": {"type": "string", "description": "Customer id, UUID v4 format."}}})
    r = param_format_specified.check(tool, catalog(), rubric)
    assert r.passed


def test_param_format_fail(rubric):
    tool = make_tool(schema={"type": "object", "properties": {
        "customer_id": {"type": "string", "description": "The customer."}}})
    r = param_format_specified.check(tool, catalog(), rubric)
    assert r.points == 0 and r.estimated


# ----- Tier 2: destructive_action_annotated (conditional) ------------------


def test_destructive_na_read_only(rubric):
    tool = make_tool(name="get_user", description="Fetch a user record.")
    r = destructive_action_annotated.check(tool, catalog(), rubric)
    assert r.n_a


def test_destructive_pass_annotation_and_description(rubric):
    tool = make_tool(name="create_invoice", description="Creates a new invoice record.",
                     annotations={"destructiveHint": True})
    r = destructive_action_annotated.check(tool, catalog(), rubric)
    assert r.passed and r.points == 2


def test_destructive_partial_description_only(rubric):
    tool = make_tool(name="create_invoice", description="Creates a new invoice record.", annotations={})
    r = destructive_action_annotated.check(tool, catalog(), rubric)
    assert r.points == 1


def test_destructive_fail_neither(rubric):
    tool = make_tool(name="delete_account", description="Tidies up an account.", annotations={})
    r = destructive_action_annotated.check(tool, catalog(), rubric)
    assert r.points == 0


# ----- Tier 3: name_is_verb_oriented ---------------------------------------


def test_name_verb_pass(rubric):
    r = name_is_verb_oriented.check(make_tool(name="create_invoice"), catalog(), rubric)
    assert r.passed


def test_name_verb_partial_nominal(rubric):
    r = name_is_verb_oriented.check(make_tool(name="invoice_creator"), catalog(), rubric)
    assert r.points == 1


def test_name_verb_fail_noun(rubric):
    r = name_is_verb_oriented.check(make_tool(name="invoice"), catalog(), rubric)
    assert r.points == 0


# ----- Tier 3: name_is_unique_in_catalog -----------------------------------


def test_name_unique_pass(rubric):
    a = make_tool(name="create_invoice")
    b = make_tool(name="search_orders")
    r = name_is_unique_in_catalog.check(a, catalog(a, b), rubric)
    assert r.passed


def test_name_unique_fail_near_synonym(rubric):
    a = make_tool(name="get_user")
    b = make_tool(name="fetch_user")
    r = name_is_unique_in_catalog.check(a, catalog(a, b), rubric)
    assert r.points == 0


# ----- Tier 3: every_param_has_description ---------------------------------


def test_every_param_pass(rubric):
    tool = make_tool(schema={"type": "object", "properties": {
        "a": {"description": "First."}, "b": {"description": "Second."}}})
    r = every_param_has_description.check(tool, catalog(), rubric)
    assert r.passed


def test_every_param_partial_one_missing(rubric):
    tool = make_tool(schema={"type": "object", "properties": {
        "a": {"description": "First."}, "b": {}}})
    r = every_param_has_description.check(tool, catalog(), rubric)
    assert r.points == 1


def test_every_param_fail_two_missing(rubric):
    tool = make_tool(schema={"type": "object", "properties": {"a": {}, "b": {}}})
    r = every_param_has_description.check(tool, catalog(), rubric)
    assert r.points == 0


# ----- Tier 3: enum_values_documented (conditional) ------------------------


def test_enum_na_no_enums(rubric):
    tool = make_tool(schema={"type": "object", "properties": {"a": {"type": "string"}}})
    r = enum_values_documented.check(tool, catalog(), rubric)
    assert r.n_a


def test_enum_pass_with_glosses(rubric):
    tool = make_tool(
        description="State: pending (not yet sent), shipped (in transit), delivered (received).",
        schema={"type": "object", "properties": {"status": {"enum": ["pending", "shipped", "delivered"]}}},
    )
    r = enum_values_documented.check(tool, catalog(), rubric)
    assert r.passed


def test_enum_fail_not_documented(rubric):
    tool = make_tool(
        description="Filters by state.",
        schema={"type": "object", "properties": {"status": {"enum": ["pending", "shipped", "delivered"]}}},
    )
    r = enum_values_documented.check(tool, catalog(), rubric)
    assert r.points == 0


# ----- Tier 3: units_specified (conditional) -------------------------------


def test_units_na_no_applicable(rubric):
    tool = make_tool(schema={"type": "object", "properties": {"count": {"type": "integer"}}})
    r = units_specified.check(tool, catalog(), rubric)
    assert r.n_a


def test_units_pass(rubric):
    tool = make_tool(schema={"type": "object", "properties": {
        "amount": {"type": "integer", "description": "Charge in USD cents."}}})
    r = units_specified.check(tool, catalog(), rubric)
    assert r.passed


def test_units_fail(rubric):
    tool = make_tool(schema={"type": "object", "properties": {
        "amount": {"type": "integer", "description": "The charge."}}})
    r = units_specified.check(tool, catalog(), rubric)
    assert r.points == 0
