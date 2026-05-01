from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def build_method_contract_params(
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    unsupported: tuple[str, ...],
) -> dict[str, list[str]]:
    params: dict[str, list[str]] = {}
    if required:
        params["required"] = list(required)
    if optional:
        params["optional"] = list(optional)
    if unsupported:
        params["unsupported"] = list(unsupported)
    return params


def build_method_contract_doc(
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    unsupported: tuple[str, ...],
    result_fields: tuple[str, ...],
    items_type: str | None = None,
    notification_response_status: int | None = None,
    extra_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    contract_doc: dict[str, Any] = {
        "params": build_method_contract_params(
            required=required,
            optional=optional,
            unsupported=unsupported,
        ),
        "result": {"fields": list(result_fields)},
    }
    if items_type:
        contract_doc["result"]["items_type"] = items_type
    if notification_response_status is not None:
        contract_doc["notification_response_status"] = notification_response_status
    if extra_fields:
        contract_doc.update(extra_fields)
    return contract_doc


def build_method_contract_docs(
    method_contracts: Iterable[Any],
    *,
    active_methods: set[str] | None = None,
    default_result_fields: tuple[str, ...] = (),
    extra_fields_by_method: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    contract_docs: dict[str, Any] = {}
    for contract in method_contracts:
        method = contract.method
        if active_methods is not None and method not in active_methods:
            continue
        contract_docs[method] = build_method_contract_doc(
            required=contract.required_params,
            optional=contract.optional_params,
            unsupported=getattr(contract, "unsupported_params", ()),
            result_fields=getattr(contract, "result_fields", default_result_fields),
            items_type=getattr(contract, "items_type", None),
            notification_response_status=getattr(
                contract,
                "notification_response_status",
                None,
            ),
            extra_fields=(
                dict(extra_fields_by_method[method])
                if extra_fields_by_method and method in extra_fields_by_method
                else None
            ),
        )
    return contract_docs
