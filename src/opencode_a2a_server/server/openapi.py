from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI

from ..config import Settings
from ..contracts.extensions import (
    INTERRUPT_CALLBACK_METHODS,
    PROVIDER_DISCOVERY_METHODS,
    SESSION_QUERY_DEFAULT_LIMIT,
    SESSION_QUERY_METHODS,
    JsonRpcCapabilitySnapshot,
    build_capability_snapshot,
    build_compatibility_profile_params,
    build_interrupt_callback_extension_params,
    build_model_selection_extension_params,
    build_provider_discovery_extension_params,
    build_session_binding_extension_params,
    build_session_query_extension_params,
    build_streaming_extension_params,
    build_wire_contract_params,
)
from ..jsonrpc.application import SESSION_CONTEXT_PREFIX
from ..profile.runtime import RuntimeProfile


def _build_jsonrpc_extension_openapi_description(
    *,
    capability_snapshot: JsonRpcCapabilitySnapshot,
) -> str:
    session_methods = list(capability_snapshot.session_query_methods().values())
    provider_methods = ", ".join(sorted(PROVIDER_DISCOVERY_METHODS.values()))
    interrupt_methods = ", ".join(sorted(INTERRUPT_CALLBACK_METHODS.values()))
    return (
        "A2A JSON-RPC entrypoint. Supports core A2A methods "
        "(message/send, message/stream, tasks/get, tasks/cancel, tasks/resubscribe) "
        "plus shared model-selection metadata, OpenCode session/provider extensions, "
        "and shared interrupt callback methods.\n\n"
        f"OpenCode session query/control methods: {', '.join(session_methods)}.\n"
        f"OpenCode provider/model discovery methods: {provider_methods}.\n"
        f"Shared interrupt callback methods: {interrupt_methods}.\n\n"
        "Notification semantics: extension requests without JSON-RPC id return HTTP 204."
    )


def _build_jsonrpc_extension_openapi_examples(
    *,
    capability_snapshot: JsonRpcCapabilitySnapshot,
) -> dict[str, Any]:
    examples = {
        "message_send": {
            "summary": "Send message via JSON-RPC core method",
            "value": {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "msg-1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Explain what this repository does."}],
                    }
                },
            },
        },
        "message_stream": {
            "summary": "Stream message via JSON-RPC core method",
            "value": {
                "jsonrpc": "2.0",
                "id": 102,
                "method": "message/stream",
                "params": {
                    "message": {
                        "messageId": "msg-stream-1",
                        "role": "user",
                        "parts": [
                            {
                                "kind": "text",
                                "text": "Stream the answer and highlight key conclusions.",
                            }
                        ],
                    }
                },
            },
        },
        "message_send_model_override": {
            "summary": "Send message with shared model override",
            "value": {
                "jsonrpc": "2.0",
                "id": 103,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "msg-model-1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "Answer with the faster model."}],
                    },
                    "metadata": {
                        "shared": {
                            "model": {
                                "providerID": "google",
                                "modelID": "gemini-2.5-flash",
                            }
                        }
                    },
                },
            },
        },
        "message_send_file_input": {
            "summary": "Send message with text + file input",
            "value": {
                "jsonrpc": "2.0",
                "id": 104,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "msg-file-1",
                        "role": "user",
                        "parts": [
                            {
                                "kind": "text",
                                "text": "Review the attached file and summarize the main risks.",
                            },
                            {
                                "kind": "file",
                                "file": {
                                    "name": "report.pdf",
                                    "mimeType": "application/pdf",
                                    "uri": "file:///workspace/report.pdf",
                                },
                            },
                        ],
                    }
                },
            },
        },
        "session_list": {
            "summary": "List OpenCode sessions",
            "value": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": SESSION_QUERY_METHODS["list_sessions"],
                "params": {"limit": SESSION_QUERY_DEFAULT_LIMIT},
            },
        },
        "session_messages": {
            "summary": "List session messages",
            "value": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": SESSION_QUERY_METHODS["get_session_messages"],
                "params": {"session_id": "s-1", "limit": SESSION_QUERY_DEFAULT_LIMIT},
            },
        },
        "session_prompt_async": {
            "summary": "Send async prompt to an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 21,
                "method": SESSION_QUERY_METHODS["prompt_async"],
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "parts": [{"type": "text", "text": "Continue and summarize next steps."}]
                    },
                },
            },
        },
        "session_command": {
            "summary": "Send command to an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 22,
                "method": SESSION_QUERY_METHODS["command"],
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "command": "/review",
                        "arguments": "focus on security findings",
                    },
                },
            },
        },
        "providers_list": {
            "summary": "List available OpenCode providers",
            "value": {
                "jsonrpc": "2.0",
                "id": 24,
                "method": PROVIDER_DISCOVERY_METHODS["list_providers"],
                "params": {},
            },
        },
        "models_list": {
            "summary": "List available models for one provider",
            "value": {
                "jsonrpc": "2.0",
                "id": 25,
                "method": PROVIDER_DISCOVERY_METHODS["list_models"],
                "params": {"provider_id": "openai"},
            },
        },
        "permission_reply": {
            "summary": "Reply to permission interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 31,
                "method": INTERRUPT_CALLBACK_METHODS["reply_permission"],
                "params": {"request_id": "req-1", "reply": "once"},
            },
        },
        "question_reply": {
            "summary": "Reply to question interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 32,
                "method": INTERRUPT_CALLBACK_METHODS["reply_question"],
                "params": {"request_id": "req-2", "answers": [["answer"]]},
            },
        },
        "question_reject": {
            "summary": "Reject question interrupt request",
            "value": {
                "jsonrpc": "2.0",
                "id": 33,
                "method": INTERRUPT_CALLBACK_METHODS["reject_question"],
                "params": {"request_id": "req-3"},
            },
        },
    }
    if capability_snapshot.is_method_enabled(SESSION_QUERY_METHODS["shell"]):
        examples["session_shell"] = {
            "summary": "Run shell command in an existing session",
            "value": {
                "jsonrpc": "2.0",
                "id": 23,
                "method": SESSION_QUERY_METHODS["shell"],
                "params": {
                    "session_id": "s-1",
                    "request": {
                        "agent": "code-reviewer",
                        "command": "git status --short",
                    },
                },
            },
        }
    return examples


def _build_rest_message_openapi_examples() -> dict[str, Any]:
    return {
        "basic_message": {
            "summary": "Send a basic user message (HTTP+JSON)",
            "value": {
                "message": {
                    "messageId": "msg-rest-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Explain what this repository does."}],
                }
            },
        },
        "message_with_file_input": {
            "summary": "Send message with FilePart input (HTTP+JSON)",
            "value": {
                "message": {
                    "messageId": "msg-rest-file-1",
                    "role": "ROLE_USER",
                    "content": [
                        {"text": "Review the attached file and summarize the main risks."},
                        {
                            "file": {
                                "name": "report.pdf",
                                "mimeType": "application/pdf",
                                "uri": "file:///workspace/report.pdf",
                            }
                        },
                    ],
                }
            },
        },
        "continue_session": {
            "summary": "Continue a historical OpenCode session",
            "value": {
                "message": {
                    "messageId": "msg-rest-continue-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Continue previous work and summarize next steps."}],
                },
                "metadata": {
                    "shared": {
                        "session": {"id": "s-1"},
                    }
                },
            },
        },
        "message_with_model_override": {
            "summary": "Send message with shared model override",
            "value": {
                "message": {
                    "messageId": "msg-rest-model-1",
                    "role": "ROLE_USER",
                    "content": [{"text": "Answer with the faster model."}],
                },
                "metadata": {
                    "shared": {
                        "model": {
                            "providerID": "google",
                            "modelID": "gemini-2.5-flash",
                        }
                    }
                },
            },
        },
    }


def _patch_jsonrpc_openapi_contract(
    app: FastAPI,
    settings: Settings,
    *,
    runtime_profile: RuntimeProfile,
) -> None:
    session_binding = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    model_selection = build_model_selection_extension_params(
        runtime_profile=runtime_profile,
    )
    streaming = build_streaming_extension_params()
    session_query = build_session_query_extension_params(
        runtime_profile=runtime_profile,
        context_id_prefix=SESSION_CONTEXT_PREFIX,
    )
    provider_discovery = build_provider_discovery_extension_params(
        runtime_profile=runtime_profile,
    )
    interrupt_callback = build_interrupt_callback_extension_params(
        runtime_profile=runtime_profile,
    )
    compatibility_profile = build_compatibility_profile_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )
    wire_contract = build_wire_contract_params(
        protocol_version=settings.a2a_protocol_version,
        runtime_profile=runtime_profile,
    )
    capability_snapshot = build_capability_snapshot(runtime_profile=runtime_profile)
    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = original_openapi()
        paths = schema.get("paths")
        if isinstance(paths, dict):
            root_path = paths.get("/")
            if isinstance(root_path, dict):
                post = root_path.get("post")
                if isinstance(post, dict):
                    post["summary"] = "Handle A2A JSON-RPC Requests"
                    post["description"] = _build_jsonrpc_extension_openapi_description(
                        capability_snapshot=capability_snapshot,
                    )
                    post["x-a2a-extension-contracts"] = {
                        "session_binding": session_binding,
                        "model_selection": model_selection,
                        "streaming": streaming,
                        "session_query": session_query,
                        "provider_discovery": provider_discovery,
                        "interrupt_callback": interrupt_callback,
                        "compatibility_profile": compatibility_profile,
                        "wire_contract": wire_contract,
                    }

                    request_body = post.setdefault("requestBody", {})
                    if isinstance(request_body, dict):
                        content = request_body.setdefault("content", {})
                        if isinstance(content, dict):
                            app_json = content.setdefault("application/json", {})
                            if isinstance(app_json, dict):
                                app_json["examples"] = _build_jsonrpc_extension_openapi_examples(
                                    capability_snapshot=capability_snapshot,
                                )

            rest_post_contracts: dict[str, dict[str, Any]] = {
                "/v1/message:send": {
                    "summary": "Send Message (HTTP+JSON)",
                    "description": (
                        "A2A HTTP+JSON message send endpoint. "
                        "Use REST payload shape with message.content and ROLE_* roles."
                    ),
                    "schema_ref": "#/components/schemas/SendMessageRequest",
                },
                "/v1/message:stream": {
                    "summary": "Stream Message (HTTP+JSON)",
                    "description": (
                        "A2A HTTP+JSON streaming endpoint. "
                        "Use REST payload shape with message.content and ROLE_* roles."
                    ),
                    "schema_ref": "#/components/schemas/SendStreamingMessageRequest",
                },
            }
            rest_examples = _build_rest_message_openapi_examples()
            for rest_path, contract in rest_post_contracts.items():
                rest_path_item = paths.get(rest_path)
                if not isinstance(rest_path_item, dict):
                    continue
                rest_post = rest_path_item.get("post")
                if not isinstance(rest_post, dict):
                    continue

                rest_post["summary"] = contract["summary"]
                rest_post["description"] = contract["description"]
                request_body = rest_post.setdefault("requestBody", {})
                if not isinstance(request_body, dict):
                    continue
                request_body.setdefault("required", True)
                content = request_body.setdefault("content", {})
                if not isinstance(content, dict):
                    continue
                app_json = content.setdefault("application/json", {})
                if not isinstance(app_json, dict):
                    continue
                app_json["schema"] = {"$ref": contract["schema_ref"]}
                app_json["examples"] = rest_examples

        app.openapi_schema = schema
        return schema

    cast(Any, app).openapi = custom_openapi
