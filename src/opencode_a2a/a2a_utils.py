from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar, cast

from a2a.types import Artifact, Message, Part, TaskArtifactUpdateEvent, TaskStatusUpdateEvent
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message as ProtoMessage
from google.protobuf.struct_pb2 import ListValue, Struct, Value

ProtoT = TypeVar("ProtoT", bound=ProtoMessage)


def clone_proto(message: ProtoT) -> ProtoT:
    cloned = cast(ProtoT, message.__class__())
    cloned.CopyFrom(message)
    return cloned


def proto_to_dict(
    message: ProtoMessage,
    *,
    preserving_proto_field_name: bool = False,
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        MessageToDict(
            message,
            preserving_proto_field_name=preserving_proto_field_name,
        ),
    )


def proto_equals(left: ProtoMessage, right: ProtoMessage) -> bool:
    return proto_to_dict(left, preserving_proto_field_name=True) == proto_to_dict(
        right,
        preserving_proto_field_name=True,
    )


def _to_proto_value(value: Any) -> Value:
    proto_value = Value()
    if value is None:
        proto_value.null_value = 0
        return proto_value
    if isinstance(value, bool):
        proto_value.bool_value = value
        return proto_value
    if isinstance(value, int | float):
        proto_value.number_value = value
        return proto_value
    if isinstance(value, str):
        proto_value.string_value = value
        return proto_value
    if isinstance(value, Mapping):
        struct_value = Struct()
        for key, item in value.items():
            struct_value.fields[str(key)].CopyFrom(_to_proto_value(item))
        proto_value.struct_value.CopyFrom(struct_value)
        return proto_value
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        list_value = ListValue()
        for item in value:
            list_value.values.add().CopyFrom(_to_proto_value(item))
        proto_value.list_value.CopyFrom(list_value)
        return proto_value
    raise TypeError(f"Unsupported structured payload type: {type(value)!r}")


def make_data_part(
    data: Any,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Part:
    part = Part()
    part.data.CopyFrom(_to_proto_value(data))
    if metadata:
        part.metadata.update(dict(metadata))
    return part


def part_kind(part: Part) -> str | None:
    if cast(bool, part.HasField("text")):
        return "text"
    if cast(bool, part.HasField("data")):
        return "data"
    if cast(bool, part.HasField("raw")) or cast(bool, part.HasField("url")):
        return "file"
    return None


def part_text(part: Part) -> str | None:
    if part.HasField("text"):
        return part.text
    return None


def part_text_fallback(part: Part) -> str | None:
    if part.HasField("text"):
        return part.text
    if part.HasField("data"):
        return json.dumps(
            MessageToDict(part.data),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    return None


def replace_message_parts(message: Message, parts: Sequence[Part]) -> Message:
    updated = clone_proto(message)
    del updated.parts[:]
    updated.parts.extend(parts)
    return updated


def replace_artifact_parts(artifact: Artifact, parts: Sequence[Part]) -> Artifact:
    updated = clone_proto(artifact)
    del updated.parts[:]
    updated.parts.extend(parts)
    return updated


def replace_status_event_message(
    event: TaskStatusUpdateEvent,
    message: Message | None,
) -> TaskStatusUpdateEvent:
    updated = clone_proto(event)
    if message is None:
        updated.status.ClearField("message")
    else:
        updated.status.message.CopyFrom(message)
    return updated


def replace_artifact_event_artifact(
    event: TaskArtifactUpdateEvent,
    artifact: Artifact,
) -> TaskArtifactUpdateEvent:
    updated = clone_proto(event)
    updated.artifact.CopyFrom(artifact)
    return updated
