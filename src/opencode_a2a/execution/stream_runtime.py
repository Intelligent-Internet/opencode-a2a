from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    DataPart,
    Part,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from .event_helpers import _enqueue_artifact_update
from .stream_events import (
    BlockType,
    _build_progress_identity,
    _extract_event_session_id,
    _extract_interrupt_asked_event,
    _extract_interrupt_resolved_event,
    _extract_progress_metadata,
    _extract_stream_message_id,
    _extract_stream_part_id,
    _extract_stream_role,
    _extract_stream_session_id,
    _extract_stream_snapshot_text,
    _extract_stream_terminal_signal,
    _extract_token_usage,
    _extract_tool_part_payload,
    _extract_upstream_error_from_event,
    _log_stream_event_debug,
    _resolve_stream_block_type,
)
from .stream_state import (
    _build_output_metadata,
    _build_stream_artifact_metadata,
    _NormalizedStreamChunk,
    _PendingDelta,
    _StreamOutputState,
    _StreamPartState,
)
from .upstream_error_translator import _StreamTerminalSignal

logger = logging.getLogger("opencode_a2a.execution.executor")


class StreamRuntime:
    def __init__(
        self,
        *,
        client,
        emit_metric: Callable[..., None],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._client = client
        self._emit_metric = emit_metric
        self._sleep = sleep

    async def consume(
        self,
        *,
        session_id: str,
        identity: str,
        task_id: str,
        context_id: str,
        artifact_id: str,
        stream_state: _StreamOutputState,
        event_queue: EventQueue,
        stop_event: asyncio.Event,
        terminal_signal: asyncio.Future[_StreamTerminalSignal],
        directory: str | None = None,
    ) -> None:
        part_states: dict[str, _StreamPartState] = {}
        pending_deltas: defaultdict[str, list[_PendingDelta]] = defaultdict(list)
        backoff = 0.5
        max_backoff = 5.0

        async def _emit_chunks(chunks: list[_NormalizedStreamChunk]) -> None:
            for chunk in chunks:
                resolved_message_id = stream_state.resolve_message_id(chunk.message_id)
                chunk_text = getattr(chunk.part.root, "text", "")
                if stream_state.should_drop_initial_user_echo(
                    chunk_text,
                    block_type=chunk.block_type,
                    role=chunk.role,
                ):
                    continue
                should_emit, effective_append = stream_state.register_chunk(
                    block_type=chunk.block_type,
                    content_key=chunk.content_key,
                    append=chunk.append,
                    accumulate_content=chunk.accumulate_content,
                )
                if not should_emit:
                    continue
                sequence = stream_state.next_sequence()
                await _enqueue_artifact_update(
                    event_queue=event_queue,
                    task_id=task_id,
                    context_id=context_id,
                    artifact_id=artifact_id,
                    part=chunk.part,
                    append=effective_append,
                    last_chunk=False,
                    artifact_metadata=_build_stream_artifact_metadata(
                        block_type=chunk.block_type,
                        shared_source=chunk.shared_source,
                        message_id=resolved_message_id,
                        role=chunk.role,
                        event_id=stream_state.build_event_id(sequence),
                        sequence=sequence,
                    ),
                )
                logger.debug(
                    "Stream chunk task_id=%s session_id=%s block_type=%s append=%s "
                    "shared_source=%s internal_source=%s text=%s",
                    task_id,
                    session_id,
                    chunk.block_type,
                    effective_append,
                    chunk.shared_source,
                    chunk.internal_source,
                    chunk.content_key,
                )
                if chunk.block_type == BlockType.TOOL_CALL:
                    self._emit_metric("tool_call_chunks_emitted_total")

        async def _emit_interrupt_status(
            *,
            state: TaskState,
            request_id: str,
            interrupt_type: str,
            phase: str,
            details: Mapping[str, Any] | None = None,
            resolution: str | None = None,
        ) -> None:
            interrupt_metadata: dict[str, Any] = {
                "request_id": request_id,
                "type": interrupt_type,
                "phase": phase,
            }
            if details is not None:
                interrupt_metadata["details"] = dict(details)
            if resolution is not None:
                interrupt_metadata["resolution"] = resolution
            sequence = stream_state.next_sequence()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=state),
                    final=False,
                    metadata=_build_output_metadata(
                        session_id=session_id,
                        stream={
                            "message_id": stream_state.resolve_message_id(None),
                            "event_id": stream_state.build_event_id(sequence),
                            "source": "interrupt",
                            "sequence": sequence,
                        },
                        interrupt=interrupt_metadata,
                    ),
                )
            )
            if phase == "asked":
                self._emit_metric("interrupt_requests_total")
            elif phase == "resolved":
                self._emit_metric("interrupt_resolved_total")

        async def _emit_progress_status(
            *,
            message_id: str | None,
            progress: Mapping[str, Any],
        ) -> None:
            sequence = stream_state.next_sequence()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.working),
                    final=False,
                    metadata=_build_output_metadata(
                        session_id=session_id,
                        stream={
                            "message_id": stream_state.resolve_message_id(message_id),
                            "event_id": stream_state.build_event_id(sequence),
                            "source": "progress",
                            "sequence": sequence,
                        },
                        progress=dict(progress),
                    ),
                )
            )

        def _new_text_chunk(
            *,
            text: str,
            append: bool,
            block_type: BlockType,
            internal_source: str,
            shared_source: str,
            message_id: str | None,
            role: str | None,
        ) -> _NormalizedStreamChunk:
            return _NormalizedStreamChunk(
                part=Part(root=TextPart(text=text)),
                content_key=text,
                accumulate_content=True,
                append=append,
                block_type=block_type,
                internal_source=internal_source,
                shared_source=shared_source,
                message_id=message_id,
                role=role,
            )

        def _new_data_chunk(
            *,
            data: Mapping[str, Any],
            content_key: str,
            append: bool,
            block_type: BlockType,
            internal_source: str,
            shared_source: str,
            message_id: str | None,
            role: str | None,
        ) -> _NormalizedStreamChunk:
            return _NormalizedStreamChunk(
                part=Part(root=DataPart(data=dict(data))),
                content_key=content_key,
                accumulate_content=False,
                append=append,
                block_type=block_type,
                internal_source=internal_source,
                shared_source=shared_source,
                message_id=message_id,
                role=role,
            )

        def _upsert_part_state(
            *,
            part_id: str,
            part: Mapping[str, Any],
            props: Mapping[str, Any],
            role: str | None,
            message_id: str | None,
        ) -> _StreamPartState | None:
            block_type = _resolve_stream_block_type(part, props)
            if block_type is None:
                return None
            state = part_states.get(part_id)
            if state is None:
                state = _StreamPartState(
                    block_type=block_type,
                    message_id=message_id,
                    role=role,
                )
                part_states[part_id] = state
                return state
            state.block_type = block_type
            if role is not None:
                state.role = role
            if message_id:
                state.message_id = message_id
            return state

        def _delta_chunks(
            *,
            state: _StreamPartState,
            delta_text: str,
            message_id: str | None,
            internal_source: str,
        ) -> list[_NormalizedStreamChunk]:
            if not delta_text:
                return []
            if message_id:
                state.message_id = message_id
            state.buffer = f"{state.buffer}{delta_text}"
            state.saw_delta = True
            return [
                _new_text_chunk(
                    text=delta_text,
                    append=True,
                    block_type=state.block_type,
                    internal_source=internal_source,
                    shared_source="stream",
                    message_id=state.message_id,
                    role=state.role,
                )
            ]

        def _snapshot_chunks(
            *,
            state: _StreamPartState,
            snapshot: str,
            message_id: str | None,
            part_id: str,
        ) -> list[_NormalizedStreamChunk]:
            if message_id:
                state.message_id = message_id
            previous = state.buffer
            if snapshot == previous:
                return []
            if snapshot.startswith(previous):
                delta_text = snapshot[len(previous) :]
                state.buffer = snapshot
                if not delta_text:
                    return []
                return [
                    _new_text_chunk(
                        text=delta_text,
                        append=True,
                        block_type=state.block_type,
                        internal_source="part_text_diff",
                        shared_source="stream",
                        message_id=state.message_id,
                        role=state.role,
                    )
                ]
            state.buffer = snapshot
            logger.warning(
                "Suppressing non-prefix snapshot rewrite "
                "task_id=%s session_id=%s part_id=%s block_type=%s had_delta=%s",
                task_id,
                session_id,
                part_id,
                state.block_type.value,
                state.saw_delta,
            )
            return []

        def _tool_chunks(
            *,
            state: _StreamPartState,
            part: Mapping[str, Any],
            message_id: str | None,
        ) -> list[_NormalizedStreamChunk]:
            tool_payload = _extract_tool_part_payload(part)
            if tool_payload is None:
                return []
            content_key = json.dumps(
                tool_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if message_id:
                state.message_id = message_id
            previous = state.buffer
            if content_key == previous:
                return []
            state.buffer = content_key
            return [
                _new_data_chunk(
                    data=tool_payload,
                    content_key=content_key,
                    append=bool(previous),
                    block_type=state.block_type,
                    internal_source="tool_part_update",
                    shared_source="tool_part_update",
                    message_id=state.message_id,
                    role=state.role,
                )
            ]

        try:
            while not stop_event.is_set():
                try:
                    async for event in self._client.stream_events(
                        stop_event=stop_event,
                        directory=directory,
                    ):
                        if stop_event.is_set():
                            break
                        _log_stream_event_debug(
                            event,
                            limit=max(0, self._client.settings.a2a_log_body_limit),
                        )
                        event_type = event.get("type")
                        if not isinstance(event_type, str):
                            continue
                        props = event.get("properties")
                        if not isinstance(props, Mapping):
                            continue
                        event_session_id = _extract_event_session_id(event)
                        if event_session_id == session_id:
                            part = props.get("part")
                            if isinstance(part, Mapping):
                                progress = _extract_progress_metadata(part, props)
                                if progress is not None:
                                    progress_identity = _build_progress_identity(part, props)
                                    progress_key = json.dumps(
                                        progress,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    )
                                    if stream_state.register_progress(
                                        identity=progress_identity,
                                        content_key=progress_key,
                                    ):
                                        await _emit_progress_status(
                                            message_id=_extract_stream_message_id(part, props),
                                            progress=progress,
                                        )
                            upstream_error = _extract_upstream_error_from_event(event)
                            if upstream_error is not None and stream_state.upstream_error is None:
                                stream_state.upstream_error = upstream_error
                            signal = _extract_stream_terminal_signal(event)
                            if signal is not None and not terminal_signal.done():
                                terminal_signal.set_result(signal)
                                stop_event.set()
                            usage = _extract_token_usage(event)
                            if usage is not None:
                                stream_state.ingest_token_usage(usage)
                            asked = _extract_interrupt_asked_event(event)
                            if asked is not None:
                                request_id = asked["request_id"]
                                if stream_state.mark_interrupt_pending(request_id):
                                    remember_request = getattr(
                                        self._client,
                                        "remember_interrupt_request",
                                        None,
                                    )
                                    if callable(remember_request):
                                        await remember_request(
                                            request_id=request_id,
                                            session_id=session_id,
                                            interrupt_type=asked["interrupt_type"],
                                            identity=identity,
                                            task_id=task_id,
                                            context_id=context_id,
                                        )
                                    await _emit_interrupt_status(
                                        state=TaskState.input_required,
                                        request_id=request_id,
                                        interrupt_type=asked["interrupt_type"],
                                        phase="asked",
                                        details=asked["details"],
                                    )
                            resolved = _extract_interrupt_resolved_event(event)
                            if resolved is not None:
                                resolved_request_id = resolved["request_id"]
                                cleared_pending = stream_state.clear_interrupt_pending(
                                    resolved_request_id
                                )
                                discard_request = getattr(
                                    self._client,
                                    "discard_interrupt_request",
                                    None,
                                )
                                if callable(discard_request):
                                    await discard_request(resolved_request_id)
                                if cleared_pending:
                                    await _emit_interrupt_status(
                                        state=TaskState.working,
                                        request_id=resolved_request_id,
                                        interrupt_type=resolved["interrupt_type"],
                                        phase="resolved",
                                        resolution=resolved["resolution"],
                                    )
                        if event_type not in {"message.part.updated", "message.part.delta"}:
                            continue
                        part = props.get("part")
                        if not isinstance(part, Mapping):
                            part = {}
                        if _extract_stream_session_id(part, props) != session_id:
                            continue
                        message_id = _extract_stream_message_id(part, props)
                        part_id = _extract_stream_part_id(part, props)
                        if not part_id:
                            continue

                        if event_type == "message.part.delta":
                            field = props.get("field")
                            delta = props.get("delta")
                            if field != "text" or not isinstance(delta, str) or not delta:
                                continue
                            state = part_states.get(part_id)
                            if state is None:
                                pending_deltas[part_id].append(
                                    _PendingDelta(
                                        field=field,
                                        delta=delta,
                                        message_id=message_id,
                                    )
                                )
                                continue
                            if state.role in {"user", "system"}:
                                continue
                            delta_chunks = _delta_chunks(
                                state=state,
                                delta_text=delta,
                                message_id=message_id,
                                internal_source="delta_event",
                            )
                            if delta_chunks:
                                await _emit_chunks(delta_chunks)
                            continue

                        role = _extract_stream_role(part, props)
                        state = _upsert_part_state(
                            part_id=part_id,
                            part=part,
                            props=props,
                            role=role,
                            message_id=message_id,
                        )
                        if state is None:
                            pending_deltas.pop(part_id, None)
                            continue
                        if state.role in {"user", "system"}:
                            pending_deltas.pop(part_id, None)
                            continue

                        chunks: list[_NormalizedStreamChunk] = []
                        pending = pending_deltas.pop(part_id, [])
                        for buffered in pending:
                            if buffered.field != "text":
                                continue
                            chunks.extend(
                                _delta_chunks(
                                    state=state,
                                    delta_text=buffered.delta,
                                    message_id=buffered.message_id,
                                    internal_source="delta_event_buffered",
                                )
                            )

                        delta = props.get("delta")
                        if isinstance(delta, str) and delta:
                            chunks.extend(
                                _delta_chunks(
                                    state=state,
                                    delta_text=delta,
                                    message_id=message_id,
                                    internal_source="delta",
                                )
                            )
                        elif state.block_type == BlockType.TOOL_CALL:
                            chunks.extend(
                                _tool_chunks(
                                    state=state,
                                    part=part,
                                    message_id=message_id,
                                )
                            )
                        else:
                            snapshot_text = _extract_stream_snapshot_text(part)
                            if snapshot_text is not None:
                                chunks.extend(
                                    _snapshot_chunks(
                                        state=state,
                                        snapshot=snapshot_text,
                                        message_id=message_id,
                                        part_id=part_id,
                                    )
                                )

                        if chunks:
                            await _emit_chunks(chunks)

                    break
                except Exception:
                    if stop_event.is_set():
                        break
                    self._emit_metric("opencode_stream_retries_total")
                    logger.exception("OpenCode event stream failed; retrying")
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
        except Exception:
            logger.exception("OpenCode event stream failed")
