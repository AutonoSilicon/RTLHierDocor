"""Shared tool-calling helpers for LLM backends.

This module isolates tool-call serialization/execution so the backend focuses on
chat completion orchestration.
"""

import json
import inspect
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union


ToolCallback = Callable[[str, Dict[str, Any]], Union[str, Awaitable[str]]]


def _dump_tool_calls(tool_calls: Any) -> List[Dict[str, Any]]:
    """Serialize tool calls to plain dict payloads for chat history."""
    try:
        return [tc.model_dump() for tc in tool_calls]
    except Exception:
        payload: List[Dict[str, Any]] = []
        for tc in tool_calls:
            payload.append(
                {
                    "id": getattr(tc, "id", ""),
                    "type": getattr(tc, "type", "function"),
                    "function": {
                        "name": getattr(getattr(tc, "function", None), "name", ""),
                        "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
                    },
                }
            )
        return payload


def _parse_tool_args(raw_args: str) -> Dict[str, Any]:
    """Parse tool argument JSON safely with resilient fallbacks."""
    try:
        args_dict = json.loads(raw_args) if raw_args else {}
        if not isinstance(args_dict, dict):
            args_dict = {"_args": args_dict}
        return args_dict
    except Exception:
        return {"_raw": raw_args}


async def build_tool_round_messages(
    assistant_message: Any,
    tool_callback: Optional[ToolCallback],
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Build chat messages for one tool round.

    Returns:
        (has_tool_calls, messages_to_append)
    """
    tool_calls = getattr(assistant_message, "tool_calls", None)
    if not tool_calls:
        return False, []

    append_messages: List[Dict[str, Any]] = []

    append_messages.append(
        {
            "role": "assistant",
            "content": assistant_message.content or "",
            "tool_calls": _dump_tool_calls(tool_calls),
        }
    )

    for tc in tool_calls:
        tc_id = getattr(tc, "id", "")
        fn = getattr(tc, "function", None)
        tool_name = getattr(fn, "name", "") if fn else ""
        raw_args = getattr(fn, "arguments", "") if fn else ""
        args_dict = _parse_tool_args(raw_args)

        try:
            tool_result = tool_callback(tool_name, args_dict) if tool_callback else ""
            if inspect.isawaitable(tool_result):
                tool_result = await tool_result
        except Exception as e:
            tool_result = f"Error: tool '{tool_name}' failed: {e}"

        append_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": tool_result or "",
            }
        )

    return True, append_messages
