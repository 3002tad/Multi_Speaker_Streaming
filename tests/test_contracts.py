from __future__ import annotations

import copy
import json
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PROJECT_ROOT / "contracts"
SCHEMA_ROOT = CONTRACT_ROOT / "schemas"
EXAMPLE_ROOT = CONTRACT_ROOT / "examples"


class ContractValidationError(AssertionError):
    pass


def _resolve_fragment(document: dict[str, Any], fragment: str) -> Any:
    value: Any = document
    if fragment in {"", "#"}:
        return value
    if not fragment.startswith("#/"):
        raise ContractValidationError(f"Unsupported JSON pointer: {fragment}")
    for raw_part in fragment[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    return value


def _matches_type(value: Any, expected: str) -> bool:
    mapping = {
        "null": lambda item: item is None,
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
    }
    return mapping[expected](value)


def validate_contract(
    value: Any,
    schema: dict[str, Any],
    *,
    root: dict[str, Any] | None = None,
    base_dir: Path = SCHEMA_ROOT,
    path: str = "$",
) -> None:
    """Validate the contract subset without adding a runtime dependency.

    Production services will use Pydantic-generated validation. This small
    test validator makes the checked-in JSON Schema fixtures executable in
    the current baseline venv, which intentionally has no jsonschema package.
    """
    root = root or schema
    if "$ref" in schema:
        reference = str(schema["$ref"])
        if reference.startswith("#"):
            target = _resolve_fragment(root, reference)
            validate_contract(value, target, root=root, base_dir=base_dir, path=path)
            return
        filename, separator, fragment = reference.partition("#")
        external_path = (base_dir / filename).resolve()
        external = json.loads(external_path.read_text(encoding="utf-8"))
        target = _resolve_fragment(external, f"#{fragment}" if separator else "")
        validate_contract(
            value,
            target,
            root=external,
            base_dir=external_path.parent,
            path=path,
        )
        return

    for item in schema.get("allOf", []):
        validate_contract(value, item, root=root, base_dir=base_dir, path=path)

    if "oneOf" in schema:
        matches = 0
        for item in schema["oneOf"]:
            try:
                validate_contract(value, item, root=root, base_dir=base_dir, path=path)
            except ContractValidationError:
                continue
            matches += 1
        if matches != 1:
            raise ContractValidationError(f"{path}: expected exactly one schema, got {matches}")

    if "const" in schema and value != schema["const"]:
        raise ContractValidationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractValidationError(f"{path}: {value!r} is not in enum")

    expected_types = schema.get("type")
    if expected_types is not None:
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        if not any(_matches_type(value, item) for item in expected_types):
            raise ContractValidationError(f"{path}: invalid type")

    if isinstance(value, dict):
        required = set(schema.get("required", []))
        missing = required - value.keys()
        if missing:
            raise ContractValidationError(f"{path}: missing {sorted(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = value.keys() - properties.keys()
            if unknown:
                raise ContractValidationError(f"{path}: unknown {sorted(unknown)}")
        for key, item in value.items():
            if key in properties:
                validate_contract(
                    item,
                    properties[key],
                    root=root,
                    base_dir=base_dir,
                    path=f"{path}.{key}",
                )

    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise ContractValidationError(f"{path}: too few items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ContractValidationError(f"{path}: duplicate items")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                validate_contract(
                    item,
                    item_schema,
                    root=root,
                    base_dir=base_dir,
                    path=f"{path}[{index}]",
                )

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise ContractValidationError(f"{path}: string too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ContractValidationError(f"{path}: string too long")
        if value and schema.get("format") == "uuid":
            try:
                uuid.UUID(value)
            except ValueError as exc:
                raise ContractValidationError(f"{path}: invalid UUID") from exc
        if value and schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ContractValidationError(f"{path}: invalid date-time") from exc
        if value and schema.get("format") == "uri":
            parsed = urlparse(value)
            if not parsed.scheme:
                raise ContractValidationError(f"{path}: invalid URI")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractValidationError(f"{path}: below minimum")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class ContractSchemaTests(unittest.TestCase):
    EXAMPLES = {
        "meeting-snapshot.json": "meeting-snapshot.schema.json",
        "ai-session.json": "ai-session.schema.json",
        "transcript-final-event.json": "ai-event.schema.json",
        "minutes-document.json": "minutes-document.schema.json",
    }

    def test_examples_validate(self) -> None:
        for example_name, schema_name in self.EXAMPLES.items():
            with self.subTest(example=example_name):
                validate_contract(
                    _load_json(EXAMPLE_ROOT / example_name),
                    _load_json(SCHEMA_ROOT / schema_name),
                )

    def test_missing_actor_is_rejected(self) -> None:
        value = _load_json(EXAMPLE_ROOT / "meeting-snapshot.json")
        value.pop("actor")
        with self.assertRaises(ContractValidationError):
            validate_contract(value, _load_json(SCHEMA_ROOT / "meeting-snapshot.schema.json"))

    def test_unknown_snapshot_field_is_rejected(self) -> None:
        value = _load_json(EXAMPLE_ROOT / "meeting-snapshot.json")
        value["database_id"] = 42
        with self.assertRaises(ContractValidationError):
            validate_contract(value, _load_json(SCHEMA_ROOT / "meeting-snapshot.schema.json"))

    def test_event_type_must_match_payload(self) -> None:
        value = _load_json(EXAMPLE_ROOT / "transcript-final-event.json")
        value["type"] = "transcript.retracted"
        with self.assertRaises(ContractValidationError):
            validate_contract(value, _load_json(SCHEMA_ROOT / "ai-event.schema.json"))

    def test_final_event_requires_external_runtime_id(self) -> None:
        value = _load_json(EXAMPLE_ROOT / "transcript-final-event.json")
        value["runtime_session_id"] = "paperless-demo"
        with self.assertRaises(ContractValidationError):
            validate_contract(value, _load_json(SCHEMA_ROOT / "ai-event.schema.json"))

    def test_minutes_evidence_rejects_unknown_field(self) -> None:
        value = copy.deepcopy(_load_json(EXAMPLE_ROOT / "minutes-document.json"))
        value["summary"][0]["hallucinated"] = True
        with self.assertRaises(ContractValidationError):
            validate_contract(value, _load_json(SCHEMA_ROOT / "minutes-document.schema.json"))

    def test_visible_minutes_item_requires_evidence(self) -> None:
        value = copy.deepcopy(_load_json(EXAMPLE_ROOT / "minutes-document.json"))
        value["summary"][0]["source_segment_ids"] = []
        with self.assertRaises(ContractValidationError):
            validate_contract(value, _load_json(SCHEMA_ROOT / "minutes-document.schema.json"))


class OpenApiContractTests(unittest.TestCase):
    EXPECTED_PATHS = {
        "meeting-service.openapi.yaml": {
            "/internal/v1/meetings/{meeting_id}/runtime",
            "/internal/v1/runtimes/{runtime_session_id}/stop",
            "/internal/v1/meetings/{meeting_id}/tokens",
            "/internal/v1/meetings/{meeting_id}/transcripts",
            "/internal/v1/meetings/{meeting_id}/minutes",
            "/internal/v1/meetings/{meeting_id}",
        },
        "meeting-ai.openapi.yaml": {
            "/internal/v1/sessions",
            "/internal/v1/sessions/{runtime_session_id}",
            "/internal/v1/sessions/{runtime_session_id}/stop",
            "/internal/v1/sessions/{runtime_session_id}/participants",
            "/internal/v1/sessions/{runtime_session_id}/analyze",
            "/internal/v1/agent/assignment",
            "/internal/v1/agent/status",
        },
    }

    def test_openapi_documents_have_locked_paths_and_unique_operations(self) -> None:
        for filename, expected_paths in self.EXPECTED_PATHS.items():
            with self.subTest(filename=filename):
                path = CONTRACT_ROOT / "openapi" / filename
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertEqual(document["openapi"], "3.1.0")
                self.assertEqual(document["info"]["version"], "1.0.0")
                self.assertTrue(expected_paths.issubset(document["paths"]))
                operation_ids = []
                for path_item in document["paths"].values():
                    for method, operation in path_item.items():
                        if method.lower() in {"get", "post", "put", "patch", "delete"}:
                            operation_ids.append(operation["operationId"])
                self.assertEqual(len(operation_ids), len(set(operation_ids)))

    def test_external_schema_references_exist(self) -> None:
        for path in (CONTRACT_ROOT / "openapi").glob("*.yaml"):
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            stack: list[Any] = [document]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    reference = item.get("$ref")
                    if isinstance(reference, str) and not reference.startswith("#"):
                        target = reference.split("#", 1)[0]
                        self.assertTrue((path.parent / target).resolve().is_file(), reference)
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)


class BaselineManifestTests(unittest.TestCase):
    def test_manifest_locks_actual_active_frontend(self) -> None:
        manifest = _load_json(PROJECT_ROOT / "baseline" / "manifest.json")
        self.assertEqual(manifest["source_commit"], "6a9fa12d3073c8cc2753d2f99f707716565ba5ee")
        self.assertEqual(manifest["pipeline"]["asr_frontend"], "legacy")
        self.assertEqual(manifest["pipeline"]["asr_enhancer"], "none")
        self.assertLess(manifest["regression"]["audio/truth.csv"]["mean_wer"], 0.10)
        self.assertLess(manifest["regression"]["audio/truth_1.csv"]["mean_wer"], 0.33)


if __name__ == "__main__":
    unittest.main()
