"""Minimal JSON Schema subset for the contracts in this tree; standard library only.

Supports exactly the keywords used by contracts/*.v1.json:
type (string or list), required, properties, enum, items, additionalProperties.
"""

TYPES = {"object": dict, "array": list, "string": str}


def _matches(name, value):
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    return isinstance(value, TYPES[name])


def validate(schema, value, path="$"):
    errors = []
    _check(schema, value, path, errors)
    return errors


def _check(schema, value, path, errors):
    if not isinstance(schema, dict):
        return
    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not any(_matches(name, value) for name in names):
            errors.append("%s: expected %s, got %r" % (path, names, type(value).__name__))
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: %r not in enum" % (path, value))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: missing %s" % (path, key))
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                _check(properties[key], item, path + "." + key, errors)
            elif extra is False:
                errors.append("%s: unexpected key %s" % (path, key))
            elif isinstance(extra, dict):
                _check(extra, item, path + "." + key, errors)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _check(schema["items"], item, "%s[%d]" % (path, index), errors)
