from __future__ import annotations

import asyncio
from collections.abc import Collection, Iterable, Mapping
from typing import Any, cast

from a2a.server.events import EventConsumer
from a2a.server.tasks import ResultAggregator, TaskManager
from a2a.types import (
    Artifact,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from google.protobuf.message import Message as ProtoMessage

from .a2a_utils import (
    clone_proto,
    proto_to_dict,
    replace_artifact_event_artifact,
    replace_artifact_parts,
    replace_message_parts,
    replace_status_event_message,
)
from .a2a_utils import (
    part_text_fallback as _part_text_fallback,
)

OUTPUT_NEGOTIATION_METADATA_KEY = "output_negotiation"
OUTPUT_NEGOTIATION_ACCEPTED_OUTPUT_MODES_FIELD = "accepted_output_modes"
_OPENCODE_METADATA_KEY = "opencode"
_APPLICATION_JSON_MEDIA_TYPE = "application/json"
_TEXT_PLAIN_MEDIA_TYPE = "text/plain"


def _accepted_output_modes_source(source: Any) -> Iterable[str] | None:
    if source is None:
        return None

    accepted = getattr(source, "accepted_output_modes", None) or getattr(
        source, "acceptedOutputModes", None
    )
    if accepted is not None:
        source = accepted

    if isinstance(source, str | bytes | bytearray | dict):
        return None
    if not isinstance(source, Iterable):
        return None
    return cast(Iterable[str], source)


def normalize_accepted_output_modes(source: Any) -> tuple[str, ...] | None:
    accepted = _accepted_output_modes_source(source)
    if accepted is None:
        return None

    normalized: list[str] = []
    for value in accepted:
        if not isinstance(value, str):
            continue
        mode = value.strip().lower()
        if not mode or mode in normalized:
            continue
        if mode in {"*", "*/*"}:
            return None
        normalized.append(mode)
    return tuple(normalized) or None


def accepts_output_mode(
    accepted_output_modes: Collection[str] | None,
    media_type: str,
) -> bool:
    return accepted_output_modes is None or media_type in accepted_output_modes


def part_text_fallback(part: Any) -> str | None:
    if isinstance(part, Part):
        return _part_text_fallback(part)
    return None


def build_output_negotiation_metadata(
    accepted_output_modes: Iterable[str] | None,
) -> dict[str, Any] | None:
    normalized = normalize_accepted_output_modes(accepted_output_modes)
    if normalized is None:
        return None
    return {
        _OPENCODE_METADATA_KEY: {
            OUTPUT_NEGOTIATION_METADATA_KEY: {
                OUTPUT_NEGOTIATION_ACCEPTED_OUTPUT_MODES_FIELD: sorted(normalized),
            }
        }
    }


def _normalize_metadata_mapping(metadata: Any) -> dict[str, Any]:
    if isinstance(metadata, ProtoMessage):
        normalized = proto_to_dict(metadata)
        return normalized if isinstance(normalized, dict) else {}
    if isinstance(metadata, Mapping):
        return dict(metadata)
    return {}


def merge_output_negotiation_metadata(
    metadata: dict[str, Any] | None,
    accepted_output_modes: Iterable[str] | None,
) -> dict[str, Any] | None:
    negotiation_metadata = build_output_negotiation_metadata(accepted_output_modes)
    if negotiation_metadata is None:
        return metadata

    merged = _normalize_metadata_mapping(metadata)
    opencode_metadata = merged.get(_OPENCODE_METADATA_KEY)
    if not isinstance(opencode_metadata, dict):
        opencode_metadata = {}
    else:
        opencode_metadata = dict(opencode_metadata)

    opencode_metadata[OUTPUT_NEGOTIATION_METADATA_KEY] = dict(
        cast(
            dict[str, Any],
            negotiation_metadata[_OPENCODE_METADATA_KEY][OUTPUT_NEGOTIATION_METADATA_KEY],
        )
    )
    merged[_OPENCODE_METADATA_KEY] = opencode_metadata
    return merged


def extract_accepted_output_modes_from_metadata(
    metadata: dict[str, Any] | None,
) -> tuple[str, ...] | None:
    normalized_metadata = _normalize_metadata_mapping(metadata)
    if not normalized_metadata:
        return None
    opencode_metadata = normalized_metadata.get(_OPENCODE_METADATA_KEY)
    if not isinstance(opencode_metadata, dict):
        return None
    negotiation_metadata = opencode_metadata.get(OUTPUT_NEGOTIATION_METADATA_KEY)
    if not isinstance(negotiation_metadata, dict):
        return None
    accepted_output_modes = negotiation_metadata.get(OUTPUT_NEGOTIATION_ACCEPTED_OUTPUT_MODES_FIELD)
    return normalize_accepted_output_modes(accepted_output_modes)


def annotate_output_negotiation_metadata(
    payload: Any,
    accepted_output_modes: Iterable[str] | None,
) -> Any:
    normalized = normalize_accepted_output_modes(accepted_output_modes)
    if normalized is None:
        return payload

    if isinstance(payload, Task):
        updated = clone_proto(payload)
        updated.ClearField("metadata")
        merged_metadata = merge_output_negotiation_metadata(payload.metadata, normalized)
        if merged_metadata:
            updated.metadata.update(merged_metadata)
        return updated

    if isinstance(payload, TaskStatusUpdateEvent):
        updated = clone_proto(payload)
        updated.ClearField("metadata")
        merged_metadata = merge_output_negotiation_metadata(payload.metadata, normalized)
        if merged_metadata:
            updated.metadata.update(merged_metadata)
        return updated

    if isinstance(payload, TaskArtifactUpdateEvent):
        updated = clone_proto(payload)
        updated.ClearField("metadata")
        merged_metadata = merge_output_negotiation_metadata(payload.metadata, normalized)
        if merged_metadata:
            updated.metadata.update(merged_metadata)
        return updated

    return payload


def apply_accepted_output_modes(
    payload: Any,
    accepted_output_modes: Iterable[str] | None,
) -> Any | None:
    normalized = normalize_accepted_output_modes(accepted_output_modes)
    if normalized is None:
        return payload

    if isinstance(payload, TaskArtifactUpdateEvent):
        artifact = _filter_artifact(payload.artifact, normalized)
        if artifact is None:
            return None
        return replace_artifact_event_artifact(payload, artifact)

    if isinstance(payload, TaskStatusUpdateEvent):
        message = None
        if payload.status.HasField("message"):
            message = _filter_optional_message(payload.status.message, normalized)
        return replace_status_event_message(payload, message)

    if isinstance(payload, Task):
        return _filter_task(payload, normalized)

    if isinstance(payload, Message):
        filtered = _filter_message(payload, normalized)
        if filtered is not None:
            return filtered
        return replace_message_parts(payload, [])

    return payload


class NegotiatingResultAggregator(ResultAggregator):
    def __init__(
        self,
        task_manager: TaskManager,
        accepted_output_modes: Iterable[str] | None,
    ) -> None:
        super().__init__(task_manager)
        self._accepted_output_modes = normalize_accepted_output_modes(accepted_output_modes)

    def _transform_event(self, event: Any) -> Any | None:
        negotiated_event = apply_accepted_output_modes(event, self._accepted_output_modes)
        if negotiated_event is None:
            return None
        return annotate_output_negotiation_metadata(negotiated_event, self._accepted_output_modes)

    async def _persist_output_negotiation_metadata(self, event: Any) -> None:
        if not isinstance(event, TaskArtifactUpdateEvent):
            return

        accepted_output_modes = extract_accepted_output_modes_from_metadata(event.metadata)
        if accepted_output_modes is None:
            return

        task = await self.task_manager.ensure_task(event)
        merged_metadata = merge_output_negotiation_metadata(task.metadata, accepted_output_modes)
        if merged_metadata == task.metadata:
            return
        task.metadata = merged_metadata
        await self.task_manager._save_task(task)

    async def consume_and_emit(self, consumer: EventConsumer):  # noqa: ANN201
        async for event in consumer.consume_all():
            transformed_event = self._transform_event(event)
            if transformed_event is None:
                continue
            await self._persist_output_negotiation_metadata(transformed_event)
            await self.task_manager.process(transformed_event)
            yield transformed_event

    async def consume_all(self, consumer: EventConsumer) -> Task | Message | None:
        async for event in consumer.consume_all():
            transformed_event = self._transform_event(event)
            if transformed_event is None:
                continue
            if isinstance(transformed_event, Message):
                self._message = transformed_event
                return transformed_event
            await self._persist_output_negotiation_metadata(transformed_event)
            await self.task_manager.process(transformed_event)
        return await self.task_manager.get_task()

    async def consume_and_break_on_interrupt(
        self,
        consumer: EventConsumer,
        blocking: bool = True,
        event_callback=None,  # noqa: ANN001
    ) -> tuple[Task | Message | None, bool, asyncio.Task | None]:
        event_stream = consumer.consume_all()
        interrupted = False
        bg_task: asyncio.Task | None = None
        async for event in event_stream:
            transformed_event = self._transform_event(event)
            if transformed_event is None:
                continue
            if isinstance(transformed_event, Message):
                self._message = transformed_event
                return transformed_event, False, None
            await self._persist_output_negotiation_metadata(transformed_event)
            await self.task_manager.process(transformed_event)

            should_interrupt = False
            is_auth_required = (
                isinstance(transformed_event, Task | TaskStatusUpdateEvent)
                and transformed_event.status.state == TaskState.TASK_STATE_AUTH_REQUIRED
            )
            if is_auth_required or not blocking:
                should_interrupt = True

            if should_interrupt:
                bg_task = asyncio.create_task(
                    self._continue_consuming(event_stream, event_callback)
                )
                interrupted = True
                break

        return await self.task_manager.get_task(), interrupted, bg_task

    async def _continue_consuming(
        self,
        event_stream,
        event_callback=None,  # noqa: ANN001
    ) -> None:
        async for event in event_stream:
            transformed_event = self._transform_event(event)
            if transformed_event is None:
                continue
            await self._persist_output_negotiation_metadata(transformed_event)
            await self.task_manager.process(transformed_event)
            if event_callback:
                await event_callback()


def _filter_task(task: Task, accepted_output_modes: Collection[str]) -> Task:
    updated = clone_proto(task)
    if updated.status.HasField("message"):
        filtered_message = _filter_optional_message(updated.status.message, accepted_output_modes)
        if filtered_message is None:
            updated.status.ClearField("message")
        else:
            updated.status.message.CopyFrom(filtered_message)

    filtered_history = [
        filtered
        for filtered in (
            _filter_message(message, accepted_output_modes) for message in task.history
        )
        if filtered is not None
    ]
    del updated.history[:]
    updated.history.extend(filtered_history)

    filtered_artifacts = [
        filtered
        for filtered in (
            _filter_artifact(artifact, accepted_output_modes) for artifact in task.artifacts
        )
        if filtered is not None
    ]
    del updated.artifacts[:]
    updated.artifacts.extend(filtered_artifacts)

    return updated


def _filter_optional_message(
    message: Message | None,
    accepted_output_modes: Collection[str],
) -> Message | None:
    if message is None:
        return None
    return _filter_message(message, accepted_output_modes)


def _filter_message(
    message: Message,
    accepted_output_modes: Collection[str],
) -> Message | None:
    parts = _filter_parts(message.parts, accepted_output_modes)
    if not parts:
        return None
    return replace_message_parts(message, parts)


def _filter_artifact(
    artifact: Artifact,
    accepted_output_modes: Collection[str],
) -> Artifact | None:
    parts = _filter_parts(artifact.parts, accepted_output_modes)
    if not parts:
        return None
    return replace_artifact_parts(artifact, parts)


def _filter_parts(
    parts: list[Part],
    accepted_output_modes: Collection[str],
) -> list[Part]:
    filtered: list[Part] = []
    for part in parts:
        media_type = _part_media_type(part)
        if media_type is None or accepts_output_mode(accepted_output_modes, media_type):
            filtered.append(part)
            continue
        if accepts_output_mode(accepted_output_modes, _TEXT_PLAIN_MEDIA_TYPE):
            fallback_text = part_text_fallback(part)
            if fallback_text is not None:
                filtered.append(Part(text=fallback_text))
    return filtered


def _part_media_type(part: Part) -> str | None:
    if part.HasField("text"):
        return _TEXT_PLAIN_MEDIA_TYPE
    if part.HasField("data"):
        return _APPLICATION_JSON_MEDIA_TYPE
    if part.HasField("raw") or part.HasField("url"):
        return part.media_type or "application/octet-stream"
    return None
