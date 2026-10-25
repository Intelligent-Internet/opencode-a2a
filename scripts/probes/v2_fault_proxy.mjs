// Test-only fault injection. Never deploy this control endpoint with the gateway.
import http from "node:http";
import { readFile } from "node:fs/promises";

const token = (await readFile("/probe/a2a-token", "utf8")).trim();
const mode = process.env.V2_PROBE_CASE ?? "normal";
const evidence = { mode, admissions: 0, event_connections: 0, injected: false, terminal_types: [], model_refs: [], tool_failures: {}, routes: {} };
const eventResponses = new Set();
const toolNames = new Map();
const terminal = type => /^session\.execution\.(succeeded|failed|interrupted)$/.test(type);
const server = http.createServer(async (request, response) => {
  if (request.url === "/__probe/evidence" || request.url === "/__probe/disconnect") {
    if (request.headers.authorization !== `Bearer ${token}`) { response.writeHead(401).end(); return; }
    if (request.url === "/__probe/disconnect") {
      evidence.injected = true;
      for (const stream of eventResponses) stream.destroy();
    }
    response.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(evidence));
    return;
  }
  const url = new URL(request.url, "http://127.0.0.1:4096");
  const route = `${request.method} ${url.pathname.replace(/(?:ses|msg|frm|per)_[^/]+/g, "ID")}`;
  evidence.routes[route] = (evidence.routes[route] ?? 0) + 1;
  if (request.method === "POST" && url.pathname.endsWith("/prompt")) evidence.admissions++;
  try {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const upstream = await fetch(url, { method: request.method, headers: { ...request.headers, host: url.host }, body: chunks.length ? Buffer.concat(chunks) : undefined });
    if (url.pathname.endsWith("/prompt") && mode === "lost-admission") {
      await upstream.arrayBuffer();
      evidence.injected = true;
      response.destroy();
      return;
    }
    response.writeHead(upstream.status, { "Content-Type": upstream.headers.get("content-type") ?? "application/json" });
    if (url.pathname !== "/api/event") {
      const body = Buffer.from(await upstream.arrayBuffer());
      if (request.method === "POST" && url.pathname === "/api/session" && upstream.ok) {
        const model = JSON.parse(body).data?.model;
        if (model && typeof model.providerID === "string" && typeof model.id === "string") {
          const ref = `${model.providerID}/${model.id}`;
          if (!evidence.model_refs.includes(ref)) evidence.model_refs.push(ref);
        }
      }
      response.end(body);
      return;
    }
    evidence.event_connections++;
    eventResponses.add(response);
    const reader = upstream.body.getReader();
    response.on("close", () => { eventResponses.delete(response); reader.cancel().catch(error => console.error(`proxy reader cleanup: ${error.name}`)); });
    let buffer = "";
    const decoder = new TextDecoder();
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, "\n");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        const event = data ? JSON.parse(data) : null;
        if (event?.type === "session.tool.input.started") toolNames.set(event.data.id, event.data.name);
        if (event?.type === "session.tool.failed") {
          const name = toolNames.get(event.data.id) ?? "unknown";
          evidence.tool_failures[name] = (evidence.tool_failures[name] ?? 0) + 1;
        }
        if (event?.type === "session.step.started") {
          const model = event.data?.model;
          if (model && typeof model.providerID === "string" && typeof model.id === "string") {
            const ref = `${model.providerID}/${model.id}`;
            if (!evidence.model_refs.includes(ref)) evidence.model_refs.push(ref);
          }
        }
        if (event && terminal(event.type)) evidence.terminal_types.push(event.type);
        if (event && terminal(event.type) && mode === "lost-terminal") { evidence.injected = true; continue; }
        response.write(frame + "\n\n");
        if (event?.type === "session.step.started" && mode === "gap") {
          evidence.injected = true;
          response.destroy();
          return;
        }
      }
    }
    response.end();
  } catch (error) {
    console.error(`proxy request failed: ${error.name}`);
    response.destroy();
  }
});
server.listen(4097, "127.0.0.1");
