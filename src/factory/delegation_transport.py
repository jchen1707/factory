"""Host endpoint for one invocation's bounded delegation mailbox.

The controller supplies both directories and the broker identity. The inbox is a
private writable sandbox mount; the outbox MUST be a separate read-only mount.
Neither directory may be beneath candidate-writable host ancestors. This endpoint
never launches a child or grants the worker database/approval access.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from factory.agent.app_server_worker import (
    MAILBOX_LIMIT,
    MAILBOX_RESPONSE_LIMIT,
    read_mailbox,
    write_mailbox,
)
from factory.agent.base import SchemaInvalid
from factory.delegation import DelegationBroker


def refusal() -> dict[str, Any]:
    # Never echo host paths, authority files or untrusted argument text.
    return {
        "success": False,
        "contentItems": [
            {
                "type": "inputText",
                "text": (
                    "Delegation request refused; use a new call for an intentional retry. "
                    "Paths must be canonical repository-relative paths (use . for the "
                    "repository root), never absolute paths or host context directories."
                ),
            }
        ],
    }


def configuration(schema: dict[str, Any], inbox: Path, outbox: Path) -> dict[str, Any]:
    """Build registration from host-owned schema and mount paths."""
    handle_schema = {
        "type": "object",
        "properties": {"handle": {"type": "string"}},
        "required": ["handle"],
        "additionalProperties": False,
    }
    return {
        "inbox": str(inbox),
        "outbox": str(outbox),
        "tools": [
            {
                "type": "function",
                "name": name,
                "description": description,
                "inputSchema": schema,
            }
            for name, description, schema in (
                (
                    "factory_request_child",
                    "Request a child; returns a pending handle, not launch approval.",
                    schema,
                ),
                (
                    "factory_child_status",
                    "Inspect an owned child request and its result.",
                    handle_schema,
                ),
                (
                    "factory_cancel_child",
                    "Request cancellation of an owned child.",
                    handle_schema,
                ),
            )
        ],
    }


class DelegationMailbox:
    def __init__(self, broker: DelegationBroker, inbox: Path, outbox: Path) -> None:
        self.broker = broker
        self.inbox = inbox
        self.outbox = outbox

    def configuration(self) -> dict[str, Any]:
        """Host preparation freezes this with launch inputs; paths must be mounted as documented."""
        return configuration(self.broker.request_schema(), self.inbox, self.outbox)

    def service(self) -> bool:
        """One controller tick. Persist request before replying; safe to replay after a crash."""
        try:
            raw = read_mailbox(self.inbox, "request.json")
        except FileNotFoundError:
            return False
        digest = hashlib.sha256(raw).hexdigest()
        try:
            call = json.loads(raw)
            result = self.dispatch(call)
        except (ValueError, TypeError, KeyError, SchemaInvalid):
            result = refusal()
        response = json.dumps({"request_sha256": digest, "result": result}, allow_nan=False)
        write_mailbox(self.outbox, "response.json", response.encode(), limit=MAILBOX_RESPONSE_LIMIT)
        return True

    def dispatch(self, call: object) -> dict[str, Any]:
        """Only task request, owned inspection and cancellation are worker operations."""
        if not isinstance(call, dict) or set(call) != {"call_id", "tool", "arguments"}:
            raise ValueError("invalid delegation envelope")
        call_id, tool, arguments = call["call_id"], call["tool"], call["arguments"]
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 128:
            raise ValueError("invalid delegation call identity")
        encoded = json.dumps(call, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > MAILBOX_LIMIT:
            raise ValueError("delegation message exceeds size limit")
        store = self.broker.store
        with store.runtime.transaction():
            parent = store.runtime.invocation(self.broker.parent_id)
            if parent is None:
                raise ValueError("unknown delegation parent")
            owner = (parent["run_id"], parent["attempt"], parent["id"], "delegation-tool", call_id)
            old = store.find_effect(*owner)
            if old is not None:
                if old.status != "confirmed" or old.external_id is None:
                    raise ValueError("delegation response requires reconciliation")
                retained = json.loads(old.external_id)
                if retained["call"] != encoded or retained["source_root"] != str(
                    self.broker.source_root
                ):
                    raise ValueError("delegation call is immutable")
                return retained["result"]
            try:
                result = self._dispatch(tool, call_id, arguments)
            except (ValueError, TypeError, KeyError, SchemaInvalid, OSError):
                result = refusal()
            store.intend_effect(*owner)
            store.confirm_effect(
                *owner,
                json.dumps(
                    {
                        "call": encoded,
                        "source_root": str(self.broker.source_root),
                        "result": result,
                    },
                    allow_nan=False,
                ),
            )
            return result

    def _dispatch(self, tool: object, call_id: str, arguments: object) -> dict[str, Any]:
        if tool == "factory_request_child":
            record = self.broker.request(call_id, arguments)
        elif tool in ("factory_child_status", "factory_cancel_child"):
            if (
                not isinstance(arguments, dict)
                or set(arguments) != {"handle"}
                or not isinstance(arguments["handle"], str)
                or len(arguments["handle"]) != 32
            ):
                raise ValueError("invalid delegation handle")
            record = (
                self.broker.inspect(arguments["handle"])
                if tool == "factory_child_status"
                else self.broker.cancel(arguments["handle"])
            )
        else:
            raise ValueError("unknown delegation tool")
        return {
            "success": True,
            "contentItems": [
                {
                    "type": "inputText",
                    "text": json.dumps(
                        {
                            "handle": record["id"],
                            "status": record["status"],
                            "result": record["result"],
                            **({"waiting": record["waiting"]} if "waiting" in record else {}),
                        },
                        allow_nan=False,
                    ),
                }
            ],
        }
