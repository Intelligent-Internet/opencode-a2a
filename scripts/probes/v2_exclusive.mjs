import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";

const password = (await readFile("/probe/password", "utf8")).trim();
const base = "http://127.0.0.1:4096";
const headers = { Authorization: `Basic ${Buffer.from(`opencode:${password}`).toString("base64")}` };
const report = { version: "2.0.28", profile: "exclusive namespace, single-flight", checks: {} };
const checks = report.checks;
const events = [];
const pendingErrors = [];
const controller = new AbortController();
const id = () => `msg_${randomBytes(12).toString("hex")}`;
async function request(path, method = "GET", body, authorized = true) {
  const response = await fetch(base + path, {
    method, headers: { ...(authorized ? headers : {}), "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(20000),
  });
  assert.ok(response.ok, `HTTP ${response.status} ${method} ${path.replace(/ses_[^/]+/g, "SESSION")}`);
  return response.status === 204 ? null : response.json();
}
async function until(predicate, timeout = 90000) {
  const deadline = Date.now() + timeout;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, "event observation deadline exceeded");
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}
async function subscribe() {
  const response = await fetch(base + "/api/event", { headers, signal: controller.signal });
  assert.equal(response.status, 200);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const done = (async () => {
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
        assert.ok(buffer.length < 2_000_000, "SSE frame limit exceeded");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
          if (data) {
            const event = JSON.parse(data);
            events.push({ ...event, properties: event.properties ?? event.data ?? {} });
          }
          assert.ok(events.length < 10000, "event count limit exceeded");
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) throw error;
    } finally { reader.releaseLock(); }
  })();
  await until(() => events.some(event => event.type === "server.connected"), 15000);
  return { done };
}
function sessionEvents(session, offset) {
  return events.slice(offset).filter(event => event.properties?.sessionID === session);
}
const terminal = event => /^session\.execution\.(succeeded|failed|interrupted)$/.test(event.type);
async function idle(session) {
  const active = await request("/api/session/active");
  assert.ok(!active.data[session], "session has foreign or unfinished activity");
  assert.deepEqual((await request(`/api/session/${session}/inbox`)).data, []);
}
async function prompt(session, text) {
  await idle(session);
  const input = id();
  const offset = events.length;
  const receipt = await request(`/api/session/${session}/prompt`, "POST", { id: input, text, delivery: "queue", resume: true });
  assert.equal(receipt.data.id, input);
  await until(() => sessionEvents(session, offset).some(terminal));
  const observed = sessionEvents(session, offset);
  const delivered = observed.findIndex(event => event.type === "session.inbox.delivered" && event.properties.inboxID === input);
  const started = observed.findIndex(event => event.type === "session.step.started");
  const ended = observed.findIndex(terminal);
  assert.ok(delivered >= 0 && started > delivered && ended > started);
  assert.ok(BigInt(observed[ended].durable.seq) > BigInt(observed[delivered].durable.seq));
  assert.equal(observed.filter(terminal).length, 1);
  await idle(session);
  const messages = (await request(`/api/session/${session}/message?order=asc&limit=100`)).data;
  assert.ok(messages.some(message => message.id === input && message.type === "user"));
  const assistantIds = new Set(observed.filter(event => event.type === "session.step.started").map(event => event.properties.assistantMessageID));
  const assistants = messages.filter(message => assistantIds.has(message.id));
  assert.equal(assistants.length, assistantIds.size);
  const output = assistants.flatMap(message => message.content ?? []).filter(part => part.type === "text").map(part => part.text).join("\n");
  return { output, terminal: observed[ended].type, steps: assistantIds.size, input, eventTypes: observed.map(event => event.type) };
}
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    try { if ((await request("/api/info")).version === "2.0.28") break; }
    catch (error) { if (attempt === 99) throw error; }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  const unauthorized = await fetch(base + "/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal: AbortSignal.timeout(3000) });
  assert.equal(unauthorized.status, 401);
  checks.unauthenticated_writer_rejected = true;
  const wrongCredential = await fetch(base + "/api/session", { method: "POST", headers: { Authorization: "Basic dGVzdDp3cm9uZw==", "Content-Type": "application/json" }, body: "{}", signal: AbortSignal.timeout(3000) });
  assert.equal(wrongCredential.status, 401);
  checks.wrong_credential_writer_rejected = true;
  const denied = [{ action: "*", resource: "*", effect: "deny" }, { action: "external_directory", resource: "*", effect: "deny" }];
  async function create(permissions = denied) {
    return (await request("/api/session", "POST", { location: { directory: "/probe/workspace" }, model: { providerID: "openai", id: "gpt-6-luna-fast" }, permissions })).data.id;
  }
  // Reader is established before the first admission and reused for both sessions.
  const reader = await subscribe();
  checks.shared_event_subscription_ready = true;
  const session = await create();
  const effectiveConfig = await request("/api/config?location[directory]=/probe/workspace");
  assert.ok(Array.isArray(effectiveConfig));
  assert.ok(effectiveConfig.filter(entry => entry.type === "document").every(entry => !entry.info.warming && !entry.info.mcp?.servers && !entry.info.plugins?.length));
  const plugins = await request("/api/plugin?location[directory]=/probe/workspace");
  assert.ok(plugins.data.every(plugin => plugin.source.type === "builtin"));
  checks.no_custom_plugins_mcp_or_warming_in_effective_config = true;
  const marker = `exclusive_${randomBytes(4).toString("hex")}`;
  const first = await prompt(session, `Remember the token ${marker}. Reply with exactly ${marker}. Do not use tools.`);
  assert.equal(first.terminal, "session.execution.succeeded");
  assert.equal(first.output.trim(), marker);
  checks.single_prompt_delivered_step_terminal_and_persisted_output = true;
  const second = await prompt(session, "What token did I ask you to remember? Reply with that token only. Do not use tools.");
  assert.notEqual(first.input, second.input);
  assert.equal(second.terminal, "session.execution.succeeded");
  assert.equal(second.output.trim(), marker);
  checks.sequential_followup_retains_context_and_has_new_delivery_before_terminal = true;
  const other = await create();
  const parallel = await Promise.all([prompt(session, "Reply exactly session-one. Do not use tools."), prompt(other, "Reply exactly session-two. Do not use tools.")]);
  assert.ok(parallel[0].output.includes("session-one"));
  assert.ok(parallel[1].output.includes("session-two"));
  assert.ok(!parallel[0].output.includes("session-two") && !parallel[1].output.includes("session-one"));
  checks.concurrent_distinct_sessions_isolated = true;
  const interactive = await create([...denied, { action: "question", resource: "*", effect: "allow" }]);
  const questionOffset = events.length;
  const question = prompt(interactive, 'Use the question tool exactly once. Ask "Continue?" with Yes and No options. Wait for the answer. Then reply exactly FORM_DONE. Do not just write the question as text.');
  question.catch(error => pendingErrors.push(error.name));
  await until(() => events.slice(questionOffset).some(event => event.type === "form.created" && event.properties.form?.sessionID === interactive));
  const form = events.slice(questionOffset).find(event => event.type === "form.created" && event.properties.form?.sessionID === interactive).properties.form;
  assert.ok(form.fields.length > 0 && form.fields.every(field => field.type === "string"));
  await request(`/api/session/${interactive}/form/${form.id}/reply`, "POST", { answer: Object.fromEntries(form.fields.map(field => [field.key, "Yes"])) });
  const answered = await question;
  assert.equal(answered.terminal, "session.execution.succeeded");
  assert.ok(answered.output.includes("FORM_DONE"));
  checks.model_question_form_and_typed_reply = true;
  const cancelledSession = await create([...denied, { action: "question", resource: "*", effect: "allow" }]);
  const cancellationOffset = events.length;
  const waiting = prompt(cancelledSession, 'Call the question tool and ask "Wait?" with Yes and No choices. Wait for my answer before proceeding.');
  waiting.catch(error => pendingErrors.push(error.name));
  await until(() => events.slice(cancellationOffset).some(event => event.type === "form.created" && event.properties.form?.sessionID === cancelledSession));
  const interrupt = await request(`/api/session/${cancelledSession}/interrupt?resume=false`, "POST");
  assert.equal(interrupt.interrupted, true);
  const interrupted = await waiting;
  assert.equal(interrupted.terminal, "session.execution.interrupted");
  checks.active_cancel_observed_interruption_and_settlement = true;
  assert.deepEqual(pendingErrors, []);
  report.event_types_first = first.eventTypes;
  report.event_types_followup = second.eventTypes;
  checks.mid_execution_disconnect = "NOT VERIFIED";
  checks.cancellation_race = "NOT VERIFIED (settled active cancel only)";
  checks.gateway_same_session_serialization = "NOT VERIFIED (native probe only)";
  controller.abort();
  await reader.done;
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  controller.abort();
  console.error(JSON.stringify({ observed: events.map(event => ({ type: event.type, keys: Object.keys(event.properties), envelope: Object.keys(event) })) }));
  console.error(`exclusive probe failed: ${error.name}: ${error.message}`);
  process.exitCode = 1;
}
