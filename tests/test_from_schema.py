"""Tests for the hypothesis-jsonschema library."""

import json
from pathlib import Path

import hypothesis.strategies as st
import jsonschema
import pytest
import strict_rfc3339
from hypothesis import HealthCheck, assume, given, note, reject, settings
from hypothesis.errors import InvalidArgument
from hypothesis.internal.reflection import proxies

from gen_schemas import schema_strategy_params
from hypothesis_jsonschema._canonicalise import canonicalish
from hypothesis_jsonschema._from_schema import from_schema, rfc3339


@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None)
@given(data=st.data())
@schema_strategy_params
def test_generated_data_matches_schema(schema_strategy, data):
    """Check that an object drawn from an arbitrary schema is valid."""
    schema = data.draw(schema_strategy)
    note(schema)
    try:
        value = data.draw(from_schema(schema), "value from schema")
    except InvalidArgument:
        reject()
    jsonschema.validate(value, schema)
    # This checks that our canonicalisation is semantically equivalent.
    jsonschema.validate(value, canonicalish(schema))


@given(from_schema(True))
def test_boolean_true_is_valid_schema_and_resolvable(_):
    """...even though it's currently broken in jsonschema."""


@pytest.mark.parametrize(
    "schema",
    [
        None,
        False,
        {"type": "an unknown type"},
        {"allOf": [{"type": "boolean"}, {"const": None}]},
        {"allOf": [{"type": "boolean"}, {"enum": [None]}]},
    ],
)
def test_invalid_schemas_raise(schema):
    """Trigger all the validation exceptions for full coverage."""
    with pytest.raises(Exception):
        from_schema(schema).example()


FLAKY_SCHEMAS = {
    # Yep, lists of lists of lists of lists of lists of integers are HealthCheck-slow
    "draft4/nested items",
    "draft7/nested items",
    "draft4/oneOf with missing optional property",
    "draft7/oneOf with missing optional property",
    # Something weird about a null that should be a string??  TODO: debug that.
    "Datalogic Scan2Deploy Android file",
    "Datalogic Scan2Deploy CE file",
    # Just not handling this one correctly yet
    "draft4/additionalProperties should not look in applicators",
    "draft7/additionalProperties should not look in applicators",
    "draft7/ECMA 262 regex escapes control codes with \\c and lower letter",
    "draft7/ECMA 262 regex escapes control codes with \\c and upper letter",
}

with open(Path(__file__).parent / "corpus-schemastore-catalog.json") as f:
    catalog = json.load(f)
with open(Path(__file__).parent / "corpus-suite-schemas.json") as f:
    suite, invalid_suite = json.load(f)
with open(Path(__file__).parent / "corpus-reported.json") as f:
    reported = json.load(f)
    assert set(reported).isdisjoint(suite)
    suite.update(reported)


def to_name_params(corpus):
    for n in sorted(corpus):
        if n.endswith("/oneOf complex types"):
            # oneOf on property names means only objects are valid,
            # but it's a very filter-heavy way to express that...
            # TODO: see if we can auto-detect this, fix it, and emit a warning.
            assert "type" not in corpus[n]
            corpus[n]["type"] = "object"
        if n in FLAKY_SCHEMAS:
            yield pytest.param(n, marks=pytest.mark.skip)
        else:
            yield n


def ref_fail_to_xfail(f):
    @proxies(f)
    def inner(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except jsonschema.exceptions.RefResolutionError:
            pytest.skip("Could not resolve a reference")

    return inner


@pytest.mark.skip
@pytest.mark.parametrize("name", to_name_params(catalog))
@settings(deadline=None, max_examples=5, suppress_health_check=HealthCheck.all())
@given(data=st.data())
@ref_fail_to_xfail
def test_can_generate_for_real_large_schema(data, name):
    note(name)
    value = data.draw(from_schema(catalog[name]))
    jsonschema.validate(value, catalog[name])


@pytest.mark.parametrize("name", to_name_params(suite))
@settings(
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    deadline=None,
    max_examples=20,
)
@given(data=st.data())
def test_can_generate_for_test_suite_schema(data, name):
    note(suite[name])
    value = data.draw(from_schema(suite[name]))
    try:
        jsonschema.validate(value, suite[name])
    except jsonschema.exceptions.SchemaError:
        jsonschema.Draft4Validator(suite[name]).validate(value)


@pytest.mark.parametrize("name", to_name_params(invalid_suite))
@ref_fail_to_xfail
def test_cannot_generate_for_empty_test_suite_schema(name):
    strat = from_schema(invalid_suite[name])
    with pytest.raises(Exception):
        strat.example()


# This schema has overlapping patternProperties - this is OK, so long as they're
# merged or otherwise handled correctly, with the exception of the key "ab" which
# would have to be both an integer and a string (and is thus disallowed).
OVERLAPPING_PATTERNS_SCHEMA = {
    "type": "string",
    "patternProperties": {
        r"\A[ab]{1,2}\Z": {},
        r"\Aa[ab]\Z": {"type": "integer"},
        r"\A[ab]b\Z": {"type": "string"},
    },
    "additionalProperties": False,
    "minimumProperties": 1,
}


@given(from_schema(OVERLAPPING_PATTERNS_SCHEMA))
def test_handles_overlapping_patternProperties(value):
    jsonschema.validate(value, OVERLAPPING_PATTERNS_SCHEMA)
    assert "ab" not in value


# A dictionary with zero or one keys, which was always empty due to a bug.
SCHEMA = {
    "type": "object",
    "properties": {"key": {"type": "string"}},
    "additionalProperties": False,
}


@given(from_schema(SCHEMA))
def test_single_property_can_generate_nonempty(query):
    # See https://github.com/Zac-HD/hypothesis-jsonschema/issues/25
    assume(query)


@given(rfc3339("date-time"))
def test_generated_rfc3339_datetime_strings_are_valid(datetime_string):
    assert strict_rfc3339.validate_rfc3339(datetime_string)


UNIQUE_NUMERIC_ARRAY_SCHEMA = {
    "type": "array",
    "uniqueItems": True,
    "items": {"enum": [0, 0.0]},
    "minItems": 1,
}


@given(from_schema(UNIQUE_NUMERIC_ARRAY_SCHEMA))
def test_numeric_uniqueness(value):
    # NOTE: this kind of test should usually be embedded in corpus-reported.json,
    # but in this case the type of the enum elements matter and we don't want to
    # allow a flexible JSON loader to mess things up.
    jsonschema.validate(value, UNIQUE_NUMERIC_ARRAY_SCHEMA)
