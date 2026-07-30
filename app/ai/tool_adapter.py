"""Convert and validate Jarvis tools for OpenAI Responses API."""

from dataclasses import asdict
import json
from typing import Any

from app.tools.registry import ToolRegistry
from app.tools.result import ToolResult

MAX_TOOL_OUTPUT_BYTES = 65_536


class ToolCallValidationError(Exception):
    """A safe local validation failure for a model-generated tool call."""

    code = "invalid_tool_arguments"


class UnknownToolCallError(ToolCallValidationError):
    code = "unknown_tool"


class ToolAdapter:
    """Expose registered tools as strict Responses API function tools."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def schemas(self) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tool in self.registry.list_tools():
            parameters = tool.parameters()
            self._validate_schema(parameters)
            schemas.append(
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": parameters,
                    "strict": True,
                }
            )
        return schemas

    def parse_and_validate(
        self, tool_name: str, raw_arguments: str
    ) -> dict[str, Any]:
        try:
            tool = self.registry.get(tool_name)
        except KeyError as error:
            raise UnknownToolCallError("Tool is not registered") from error
        try:
            arguments = json.loads(raw_arguments)
        except (json.JSONDecodeError, TypeError) as error:
            raise ToolCallValidationError(
                "Tool arguments are not valid JSON"
            ) from error
        if not isinstance(arguments, dict):
            raise ToolCallValidationError("Tool arguments must be an object")

        schema = tool.parameters()
        properties = schema["properties"]
        required = set(schema["required"])
        unknown = set(arguments) - set(properties)
        missing = required - set(arguments)
        if unknown:
            raise ToolCallValidationError("Unknown tool argument")
        if missing:
            raise ToolCallValidationError("Required tool argument is missing")
        for name, value in arguments.items():
            expected = properties[name].get("type")
            if expected == "string":
                if not isinstance(value, str) or not value.strip():
                    raise ToolCallValidationError(
                        "Tool argument must be a non-empty string"
                    )
            elif expected == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ToolCallValidationError(
                        "Tool argument has an invalid type"
                    )
            elif expected == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ToolCallValidationError(
                        "Tool argument has an invalid type"
                    )
            elif expected == "boolean" and not isinstance(value, bool):
                raise ToolCallValidationError(
                    "Tool argument has an invalid type"
                )
        try:
            tool.validate_arguments(arguments)
        except Exception as error:
            validation_error = ToolCallValidationError(
                "Tool arguments are not allowed"
            )
            validation_error.code = getattr(
                error, "code", "invalid_tool_arguments"
            )
            raise validation_error from error
        return arguments

    @staticmethod
    def _validate_schema(schema: dict[str, Any]) -> None:
        if (
            schema.get("type") != "object"
            or not isinstance(schema.get("properties"), dict)
            or not isinstance(schema.get("required"), list)
            or schema.get("additionalProperties") is not False
        ):
            raise ValueError("Tool schema must be a strict object schema")
        properties = schema["properties"]
        required = schema["required"]
        if (
            len(required) != len(set(required))
            or set(required) != set(properties)
        ):
            raise ValueError(
                "Strict tool schema must require every declared property"
            )
        for property_schema in properties.values():
            if not isinstance(property_schema, dict):
                raise ValueError("Tool property schema must be an object")
            if property_schema.get("type") == "object":
                ToolAdapter._validate_schema(property_schema)


def serialize_tool_result(
    result: ToolResult, *, max_bytes: int = MAX_TOOL_OUTPUT_BYTES
) -> str:
    """Serialize only the safe public ToolResult fields as compact JSON."""
    safe_result = {
        key: value
        for key, value in asdict(result).items()
        if key in {"success", "tool", "data", "message", "error"}
    }
    encoded = json.dumps(
        safe_result, ensure_ascii=False, separators=(",", ":")
    )
    if len(encoded.encode("utf-8")) > max_bytes:
        encoded = json.dumps(
            {
                "success": False,
                "tool": result.tool,
                "data": {},
                "message": "Tool output exceeded the safe size limit.",
                "error": "tool_output_too_large",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return encoded
