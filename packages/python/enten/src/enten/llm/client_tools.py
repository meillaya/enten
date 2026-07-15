from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import tiktoken
from pydantic import BaseModel, ConfigDict, create_model

from enten.llm.models_pricing import PRICING
from enten.llm.types import Message, ToolSchema, ToolUse


class UnknownToolError(RuntimeError):
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"unknown tool: {tool_name}")


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> Decimal:
    prices = PRICING.get(model)
    if prices is None:
        return Decimal("0")
    input_cost = Decimal(tokens_in) * Decimal(str(prices["input"])) / Decimal("1000000")
    output_cost = Decimal(tokens_out) * Decimal(str(prices["output"])) / Decimal("1000000")
    return input_cost + output_cost


def count_message_tokens(messages: list[Message], model: str) -> int:
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    total = 0
    for message in messages:
        content = message.content
        if isinstance(content, str):
            total += len(encoding.encode(content))
        elif isinstance(content, list):
            for part in content:
                if part.text:
                    total += len(encoding.encode(part.text))
    return total


def tool_hash(tool: ToolUse) -> str:
    payload = json.dumps(
        {"name": tool.name, "args": tool.arguments},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_tool_call(tool: ToolUse, schemas: dict[str, ToolSchema]) -> None:
    try:
        schema = schemas[tool.name]
    except KeyError as exc:
        raise UnknownToolError(tool.name) from exc
    _schema_to_model(schema).model_validate(tool.arguments)


def _schema_to_model(schema: ToolSchema) -> type[BaseModel]:
    properties = schema.parameters.get("properties", {})
    required = set(schema.parameters.get("required", []))
    fields: dict[str, Any] = {}
    for name, prop in properties.items():
        field_type = _json_schema_type_to_py(prop)
        fields[name] = (field_type, ...) if name in required else (field_type | None, None)
    return create_model(
        f"ToolArgs_{schema.name}",
        __config__=ConfigDict(extra="ignore"),
        **fields,
    )


def _json_schema_type_to_py(prop: dict[str, Any]) -> type[Any]:
    type_name = prop.get("type", "string")
    if type_name == "string":
        return str
    if type_name == "integer":
        return int
    if type_name == "number":
        return float
    if type_name == "boolean":
        return bool
    if type_name == "array":
        return list[_json_schema_type_to_py(prop.get("items", {}))]
    if type_name == "object":
        return dict[str, Any]
    return str
