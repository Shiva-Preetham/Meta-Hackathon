"""A small validator for the strict JSON-schema subset we send to Spark.

We validate model output ourselves (instead of trusting strict mode) because a gateway
should not assume the server enforced the schema. The subset covers what strict mode allows
and what our schemas use: object, array, string (with enum), boolean, integer, number.
"""

from __future__ import annotations

from typing import Any

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Return a list of human-readable errors; empty means valid."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected:
        py_type = _TYPES[expected]
        ok = isinstance(instance, py_type)
        # bool is a subclass of int in Python; JSON does not agree.
        if expected in ("integer", "number") and isinstance(instance, bool):
            ok = False
        if not ok:
            return [f"{path}: expected {expected}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']}")
    if expected == "object":
        props: dict[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, sub in props.items():
            if key in instance:
                errors.extend(validate(instance[key], sub, f"{path}.{key}"))
    if expected == "array" and "items" in schema:
        for i, element in enumerate(instance):
            errors.extend(validate(element, schema["items"], f"{path}[{i}]"))
    return errors


def strict_mode_violations(schema: dict[str, Any], path: str = "$", depth: int = 1) -> list[str]:
    """Check a schema against the strict structured-output rules in CLAUDE.md §3."""
    problems: list[str] = []
    if depth > 10:
        problems.append(f"{path}: nesting deeper than 10 levels")
    for banned in ("allOf", "oneOf", "anyOf"):
        if banned in schema:
            problems.append(f"{path}: '{banned}' is not allowed")
    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties must be false")
        props = schema.get("properties", {})
        missing = set(props) - set(schema.get("required", []))
        if missing:
            problems.append(f"{path}: properties not listed in required: {sorted(missing)}")
        for key, sub in props.items():
            problems.extend(strict_mode_violations(sub, f"{path}.{key}", depth + 1))
    if schema.get("type") == "array" and "items" in schema:
        problems.extend(strict_mode_violations(schema["items"], f"{path}[]", depth + 1))
    return problems
