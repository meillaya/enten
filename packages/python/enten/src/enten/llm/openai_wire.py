from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from enten.llm.types import LLMDelta, Message, ToolSchema, ToolUse


def message_to_openai(message: Message) -> dict[str, Any]:
    content: str | list[dict[str, Any]]
    if isinstance(message.content, str):
        content = message.content
    else:
        content = []
        for part in message.content:
            if part.type == "text":
                content.append({"type": "text", "text": part.text or ""})
            elif part.type == "image":
                content.append({"type": "image_url", "image_url": {"url": part.image_url or ""}})

    result: dict[str, Any] = {"role": message.role, "content": content}
    if message.tool_call_id:
        result["tool_call_id"] = message.tool_call_id
    if message.name:
        result["name"] = message.name
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": tool.id,
                "type": "function",
                "function": {
                    "name": tool.name,
                    "arguments": json.dumps(tool.arguments, sort_keys=True),
                },
            }
            for tool in message.tool_calls
        ]
    return result


def tool_to_openai(tool: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def messages_text(messages: list[Message]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message.content, str):
            parts.append(message.content)
        else:
            parts.extend(part.text or "" for part in message.content)
    return "\n".join(parts)


def request_payload(
    messages: list[Message],
    tools: list[ToolSchema] | None,
    model: str,
    stream: bool,
    temperature: float,
    options: Mapping[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [message_to_openai(message) for message in messages],
        "stream": stream,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = [tool_to_openai(tool) for tool in tools]
    for key, value in options.items():
        body.setdefault(key, value)
    return body


def tool_calls_from_openai(raw_tool_calls: list[dict[str, Any]]) -> list[ToolUse]:
    tool_calls: list[ToolUse] = []
    for raw in raw_tool_calls:
        function = raw.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        tool_calls.append(
            ToolUse(
                id=raw.get("id", ""),
                name=function.get("name", ""),
                arguments=arguments,
            )
        )
    return tool_calls


def parse_stream_line(line: str) -> LLMDelta | None:
    if not line:
        return None
    payload = line[6:] if line.startswith("data: ") else line
    if payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choice = (data.get("choices") or [{}])[0]
    delta = choice.get("delta") or {}
    raw_tool_calls = delta.get("tool_calls")
    tool_calls = tool_calls_from_openai(raw_tool_calls) if raw_tool_calls else None
    return LLMDelta(content=delta.get("content") or "", tool_calls=tool_calls)
