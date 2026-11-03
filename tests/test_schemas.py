import pytest

from signalhq.domain.schemas import ENRICH_BATCH_SCHEMA, ENRICH_SCHEMA, THEME_SCHEMA
from signalhq.llm.validation import strict_mode_violations, validate


@pytest.mark.parametrize("schema", [ENRICH_SCHEMA, ENRICH_BATCH_SCHEMA, THEME_SCHEMA])
def test_schemas_follow_strict_mode_rules(schema):
    assert strict_mode_violations(schema) == []


def test_strict_mode_checker_catches_problems():
    bad = {"type": "object", "properties": {"a": {"type": "string"}}, "oneOf": []}
    problems = strict_mode_violations(bad)
    assert any("oneOf" in p for p in problems)
    assert any("additionalProperties" in p for p in problems)
    assert any("required" in p for p in problems)


VALID = {
    "type": "bug",
    "product_area": "playback",
    "severity": "high",
    "sentiment": "negative",
    "churn_signal": False,
    "key_quote": "it stops",
    "language": "en",
}


def test_validator_accepts_valid_enrichment():
    assert validate(VALID, ENRICH_SCHEMA) == []


def test_validator_reports_enum_missing_extra_and_type_errors():
    bad = dict(VALID, type="rant", churn_signal="yes", extra=1)
    del bad["language"]
    errors = validate(bad, ENRICH_SCHEMA)
    joined = " | ".join(errors)
    assert "not one of" in joined
    assert "expected boolean" in joined
    assert "unexpected property 'extra'" in joined
    assert "missing required property 'language'" in joined


def test_validator_does_not_treat_bool_as_integer():
    schema = {"type": "integer"}
    assert validate(True, schema) != []
    assert validate(3, schema) == []


def test_validator_walks_arrays():
    payload = {"items": [dict(VALID, id=1), dict(VALID, id="x")]}
    errors = validate(payload, ENRICH_BATCH_SCHEMA)
    assert errors == ["$.items[1].id: expected integer, got str"]
