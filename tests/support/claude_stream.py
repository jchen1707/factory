from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from factory.agent.stream import FailureKind

Wire = dict[str, Any]

SESSION = "5b0c2a4e-8f7d-4c1a-9e3b-2d6f1a7c9e40"
MODEL = "claude-haiku-4-5-20251001"
TOOLS = ("Bash", "Edit", "Read", "Skill", "StructuredOutput", "Write")
BUILTIN_PLUGINS = ("cc-plugin-agents-md", "cc-plugin-telemetry", "cc-plugin-plugin-authoring")
TIMESTAMP = "2026-10-07T04:14:30.323Z"
ANSWER: Mapping[str, object] = {
    "status": "no_change_needed",
    "summary": "No code changes required for this task.",
    "files_changed": [],
    "tests_added": [],
    "out_of_scope": [],
    "behaviour_changed": False,
    "seam_confirmed": False,
    "tdd_used": False,
    "gates_run": [],
    "blocked_reason": None,
    "docs_updated": [],
}


class Scenario(NamedTuple):
    lines: list[str]
    exit_code: int | None
    stderr: str


def line(event: Wire) -> str:
    return json.dumps(event, separators=(",", ":"))


def lines(*events: Wire) -> list[str]:
    return [line(event) for event in events]


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def init(
    *,
    session: str = SESSION,
    model: str = MODEL,
    tools: Iterable[str] = TOOLS,
    plugin_sources: Mapping[str, str] | None = None,
    mcp_servers: Sequence[str] = (),
    permission_mode: str = "default",
) -> Wire:
    sources = (
        {name: f"{name}@builtin" for name in BUILTIN_PLUGINS}
        if plugin_sources is None
        else plugin_sources
    )
    return {
        "type": "system",
        "subtype": "init",
        "cwd": "/work/repo",
        "session_id": session,
        "tools": list(tools),
        "mcp_servers": [
            {"name": name, "status": "connected", "source": "claudeai"} for name in mcp_servers
        ],
        "model": model,
        "permissionMode": permission_mode,
        "slash_commands": [],
        "terminal_slash_commands": [],
        "apiKeySource": "none",
        "claude_code_version": "2.1.292",
        "output_style": "default",
        "agents": ["claude", "Explore", "general-purpose", "Plan"],
        "skills": [],
        "plugins": [
            {"name": name, "path": "builtin", "source": source} for name, source in sources.items()
        ],
        "capabilities": [],
        "analytics_disabled": False,
        "product_feedback_disabled": False,
        "uuid": str(uuid.uuid4()),
        "memory_paths": {"auto": "/home/agent/.claude/projects/-work-repo/memory/"},
        "messaging_socket_path": "/run/cc-socks/1.sock",
        "fast_mode_state": "off",
        "fast_mode_disabled_reason": "sdk_opt_in_required",
        "per_turn_effort_active": False,
        "view_mode": "default",
    }


def message_usage(
    *, input: int = 9, cache_read: int = 0, cache_write: int = 14967, output: int = 3
) -> Wire:
    return {
        "input_tokens": input,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": cache_write,
        },
        "output_tokens": output,
        "service_tier": "standard",
        "inference_geo": "not_available",
    }


def _assistant(content: list[Wire], *, session: str, model: str, usage: Wire | None) -> Wire:
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "id": _id("msg"),
            "type": "message",
            "role": "assistant",
            "content": content,
            "container": None,
            "stop_reason": None,
            "stop_sequence": None,
            "stop_details": None,
            "usage": usage or message_usage(),
            "input_transformations": [],
            "diagnostics": None,
            "context_management": None,
        },
        "parent_tool_use_id": None,
        "session_id": session,
        "uuid": str(uuid.uuid4()),
        "timestamp": TIMESTAMP,
        "request_id": _id("req"),
    }


def assistant_text(
    text: str, *, session: str = SESSION, model: str = MODEL, usage: Wire | None = None
) -> Wire:
    return _assistant([{"type": "text", "text": text}], session=session, model=model, usage=usage)


def assistant_tool_use(
    name: str,
    input: Mapping[str, object],
    *,
    tool_use_id: str | None = None,
    session: str = SESSION,
    model: str = MODEL,
    usage: Wire | None = None,
) -> Wire:
    use_id = tool_use_id or _id("toolu")
    block = {
        "type": "tool_use",
        "id": use_id,
        "name": name,
        "input": dict(input),
        "caller": {"type": "direct"},
    }
    event = _assistant([block], session=session, model=model, usage=usage)
    event["wire_tool_inputs"] = {use_id: dict(input)}
    return event


def assistant_api_error(text: str, error: str, *, session: str = SESSION) -> Wire:
    return {
        "type": "assistant",
        "message": {
            "diagnostics": None,
            "id": str(uuid.uuid4()),
            "container": None,
            "model": "<synthetic>",
            "role": "assistant",
            "stop_details": None,
            "stop_reason": "stop_sequence",
            "stop_sequence": "",
            "type": "message",
            "usage": {
                "output_tokens_details": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
                "service_tier": None,
                "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0},
                "inference_geo": None,
                "iterations": None,
                "speed": None,
                "fallback_credit": None,
            },
            "content": [{"type": "text", "text": text}],
            "context_management": None,
        },
        "parent_tool_use_id": None,
        "session_id": session,
        "uuid": str(uuid.uuid4()),
        "timestamp": TIMESTAMP,
        "error": error,
        "is_api_error_message": True,
    }


def tool_result(
    tool_use_id: str,
    content: str,
    *,
    is_error: bool | None = None,
    denied: bool = False,
    session: str = SESSION,
) -> Wire:
    block: Wire = {"tool_use_id": tool_use_id, "type": "tool_result", "content": content}
    if denied or is_error is not None:
        block["is_error"] = denied or bool(is_error)
    event: Wire = {
        "type": "user",
        "message": {"role": "user", "content": [block]},
        "parent_tool_use_id": None,
        "session_id": session,
        "uuid": str(uuid.uuid4()),
        "timestamp": TIMESTAMP,
        "tool_use_result": f"Error: {content}" if denied else content,
    }
    if denied:
        event["tool_result_meta"] = [{"id": tool_use_id, "non_execution_kind": "permission-rule"}]
    return event


def api_retry(
    attempt: int,
    *,
    status: int | None = 401,
    error: str = "authentication_failed",
    session: str = SESSION,
) -> Wire:
    return {
        "type": "system",
        "subtype": "api_retry",
        "attempt": attempt,
        "max_retries": 10,
        "retry_delay_ms": 500 * attempt,
        "error_status": status,
        "error": error,
        "session_id": session,
        "uuid": str(uuid.uuid4()),
    }


def permission_denied(
    tool: str,
    tool_use_id: str,
    *,
    reason: str = "no approval surface in this session; permission request denied automatically",
    session: str = SESSION,
) -> Wire:
    return {
        "type": "system",
        "subtype": "permission_denied",
        "tool_name": tool,
        "tool_use_id": tool_use_id,
        "decision_reason_type": "asyncAgent",
        "decision_reason": reason,
        "message": "Permission for this tool use was denied. The action was NOT performed.",
        "uuid": str(uuid.uuid4()),
        "session_id": session,
    }


def rate_limit(
    *,
    status: str = "allowed",
    five_hour: float = 0.44,
    seven_day: float = 0.11,
    resets_at: int = 1791358800,
    session: str = SESSION,
) -> Wire:
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "resetsAt": resets_at,
            "rateLimitType": "five_hour",
            "overageStatus": "rejected",
            "overageDisabledReason": "org_level_disabled",
            "isUsingOverage": False,
            "unifiedWindows": {
                "five_hour": {"utilization": five_hour, "resetsAt": resets_at},
                "seven_day": {"utilization": seven_day, "resetsAt": resets_at + 44400},
            },
        },
        "uuid": str(uuid.uuid4()),
        "session_id": session,
    }


def result_denial(tool: str, tool_use_id: str, tool_input: Mapping[str, object]) -> Wire:
    return {"tool_name": tool, "tool_use_id": tool_use_id, "tool_input": dict(tool_input)}


def _result_usage(*, input: int, cache_read: int, cache_write: int, output: int) -> Wire:
    return {
        "input_tokens": input,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output,
        "output_tokens_details": {"thinking_tokens": 0},
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "service_tier": "standard",
        "cache_creation": {
            "ephemeral_1h_input_tokens": cache_write,
            "ephemeral_5m_input_tokens": 0,
        },
        "inference_geo": "not_available",
        "iterations": [],
        "speed": "standard",
        "fallback_credit": None,
    }


def _model_usage(cost: float) -> Wire:
    return {
        "inputTokens": 9,
        "outputTokens": 339,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 14967,
        "webSearchRequests": 0,
        "costUSD": cost,
        "contextWindow": 200000,
        "maxOutputTokens": 32000,
        "thinkingTokens": 80,
        "canonicalModel": "claude-haiku-4-5",
        "provider": "firstParty",
        "costBasis": "list",
    }


def _subagent_stats() -> Wire:
    return {
        "spawned": 0,
        "requested": {"background": 0, "foreground": 0, "unset": 0},
        "started_in_background": 0,
        "max_depth": 0,
        "spawned_by_subagents": 0,
        "completed": 0,
        "failed": 0,
        "killed": {"parent": 0, "user": 0, "system": 0},
        "refused": {"depth_limit": 0, "concurrency_limit": 0, "budget": 0},
        "by_type": {},
    }


def _result(
    *,
    subtype: str,
    is_error: bool,
    num_turns: int,
    cost: float,
    model: str,
    session: str,
    denials: Sequence[Wire] = (),
) -> Wire:
    return {
        "type": "result",
        "subtype": subtype,
        "duration_ms": 2911,
        "duration_api_ms": 2791,
        "is_error": is_error,
        "num_turns": num_turns,
        "stop_reason": "end_turn",
        "session_id": session,
        "total_cost_usd": cost,
        "usage": _result_usage(input=9, cache_read=0, cache_write=14967, output=339),
        "modelUsage": {model: _model_usage(cost)} if cost else {},
        "permission_denials": list(denials),
        "fast_mode_state": "off",
        "fast_mode_disabled_reason": "sdk_opt_in_required",
        "subagent_stats": _subagent_stats(),
        "safety_stops": 0,
        "uuid": str(uuid.uuid4()),
        "queued_turn_count": 0,
        "result_index": 0,
    }


def result_success(
    structured_output: Mapping[str, object] | None = None,
    *,
    text: str | None = None,
    num_turns: int = 2,
    cost: float = 0.031638,
    denials: Sequence[Wire] = (),
    session: str = SESSION,
    model: str = MODEL,
) -> Wire:
    event = _result(
        subtype="success",
        is_error=False,
        num_turns=num_turns,
        cost=cost,
        model=model,
        session=session,
        denials=denials,
    )
    answer = json.dumps(structured_output, separators=(",", ":")) if structured_output else None
    event |= {
        "api_error_status": None,
        "terminal_reason": "completed",
        "result": text if text is not None else answer or "done",
        "ttft_ms": 1454,
        "ttft_stream_ms": 747,
        "time_to_request_ms": 111,
        "first_content_frame_ms": 748,
    }
    if structured_output is not None:
        event["structured_output"] = dict(structured_output)
    return event


_NOT_LOGGED_IN = "Not logged in · Please run /login"
_MODEL_UNKNOWN = (
    "There's an issue with the selected model ({model}). It may not exist or you may not have "
    "access to it. Run --model to pick a different model."
)


def result_error(kind: FailureKind, *, session: str = SESSION, model: str = MODEL) -> Wire:
    if kind is FailureKind.SESSION_LOST:
        return {
            "type": "result",
            "subtype": "error_during_execution",
            "duration_ms": 0,
            "duration_api_ms": 0,
            "is_error": True,
            "num_turns": 0,
            "stop_reason": None,
            "session_id": session,
            "total_cost_usd": 0,
            "usage": _result_usage(input=0, cache_read=0, cache_write=0, output=0),
            "modelUsage": {},
            "permission_denials": [],
            "uuid": str(uuid.uuid4()),
            "errors": [f"No conversation found with session ID: {session}"],
            "result_index": 0,
        }
    if kind in (FailureKind.AUTH, FailureKind.MODEL_UNAVAILABLE):
        # not-logged-in and model-unknown: subtype `success` with `is_error: true`.
        event = _result(
            subtype="success", is_error=True, num_turns=1, cost=0, model=model, session=session
        )
        auth = kind is FailureKind.AUTH
        return event | {
            "api_error_status": None if auth else 404,
            "terminal_reason": "api_error",
            "result": _NOT_LOGGED_IN if auth else _MODEL_UNKNOWN.format(model=model),
        }
    if kind is FailureKind.MAX_TURNS:
        event = _result(
            subtype="error_max_turns",
            is_error=True,
            num_turns=2,
            cost=0.0177634,
            model=model,
            session=session,
        )
        return event | {
            "terminal_reason": "max_turns",
            "errors": ["Reached maximum number of turns (1)"],
        }
    if kind is FailureKind.BUDGET:
        event = _result(
            subtype="error_max_budget_usd",
            is_error=True,
            num_turns=1,
            cost=0.0199633,
            model=model,
            session=session,
        )
        return event | {
            "terminal_reason": "budget_exhausted",
            "errors": ["Reached maximum budget ($0.001)"],
        }
    raise ValueError(f"no measured result shape for {kind}")


def success(
    structured_output: Mapping[str, object] = ANSWER, *, session: str = SESSION, model: str = MODEL
) -> Scenario:
    use_id = _id("toolu")
    return Scenario(
        lines(
            init(session=session, model=model),
            assistant_tool_use(
                "StructuredOutput",
                structured_output,
                tool_use_id=use_id,
                session=session,
                model=model,
            ),
            tool_result(use_id, "Structured output provided successfully", session=session),
            rate_limit(session=session),
            result_success(structured_output, session=session, model=model),
        ),
        0,
        "",
    )


def denial(
    *, path: str = "/work/repo/denied.txt", session: str = SESSION, model: str = MODEL
) -> Scenario:
    use_id = _id("toolu")
    write = {"file_path": path, "content": "x"}
    return Scenario(
        lines(
            init(session=session, model=model),
            assistant_tool_use("Write", write, tool_use_id=use_id, session=session, model=model),
            rate_limit(session=session),
            permission_denied("Write", use_id, session=session),
            tool_result(
                use_id, "Permission for this tool use was denied.", denied=True, session=session
            ),
            assistant_text("refused", session=session, model=model),
            result_success(
                text="refused",
                denials=[result_denial("Write", use_id, write)],
                session=session,
                model=model,
            ),
        ),
        0,
        "",
    )


def max_turns(*, session: str = SESSION, model: str = MODEL) -> Scenario:
    use_id = _id("toolu")
    return Scenario(
        lines(
            init(session=session, model=model),
            assistant_tool_use(
                "Bash",
                {"command": "echo hello-from-bash", "description": "Echo"},
                tool_use_id=use_id,
                session=session,
                model=model,
            ),
            rate_limit(session=session),
            tool_result(use_id, "hello-from-bash", is_error=False, session=session),
            result_error(FailureKind.MAX_TURNS, session=session, model=model),
        ),
        1,
        "",
    )


def budget(*, session: str = SESSION, model: str = MODEL) -> Scenario:
    return Scenario(
        lines(
            init(session=session, model=model),
            assistant_text("# Rivers", session=session, model=model),
            result_error(FailureKind.BUDGET, session=session, model=model),
        ),
        1,
        "",
    )


def not_logged_in(*, session: str = SESSION, model: str = MODEL) -> Scenario:
    return Scenario(
        lines(
            init(session=session, model=model),
            assistant_api_error(_NOT_LOGGED_IN, "authentication_failed", session=session),
            result_error(FailureKind.AUTH, session=session, model=model),
        ),
        1,
        "",
    )


def auth_retrying(*, retries: int = 2, session: str = SESSION, model: str = MODEL) -> Scenario:
    return Scenario(
        lines(
            init(session=session, model=model),
            *(api_retry(attempt, session=session) for attempt in range(1, retries + 1)),
        ),
        None,
        "",
    )


def truncated(*, session: str = SESSION, model: str = MODEL) -> Scenario:
    whole = success(session=session, model=model).lines
    return Scenario([*whole[:-1], whole[-1][: len(whole[-1]) // 2]], None, "")


def session_lost(*, session: str = SESSION, model: str = MODEL) -> Scenario:
    return Scenario(
        lines(result_error(FailureKind.SESSION_LOST, session=session, model=model)),
        1,
        f"No conversation found with session ID: {session}\n",
    )


def session_in_use(*, session: str = SESSION) -> Scenario:
    return Scenario([], 1, f"Error: Session ID {session} is already in use.\n")
