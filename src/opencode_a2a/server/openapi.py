from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI

from ..config import Settings
from ..contracts.extensions import (
    INTERRUPT_CALLBACK_EXTENSION_URI,
    INTERRUPT_CALLBACK_METHODS,
    MODEL_SELECTION_EXTENSION_URI,
    SESSION_BINDING_EXTENSION_URI,
    STREAMING_EXTENSION_URI,
    build_interrupt_callback_extension_params,
    build_model_selection_extension_params,
    build_public_interrupt_callback_extension_params,
    build_public_session_binding_extension_params,
    build_public_streaming_extension_params,
    build_session_binding_extension_params,
    build_streaming_extension_params,
    select_public_extension_params,
)
from ..jsonrpc.models import JSONRPCRequest
from ..profile.runtime import RuntimeProfile


def _build_jsonrpc_extension_openapi_description() -> str:
    interrupt_methods = ", ".join(sorted(INTERRUPT_CALLBACK_METHODS.values()))
    return (
        "A2A JSON-RPC entrypoint. Supports core A2A methods "
        "(SendMessage, SendStreamingMessage, GetTask, CancelTask, SubscribeToTask) "
        "plus shared session binding, shared model-selection metadata, shared stream "
        "hints, and shared interrupt callback methods.\n\n"
        "Anonymous discovery intentionally exposes only the minimal shared extension "
        "contract surface needed for interoperable clients.\n"
        "Deployment-specific provider-private JSON-RPC methods are documented through "
        "the authenticated extended Agent Card instead of this anonymous OpenAPI "
        "surface.\n"
        f"Shared interrupt callback methods: {interrupt_methods}.\n\n"
        "Notification semantics: shared interrupt callback requests without JSON-RPC id "
        "return HTTP 204."
    )


def _build_jsonrpc_extension_openapi_examples() -> dict[str, Any]:
    return {
        "message_send": {
            "summary": "Send message via JSON-RPC core method",
            "value": {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "Explain what this repository does."}],
                    }
                },
            },
        },
        "message_stream": {
            "summary": "Stream message via JSON-RPC core method",
            "value": {
                "jsonrpc": "2.0",
                "id": 102,
                "method": "SendStreamingMessage",
                "params": {
                    "message": {
                        "messageId": "msg-stream-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "Stream the answer and highlight key conclusions."}],
                    }
                },
            },
        },
        "message_send_model_override": {
            "summary": "Send message with shared model override",
            "value": {
                "jsonrpc": "2.0",
                "id": 103,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-model-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "Answer with the faster model."}],
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
        "message_send_session_binding": {
            "summary": "Continue a session with shared session binding metadata",
            "value": {
                "jsonrpc": "2.0",
                "id": 104,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-continue-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "Continue previous work and summarize next steps."}],
                    },
                    "metadata": {
                        "shared": {
                            "session": {
                                "id": "s-1",
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
                "id": 105,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "msg-file-1",
                        "role": "ROLE_USER",
                        "parts": [
                            {"text": "Review the attached file and summarize the main risks."},
                            {
                                "url": "file:///workspace/report.pdf",
                                "filename": "report.pdf",
                                "mediaType": "application/pdf",
                            },
                        ],
                    }
                },
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


def _build_rest_message_openapi_examples() -> dict[str, Any]:
    return {
        "basic_message": {
            "summary": "Send a basic user message (HTTP+JSON)",
            "value": {
                "message": {
                    "messageId": "msg-rest-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "Explain what this repository does."}],
                }
            },
        },
        "message_with_file_input": {
            "summary": "Send message with file input (HTTP+JSON)",
            "value": {
                "message": {
                    "messageId": "msg-rest-file-1",
                    "role": "ROLE_USER",
                    "parts": [
                        {"text": "Review the attached file and summarize the main risks."},
                        {
                            "url": "file:///workspace/report.pdf",
                            "filename": "report.pdf",
                            "mediaType": "application/pdf",
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
                    "parts": [{"text": "Continue previous work and summarize next steps."}],
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
                    "parts": [{"text": "Answer with the faster model."}],
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
    del settings
    session_binding = build_session_binding_extension_params(
        runtime_profile=runtime_profile,
    )
    model_selection = build_model_selection_extension_params(
        runtime_profile=runtime_profile,
    )
    streaming = build_streaming_extension_params()
    interrupt_callback = build_interrupt_callback_extension_params(
        runtime_profile=runtime_profile,
    )
    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = original_openapi()
        components = schema.setdefault("components", {})
        if isinstance(components, dict):
            schemas = components.setdefault("schemas", {})
            if isinstance(schemas, dict):
                jsonrpc_request_schema = JSONRPCRequest.model_json_schema()
                jsonrpc_request_schema["title"] = "A2ARequest"
                schemas.setdefault("A2ARequest", jsonrpc_request_schema)
        paths = schema.get("paths")
        if isinstance(paths, dict):
            root_path = paths.get("/")
            if isinstance(root_path, dict):
                post = root_path.get("post")
                if isinstance(post, dict):
                    post["summary"] = "Handle A2A JSON-RPC Requests"
                    post["description"] = _build_jsonrpc_extension_openapi_description()
                    post["x-a2a-extension-contracts"] = {
                        "session_binding": {
                            "extension_uri": SESSION_BINDING_EXTENSION_URI,
                            **build_public_session_binding_extension_params(session_binding),
                        },
                        "model_selection": {
                            "extension_uri": MODEL_SELECTION_EXTENSION_URI,
                            **select_public_extension_params(
                                model_selection,
                                keys=(
                                    "metadata_field",
                                    "behavior",
                                    "applies_to_methods",
                                    "supported_metadata",
                                    "provider_private_metadata",
                                    "fields",
                                ),
                            ),
                        },
                        "streaming": {
                            "extension_uri": STREAMING_EXTENSION_URI,
                            **build_public_streaming_extension_params(streaming),
                        },
                        "interrupt_callback": {
                            "extension_uri": INTERRUPT_CALLBACK_EXTENSION_URI,
                            **select_public_extension_params(
                                build_public_interrupt_callback_extension_params(
                                    interrupt_callback
                                ),
                                keys=(
                                    "methods",
                                    "supported_interrupt_events",
                                    "request_id_field",
                                    "interrupt_metadata_field",
                                    "interrupt_fields",
                                ),
                            ),
                        },
                    }

                    request_body = post.setdefault("requestBody", {})
                    if isinstance(request_body, dict):
                        request_body.setdefault("required", True)
                        content = request_body.setdefault("content", {})
                        if isinstance(content, dict):
                            app_json = content.setdefault("application/json", {})
                            if isinstance(app_json, dict):
                                app_json["schema"] = {"$ref": "#/components/schemas/A2ARequest"}
                                app_json["examples"] = _build_jsonrpc_extension_openapi_examples()

            rest_post_contracts: dict[str, dict[str, Any]] = {
                "/message:send": {
                    "summary": "Send Message (HTTP+JSON)",
                    "description": (
                        "A2A HTTP+JSON message send endpoint. "
                        "Use ProtoJSON SendMessageRequest payloads with message.parts "
                        "and ROLE_* roles."
                    ),
                    "schema_ref": "#/components/schemas/SendMessageRequest",
                },
                "/message:stream": {
                    "summary": "Stream Message (HTTP+JSON)",
                    "description": (
                        "A2A HTTP+JSON streaming endpoint. "
                        "Use ProtoJSON SendMessageRequest payloads with message.parts "
                        "and ROLE_* roles."
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
