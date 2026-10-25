import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";

let stage = "startup";
const blockTypes = new Set();
process.on("uncaughtException", error => {
  console.error(`A2A probe failed at ${stage}: ${error.name}: ${error.message}`);
  process.exit(1);
});

const token = (await readFile("/probe/a2a-token", "utf8")).trim();
const otherToken = (await readFile("/probe/other-token", "utf8")).trim();
const base = "http://127.0.0.1:8000";
const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "A2A-Extensions": "urn:opencode-a2a:extension:session-binding:v1,urn:opencode-a2a:extension:stream-hints:v1,urn:opencode-a2a:extension:interactive-interrupt:v1" };
let ready = false;
for (let attempt = 0; attempt < 240; attempt++) {
  try {
    const response = await fetch(base + "/.well-known/agent-card.json", { headers, signal: AbortSignal.timeout(2000) });
    if (response.ok) { ready = true; break; }
  } catch (error) { if (error.name !== "TypeError" && error.name !== "TimeoutError") throw error; }
  await new Promise(resolve => setTimeout(resolve, 500));
}
assert.ok(ready, "gateway did not become ready; inspect private gateway log");
async function rpc(method, params, streaming = false, onEvent, authToken = token) {
  const response = await fetch(base + "/", {
    method: "POST", headers: { ...headers, Authorization: `Bearer ${authToken}`, Accept: streaming ? "text/event-stream" : "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: randomUUID(), method, params }),
    signal: AbortSignal.timeout(180000),
  });
  assert.equal(response.status, 200);
  if (!streaming) return response.json();
  const events = [];
  let buffer = "";
  const decoder = new TextDecoder();
  for await (const chunk of response.body) {
    buffer += decoder.decode(chunk, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, boundary).trimEnd();
      buffer = buffer.slice(boundary + 1);
      if (!line.startsWith("data:")) continue;
      const event = JSON.parse(line.slice(5));
      events.push(event);
      const block = event.result?.artifactUpdate?.artifact?.metadata?.shared?.stream?.block_type;
      if (block) blockTypes.add(block);
      if (onEvent) await onEvent(event);
    }
  }
  return events;
}
function message(text, contextId) {
  return { message: { messageId: randomUUID(), ...(contextId ? { contextId } : {}), role: "ROLE_USER", parts: [{ text }] } };
}
function content(value) {
  if (Array.isArray(value)) return value.map(content).join("\n");
  if (!value || typeof value !== "object") return "";
  return (typeof value.text === "string" ? value.text : "") + Object.entries(value).filter(([key]) => key !== "text").map(([, child]) => content(child)).join("\n");
}
function artifacts(events) {
  const blocks = new Map();
  for (const event of events) {
    const update = event.result?.artifactUpdate;
    if (!update?.artifact) continue;
    const key = update.artifact.artifactId;
    const text = content(update.artifact.parts);
    blocks.set(key, update.append ? (blocks.get(key) ?? "") + text : text);
  }
  return [...blocks.values()].join("\n");
}
const mode = process.env.V2_PROBE_CASE ?? "normal";
async function evidence() {
  const response = await fetch("http://127.0.0.1:4097/__probe/evidence", { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(2000) });
  assert.equal(response.status, 200);
  return response.json();
}
if (mode !== "normal") {
  let pendingIntervention;
  const negativePrompt = ["intervention-gap", "expired-intervention"].includes(mode)
    ? 'Call the question tool once, ask "Proceed?" with Yes/No choices and wait for my answer.'
    : "Reply exactly NEGATIVE_TEST. Do not use tools.";
  const failed = await rpc("SendStreamingMessage", message(negativePrompt), true, async event => {
    const update = event.result?.statusUpdate;
    if (!["intervention-gap", "expired-intervention"].includes(mode) || update?.status?.state !== "TASK_STATE_INPUT_REQUIRED") return;
    pendingIntervention = update.metadata.shared.interrupt.request_id;
    if (mode === "expired-intervention") {
      await new Promise(resolve => setTimeout(resolve, 1500));
      const expired = await rpc("a2a.interrupt.question.reply", { request_id: pendingIntervention, answers: [["Yes"]] });
      assert.ok(expired.error, "expired intervention accepted");
      return;
    }
    const disconnected = await fetch("http://127.0.0.1:4097/__probe/disconnect", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    assert.equal(disconnected.status, 200);
  });
  assert.ok(failed.some(event => event.result?.statusUpdate?.status?.state === "TASK_STATE_FAILED"));
  assert.ok(!failed.some(event => event.result?.statusUpdate?.status?.state === "TASK_STATE_COMPLETED"));
  const observed = await evidence();
  assert.equal(observed.admissions, 1, "uncertain request was resubmitted");
  assert.equal(observed.event_connections, 1, "unexpected SSE replay/reconnect");
  if (observed.model_refs.length) assert.deepEqual(observed.model_refs, ["openai/gpt-6-luna-fast"]);
  if (!["provider-failure", "expired-intervention"].includes(mode)) assert.equal(observed.injected, true);
  if (mode === "provider-failure") assert.ok(observed.terminal_types.includes("session.execution.failed"));
  if (["intervention-gap", "expired-intervention"].includes(mode)) {
    assert.ok(pendingIntervention);
    const late = await rpc("a2a.interrupt.question.reply", { request_id: pendingIntervention, answers: [["Yes"]] });
    assert.ok(late.error, "poisoned intervention resumed upstream execution");
    assert.equal((await evidence()).routes["POST /api/session/ID/form/ID/reply"] ?? 0, 0);
  }
  console.log(JSON.stringify({ mode, no_false_success: true, no_resubmission: true, upstream: observed }, null, 2));
  process.exit(0);
}
const marker = `a2a-exclusive-${randomUUID()}`;
stage = "stream";
const stream = await rpc("SendStreamingMessage", message(`Remember ${marker}. Reply exactly ${marker}. Do not use tools.`), true);
assert.ok(stream.some(event => event.result?.statusUpdate?.status?.state === "TASK_STATE_COMPLETED"), "stream has no proven completion");
assert.ok(artifacts(stream).includes(marker), "stream has no expected assistant output");
assert.ok(!stream.some(event => event.error), "stream returned protocol error");
const task = stream.find(event => event.result?.task)?.result.task;
assert.ok(task?.id && task.contextId);
stage = "followup";
const followup = await rpc("SendMessage", message("Reply with the remembered token only. Do not use tools.", task.contextId));
const followupTask = followup.result?.task ?? followup.result;
if (followupTask.status?.state !== "TASK_STATE_COMPLETED") {
  console.error(JSON.stringify({ state: followupTask.status?.state }));
}
assert.equal(followupTask.status?.state, "TASK_STATE_COMPLETED", "unary did not await terminal execution");
assert.ok(content(followupTask.artifacts).includes(marker), "follow-up lost context");
assert.notEqual(followupTask.id, task.id);
const persisted = await rpc("GetTask", { id: task.id });
assert.equal(persisted.result?.status?.state, "TASK_STATE_COMPLETED");
const foreignTask = await rpc("GetTask", { id: task.id }, false, undefined, otherToken);
assert.ok(foreignTask.error, "cross-owner task read accepted");
let formReplies = 0;
stage = "form";
let formRequest;
const interactive = await rpc("SendStreamingMessage", message('Call the question tool once with the question "Continue?" and Yes/No choices. Wait for my answer. After Yes reply exactly A2A_FORM_DONE. Do not ask as plain text.'), true, async event => {
  const update = event.result?.statusUpdate;
  const interrupt = update?.metadata?.shared?.interrupt;
  if (update?.status?.state !== "TASK_STATE_INPUT_REQUIRED") return;
  assert.ok(interrupt?.request_id, "input-required lacks bound request ID");
  formRequest = interrupt.request_id;
  const unauthorized = await rpc("a2a.interrupt.question.reply", { request_id: formRequest, answers: [["Yes"]] }, false, undefined, otherToken);
  assert.ok(unauthorized.error, "cross-owner form reply accepted");
  const reply = await rpc("a2a.interrupt.question.reply", { request_id: formRequest, answers: [["Yes"]] });
  assert.ok(!reply.error, "authorized typed form reply rejected");
  formReplies++;
});
if (formReplies !== 1) console.error(JSON.stringify({ states: interactive.map(event => event.result?.statusUpdate?.status?.state) }));
assert.equal(formReplies, 1);
assert.ok(interactive.some(event => event.result?.statusUpdate?.status?.state === "TASK_STATE_COMPLETED"));
assert.ok(artifacts(interactive).includes("A2A_FORM_DONE"));
const stale = await rpc("a2a.interrupt.question.reply", { request_id: formRequest, answers: [["Yes"]] });
assert.ok(stale.error, "stale form callback accepted");
stage = "multiselect-form";
let multiselectReplies = 0;
const multiselect = await rpc("SendStreamingMessage", message('Call question with exactly {"questions":[{"header":"Pick","question":"Choose acceptance values","multiple":true,"options":[{"label":"Alpha","description":"First"},{"label":"Beta","description":"Second"}]}]}. Wait for the answer, then reply exactly MULTI_DONE.'), true, async event => {
  const update = event.result?.statusUpdate;
  if (update?.status?.state !== "TASK_STATE_INPUT_REQUIRED") return;
  const interrupt = update.metadata.shared.interrupt;
  assert.equal(interrupt.details.questions[0].multiple, true);
  const invalid = await rpc("a2a.interrupt.question.reply", { request_id: interrupt.request_id, answers: [[42]] });
  assert.ok(invalid.error, "invalid typed form answer was accepted");
  const reply = await rpc("a2a.interrupt.question.reply", { request_id: interrupt.request_id, answers: [["Alpha", "Beta"]] });
  assert.ok(!reply.error);
  multiselectReplies++;
});
assert.equal(multiselectReplies, 1);
assert.ok(artifacts(multiselect).includes("MULTI_DONE"));
assert.ok(multiselect.some(event => event.result?.statusUpdate?.status?.state === "TASK_STATE_COMPLETED"));
for (const decision of ["once", "reject"]) {
  stage = `permission-${decision}`;
  let permissions = 0;
  const permissionStream = await rpc("SendStreamingMessage", message("Use the read tool to read /probe/workspace/acceptance.txt. Wait for permission. If allowed, report its exact contents; if denied, reply DENIED. Do not use any other tool."), true, async event => {
    const update = event.result?.statusUpdate;
    const interrupt = update?.metadata?.shared?.interrupt;
    if (update?.status?.state !== "TASK_STATE_INPUT_REQUIRED") return;
    assert.equal(interrupt.type, "permission");
    const reply = await rpc("a2a.interrupt.permission.reply", { request_id: interrupt.request_id, reply: decision });
    assert.ok(!reply.error, "authorized permission reply rejected");
    permissions++;
  });
  assert.equal(permissions, 1, "real model did not trigger a permission");
  const completed = permissionStream.some(event => event.result?.statusUpdate?.status?.state === "TASK_STATE_COMPLETED");
  const failed = permissionStream.some(event => event.result?.statusUpdate?.status?.state === "TASK_STATE_FAILED");
  if (decision === "once" && !completed) console.error(JSON.stringify({ decision, states: permissionStream.map(event => event.result?.statusUpdate?.status?.state) }));
  assert.ok(decision === "once" ? completed : completed || failed);
  if (decision === "once") {
    if (!artifacts(permissionStream).includes("SAFE_FILE_CONTENT")) console.error("Expected approved-read output was absent from assistant artifacts");
    assert.ok(artifacts(permissionStream).includes("SAFE_FILE_CONTENT"));
  }
  if (decision === "reject") assert.ok(!artifacts(permissionStream).includes("SAFE_FILE_CONTENT"));
}
let cancelled = false;
stage = "active-cancel";
const cancelledStream = await rpc("SendStreamingMessage", message('Call the question tool and ask "Wait?" with Yes and No choices. Wait for my answer before proceeding.'), true, async event => {
  const update = event.result?.statusUpdate;
  if (update?.status?.state !== "TASK_STATE_INPUT_REQUIRED") return;
  const result = await rpc("CancelTask", { id: update.taskId });
  assert.equal(result.result?.status?.state, "TASK_STATE_CANCELED");
  cancelled = true;
});
assert.ok(cancelled);
assert.ok(!cancelledStream.some(event => event.result?.statusUpdate?.status?.state === "TASK_STATE_COMPLETED"));
const parallel = await Promise.all([rpc("SendMessage", message("Reply exactly PARALLEL_ONE. Do not use tools.")), rpc("SendMessage", message("Reply exactly PARALLEL_TWO. Do not use tools."))]);
for (const result of parallel) assert.equal((result.result?.task ?? result.result)?.status?.state, "TASK_STATE_COMPLETED");
const outputs = parallel.map(result => content((result.result?.task ?? result.result)?.artifacts));
assert.ok(outputs[0].includes("PARALLEL_ONE") && !outputs[0].includes("PARALLEL_TWO"));
assert.ok(outputs[1].includes("PARALLEL_TWO") && !outputs[1].includes("PARALLEL_ONE"));
let queued;
stage = "same-session-contention";
let serialized = false;
let locallyCancelled = false;
const ownerStream = await rpc("SendStreamingMessage", message('Call the question tool once. Ask "Proceed?" with Yes and No choices and wait. After Yes reply exactly OWNER_DONE.'), true, async event => {
  const update = event.result?.statusUpdate;
  if (update?.status?.state !== "TASK_STATE_INPUT_REQUIRED") return;
  const session = update.metadata?.shared?.session?.id;
  assert.ok(session, "intervention has no session binding");
  const before = await evidence();
  const next = message("Reply exactly SERIALIZED_NEXT. Do not use tools.", randomUUID());
  next.metadata = { shared: { session: { id: session } } };
  queued = rpc("SendMessage", next);
  const cancellation = message("Do not execute this locally cancelled request.", randomUUID());
  cancellation.metadata = { shared: { session: { id: session } } };
  const cancelledQueue = await rpc("SendStreamingMessage", cancellation, true, async queuedEvent => {
    const queuedTask = queuedEvent.result?.task;
    if (!queuedTask) return;
    stage = "local-queued-cancel";
    const result = await rpc("CancelTask", { id: queuedTask.id });
    assert.equal(result.result?.status?.state, "TASK_STATE_CANCELED");
    locallyCancelled = true;
    stage = "local-queued-stream-close";
  });
  stage = "owner-form-reply-after-queued-cancel";
  assert.ok(!cancelledQueue.some(item => item.result?.statusUpdate?.status?.state === "TASK_STATE_COMPLETED"));
  await new Promise(resolve => setTimeout(resolve, 100));
  const waiting = await evidence();
  assert.equal(waiting.admissions, before.admissions, "same-session waiter was admitted before owner terminal");
  assert.equal(waiting.routes["POST /api/session/ID/interrupt"] ?? 0, before.routes["POST /api/session/ID/interrupt"] ?? 0, "local queued cancellation interrupted owner");
  serialized = true;
  const reply = await rpc("a2a.interrupt.question.reply", { request_id: update.metadata.shared.interrupt.request_id, answers: [["Yes"]] });
  assert.ok(!reply.error);
});
assert.ok(serialized && locallyCancelled && queued);
assert.ok(artifacts(ownerStream).includes("OWNER_DONE") && !artifacts(ownerStream).includes("SERIALIZED_NEXT"));
const successor = await queued;
assert.equal((successor.result?.task ?? successor.result)?.status?.state, "TASK_STATE_COMPLETED");
assert.ok(content((successor.result?.task ?? successor.result).artifacts).includes("SERIALIZED_NEXT"));
for (const streaming of [false, true]) {
  stage = streaming ? "rest-stream" : "rest-unary";
  const response = await fetch(base + (streaming ? "/message:stream" : "/message:send"), { method: "POST", headers, body: JSON.stringify(message("Reply exactly REST_OUTPUT. Do not use tools.")), signal: AbortSignal.timeout(120000) });
  assert.equal(response.status, 200);
  if (!streaming) {
    const result = await response.json();
    const restTask = result.task ?? result;
    assert.equal(restTask.status?.state, "TASK_STATE_COMPLETED");
    assert.ok(content(restTask.artifacts).includes("REST_OUTPUT"));
    continue;
  }
  const frames = (await response.text()).split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => ({ result: JSON.parse(line.slice(5)) }));
  assert.ok(frames.some(frame => frame.result.statusUpdate?.status?.state === "TASK_STATE_COMPLETED"));
  assert.ok(artifacts(frames).includes("REST_OUTPUT"));
}
const finalEvidence = await evidence();
assert.equal(finalEvidence.event_connections, 1);
assert.deepEqual(finalEvidence.model_refs, ["openai/gpt-6-luna-fast"]);
assert.ok(blockTypes.has("text") && blockTypes.has("tool_call"));
console.log(JSON.stringify({ profile: "V2 exclusive A2A", observed_block_types: [...blockTypes].sort(), checks: { jsonrpc_stream: true, jsonrpc_unary: true, rest_stream: true, rest_unary: true, sequential_context: true, persisted_terminal_task: true, real_form_typed_reply: true, real_multiselect_reply: true, invalid_typed_answer_rejected: true, cross_owner_callback_rejected: true, cross_owner_task_rejected: true, stale_callback_rejected: true, real_permission_allow_deny: true, active_cancellation: true, concurrent_separate_sessions: true, same_session_serialized: true, local_queued_cancellation: true }, upstream: finalEvidence }, null, 2));
