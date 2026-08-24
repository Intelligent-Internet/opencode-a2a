from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx


def _jsonrpc(method: str, params: dict[str, Any], request_id: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def _valid_message(message_id: str) -> dict[str, Any]:
    return {
        "message": {
            "messageId": message_id,
            "role": "ROLE_USER",
            "parts": [{"text": "conformance probe"}],
        }
    }


def _stream_payload(response: httpx.Response) -> dict[str, Any]:
    if response.headers.get("content-type", "").startswith("application/json"):
        return dict(response.json())
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return dict(json.loads(line.removeprefix("data:").strip()))
    raise AssertionError(
        f"stream response did not contain JSON data (status={response.status_code})"
    )


def _error_reason(payload: dict[str, Any]) -> str | None:
    details = payload.get("error", {}).get("details", [])
    if not details:
        details = payload.get("error", {}).get("data", [])
    for detail in details:
        if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
            return detail["reason"]
    return None


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def run_probes(base_url: str, bearer_token: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {bearer_token}"}

    def check(name: str, probe: Callable[[], None]) -> None:
        try:
            probe()
        except Exception as exc:  # noqa: BLE001 - each probe must be reported independently
            results.append({"name": name, "status": "failed", "detail": str(exc)})
        else:
            results.append({"name": name, "status": "passed"})

    with httpx.Client(base_url=base_url, headers=headers, timeout=20.0) as client:
        card_response = client.get("/.well-known/agent-card.json", headers={})
        card_response.raise_for_status()
        card = dict(card_response.json())

        def agent_card() -> None:
            interfaces = card.get("supportedInterfaces", [])
            bindings = {
                item.get("protocolBinding") for item in interfaces if isinstance(item, dict)
            }
            if not {"JSONRPC", "HTTP+JSON"}.issubset(bindings):
                raise AssertionError(f"missing advertised transports: {sorted(bindings)}")

        check("agent-card-transports", agent_card)

        def jsonrpc_empty_message() -> None:
            response = client.post("/", json=_jsonrpc("SendMessage", {}, 1))
            _assert_equal(response.status_code, 200, "HTTP status")
            _assert_equal(response.json().get("error", {}).get("code"), -32602, "error code")

        check("jsonrpc-empty-message-invalid-params", jsonrpc_empty_message)

        def rest_empty_message() -> None:
            response = client.post("/message:send", json={})
            _assert_equal(response.status_code, 400, "HTTP status")
            _assert_equal(
                response.json().get("error", {}).get("status"),
                "INVALID_ARGUMENT",
                "status",
            )

        check("rest-empty-message-invalid-argument", rest_empty_message)

        def jsonrpc_push_unsupported() -> None:
            response = client.post(
                "/",
                json=_jsonrpc("GetTaskPushNotificationConfig", {"id": "probe-task"}, 2),
            )
            payload = response.json()
            _assert_equal(payload.get("error", {}).get("code"), -32003, "error code")
            _assert_equal(_error_reason(payload), "PUSH_NOTIFICATION_NOT_SUPPORTED", "reason")

        check("jsonrpc-push-notification-not-supported", jsonrpc_push_unsupported)

        def rest_push_unsupported() -> None:
            response = client.get("/tasks/probe-task/pushNotificationConfigs/probe-config")
            payload = response.json()
            _assert_equal(response.status_code, 400, "HTTP status")
            _assert_equal(_error_reason(payload), "PUSH_NOTIFICATION_NOT_SUPPORTED", "reason")

        check("rest-push-notification-not-supported", rest_push_unsupported)

        task_id: str | None = None
        response = client.post("/", json=_jsonrpc("SendMessage", _valid_message("probe-1"), 3))
        if response.status_code == 200:
            result = response.json().get("result", {})
            task = result.get("task", result) if isinstance(result, dict) else {}
            if isinstance(task, dict) and isinstance(task.get("id"), str):
                task_id = task["id"]

        def jsonrpc_terminal_subscribe() -> None:
            if task_id is None:
                raise AssertionError("valid SendMessage did not return a terminal task")
            response = client.post(
                "/",
                json=_jsonrpc("SubscribeToTask", {"id": task_id}, 4),
                headers={**headers, "Accept": "text/event-stream"},
            )
            payload = _stream_payload(response)
            _assert_equal(payload.get("error", {}).get("code"), -32004, "error code")

        check("jsonrpc-terminal-task-subscribe-unsupported", jsonrpc_terminal_subscribe)

        def rest_terminal_subscribe() -> None:
            if task_id is None:
                raise AssertionError("valid SendMessage did not return a terminal task")
            response = client.get(
                f"/tasks/{task_id}:subscribe",
                headers={**headers, "Accept": "text/event-stream"},
            )
            payload = _stream_payload(response)
            _assert_equal(response.status_code, 400, "HTTP status")
            _assert_equal(_error_reason(payload), "UNSUPPORTED_OPERATION", "reason")

        check("rest-terminal-task-subscribe-unsupported", rest_terminal_subscribe)

        def list_tasks_transports() -> None:
            rpc = client.post("/", json=_jsonrpc("ListTasks", {}, 5))
            _assert_equal(rpc.status_code, 200, "JSON-RPC HTTP status")
            if "result" not in rpc.json():
                raise AssertionError("JSON-RPC ListTasks did not return a result")
            rest = client.get("/tasks")
            _assert_equal(rest.status_code, 200, "REST HTTP status")
            if "tasks" not in rest.json():
                raise AssertionError("REST ListTasks did not return tasks")

        check("list-tasks-both-transports", list_tasks_transports)

    return results, card


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repository-owned A2A compatibility probes")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    token = os.environ.get("CONFORMANCE_AUTH_TOKEN")
    if not token:
        parser.error("CONFORMANCE_AUTH_TOKEN is required")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results, card = run_probes(args.base_url.rstrip("/"), token)
    failures = [result for result in results if result["status"] == "failed"]
    report = {
        "schema_version": 1,
        "scope": "repository-owned-a2a-1.0-compatibility-probes",
        "sut_url": args.base_url,
        "repo_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "summary": {
            "passed": len(results) - len(failures),
            "failed": len(failures),
            "total": len(results),
        },
        "checks": results,
    }
    (args.output_dir / "agent-card.json").write_text(
        json.dumps(card, indent=2, sort_keys=True) + "\n"
    )
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )

    for result in results:
        marker = "PASS" if result["status"] == "passed" else "FAIL"
        detail = f": {result['detail']}" if "detail" in result else ""
        print(f"[{marker}] {result['name']}{detail}")
    print(f"Summary: {report['summary']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
