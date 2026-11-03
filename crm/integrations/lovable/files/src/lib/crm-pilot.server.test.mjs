import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { handleCrmPilot } from "./crm-pilot.server.ts";
import { canCompose, commandUnsettled } from "./crm-pilot-contract.ts";
const env = {
  CRM_PILOT_ENABLED: "true",
  CRM_PILOT_BUSINESS_ID: "briosos",
  CRM_PILOT_OPERATOR_ID: "employee-pilot-001",
  CRM_PILOT_OPERATOR_SECRET: "synthetic-personal-" + "p".repeat(40),
  CRM_JWT_SECRET: "synthetic-jwt-" + "j".repeat(40),
  CRM_BACKEND_URL: "https://crm.example",
};
const base = "https://frontend.example/api/public/crm-pilot/";
function incoming(
  path,
  { method = "GET", body, auth = env.CRM_PILOT_OPERATOR_SECRET, headers = {} } = {},
) {
  return new Request(base + path, {
    method,
    headers: {
      Authorization: "Bearer " + auth,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...headers,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}
const noFetch = () => {
  throw new Error("unexpected external request");
};
test("pilot is disabled by default and a shared brand profile is not an individual", async () => {
  const disabled = await handleCrmPilot(incoming("availability"), {}, noFetch);
  assert.deepEqual(await disabled.json(), { enabled: false, business_id: null });
  assert.equal((await handleCrmPilot(incoming("inbox"), {}, noFetch)).status, 404);
  for (const actor of ["briosos", "tanaka", "admin"]) {
    assert.equal(
      (
        await handleCrmPilot(
          incoming("session", { method: "POST", body: {} }),
          { ...env, CRM_PILOT_OPERATOR_ID: actor },
          noFetch,
        )
      ).status,
      503,
    );
  }
  assert.equal(
    (
      await handleCrmPilot(
        incoming("inbox"),
        { ...env, PASSWORD_ADMIN: env.CRM_PILOT_OPERATOR_SECRET },
        noFetch,
      )
    ).status,
    503,
  );
});
test("dedicated credential authenticates only the configured individual", async () => {
  assert.equal(
    (
      await handleCrmPilot(
        incoming("session", { method: "POST", body: {}, auth: "wrong" }),
        env,
        noFetch,
      )
    ).status,
    401,
  );
  const response = await handleCrmPilot(
    incoming("session", { method: "POST", body: {} }),
    env,
    noFetch,
  );
  assert.deepEqual(await response.json(), {
    profile: "employee-pilot-001",
    business_id: "briosos",
  });
  assert.equal(response.headers.get("cache-control"), "no-store");
});
test("proxy signs exact short HS256 claims and strips all browser credentials/headers", async () => {
  let calls = 0;
  const response = await handleCrmPilot(
    incoming("inbox?bucket=awaiting_human&limit=1", {
      headers: {
        Origin: "https://frontend.example",
        "Sec-Fetch-Site": "same-origin",
        Cookie: "sensitive=private",
        "X-Bot": "tanaka",
        "X-CRM-Bot-Secret": "untrusted",
        "X-CRM-Source-Secret": "untrusted",
      },
    }),
    env,
    async (url, init) => {
      calls++;
      assert.equal(url.origin, "https://crm.example");
      assert.equal(url.pathname, "/v1/inbox");
      assert.equal(url.searchParams.get("business_id"), "briosos");
      const headers = new Headers(init.headers);
      for (const name of [
        "origin",
        "sec-fetch-site",
        "cookie",
        "x-bot",
        "x-crm-bot-secret",
        "x-crm-source-secret",
      ])
        assert.equal(headers.has(name), false);
      const token = headers.get("authorization").slice(7);
      assert.equal(token.includes(env.CRM_PILOT_OPERATOR_SECRET), false);
      const [header, body, signature] = token.split(".");
      assert.deepEqual(JSON.parse(Buffer.from(header, "base64url")), { alg: "HS256", typ: "JWT" });
      const claims = JSON.parse(Buffer.from(body, "base64url"));
      assert.equal(claims.iss, "chatty-catalog-compass");
      assert.equal(claims.aud, "whatsapp-crm");
      assert.equal(claims.profile, "employee-pilot-001");
      assert.deepEqual(claims.allowed_businesses, ["briosos"]);
      assert.equal(claims.exp - claims.iat, 120);
      assert.ok(Math.abs(claims.iat - Math.floor(Date.now() / 1000)) <= 1);
      assert.equal(
        signature,
        createHmac("sha256", env.CRM_JWT_SECRET).update(`${header}.${body}`).digest("base64url"),
      );
      assert.equal(init.redirect, "error");
      assert.equal(init.cache, "no-store");
      return Response.json({ items: [], next_cursor: null });
    },
  );
  assert.equal(response.status, 200);
  assert.equal(calls, 1);
});
test("cross-site and cross-tenant input cannot select an upstream destination", async () => {
  assert.equal(
    (
      await handleCrmPilot(
        incoming("inbox", { headers: { Origin: "https://attacker.example" } }),
        env,
        noFetch,
      )
    ).status,
    403,
  );
  assert.equal(
    (
      await handleCrmPilot(
        incoming("inbox", { headers: { "Sec-Fetch-Site": "cross-site" } }),
        env,
        noFetch,
      )
    ).status,
    403,
  );
  for (const path of ["inbox?business_id=tanaka", "inbox?business_id=briosos&business_id=velvet"]) {
    assert.equal((await handleCrmPilot(incoming(path), env, noFetch)).status, 403);
  }
  for (const path of ["inbox?backend_url=https://attacker.example", "inbox?actor=admin"]) {
    assert.equal((await handleCrmPilot(incoming(path), env, noFetch)).status, 400);
  }
  for (const path of [
    "internal/sync",
    "https://attacker.example",
    "conversations/contact%2Fother/control",
    "conversations/contact%252Fother/control",
    "conversations/contact/commands/not/valid",
  ]) {
    assert.equal((await handleCrmPilot(incoming(path), env, noFetch)).status, 404);
  }
});
test("method and body allowlists prohibit extra actor/credential/URL fields", async () => {
  assert.equal(
    (
      await handleCrmPilot(
        incoming("conversations/573000000001/control", { method: "POST", body: {} }),
        env,
        noFetch,
      )
    ).status,
    405,
  );
  assert.equal(
    (await handleCrmPilot(incoming("conversations/573000000001/messages"), env, noFetch)).status,
    405,
  );
  for (const extra of [
    "actor",
    "business_id",
    "url",
    "credentials",
    "expected_business",
    "backend_url",
  ]) {
    const body = {
      command_id: "synthetic-command-1",
      expected_version: 3,
      text: "SYNTHETIC answer",
      [extra]: "untrusted",
    };
    assert.equal(
      (
        await handleCrmPilot(
          incoming("conversations/573000000001/messages", { method: "POST", body }),
          env,
          noFetch,
        )
      ).status,
      400,
    );
  }
});
test("opaque contact identifiers remain intact and a failed send is never called delivered", async () => {
  const response = await handleCrmPilot(
    incoming("conversations/CO.SyntheticID/messages", {
      method: "POST",
      body: { command_id: "synthetic-command-2", expected_version: 3, text: "SYNTHETIC answer" },
    }),
    env,
    async (url, init) => {
      assert.equal(url.pathname, "/v1/conversations/briosos/CO.SyntheticID/messages");
      assert.deepEqual(JSON.parse(init.body), {
        command_id: "synthetic-command-2",
        expected_version: 3,
        text: "SYNTHETIC answer",
      });
      return Response.json({ command_id: "synthetic-command-2", status: "failed", control: null });
    },
  );
  assert.equal(response.status, 200);
  assert.equal((await response.json()).status, "failed");
});
test("ambiguous POST is attempted once and becomes query-only unknown", async () => {
  let calls = 0;
  const response = await handleCrmPilot(
    incoming("conversations/573000000001/messages", {
      method: "POST",
      body: { command_id: "synthetic-timeout", expected_version: 3, text: "SYNTHETIC answer" },
    }),
    env,
    async () => {
      calls++;
      throw new TypeError("synthetic network timeout");
    },
  );
  assert.equal(calls, 1);
  assert.equal(response.status, 202);
  assert.deepEqual(await response.json(), {
    command_id: "synthetic-timeout",
    status: "unknown",
    control: null,
    provider_message_id: null,
    error: "proxy_outcome_unknown_query_command",
  });
});
test("server rejects unsafe origin configuration instead of following a redirect", async () => {
  for (const url of [
    "http://crm.example",
    "https://user:password@crm.example",
    "https://crm.example/api",
    "https://crm.example?token=private",
  ]) {
    assert.equal(
      (await handleCrmPilot(incoming("inbox"), { ...env, CRM_BACKEND_URL: url }, noFetch)).status,
      503,
    );
  }
});
test("composer requires confirmed current ownership, pause and a sendable window", () => {
  const control = {
    business_id: "briosos",
    phone_number: "573000000001",
    provider: "crm",
    phase: "human",
    version: 3,
    owner: "employee-pilot-001",
    is_paused: true,
    confirmed: true,
    can_send: true,
    window_expires_at: "2030-01-01T00:00:00Z",
  };
  assert.equal(canCompose(control, "employee-pilot-001", null), true);
  for (const change of [
    { phase: "pausing" },
    { owner: "other" },
    { is_paused: false },
    { confirmed: false },
    { can_send: false },
  ]) {
    assert.equal(canCompose({ ...control, ...change }, "employee-pilot-001", null), false);
  }
  for (const status of ["unknown", "pending"]) {
    const command = { command_id: "synthetic", status, control: null };
    assert.equal(commandUnsettled(command), true);
    assert.equal(canCompose(control, "employee-pilot-001", command), false);
  }
  assert.equal(canCompose(control, "other", null), false);
});

test("rotating the personal pilot credential revokes it on the next request", async () => {
  const changed = { ...env, CRM_PILOT_OPERATOR_SECRET: "synthetic-rotated-" + "r".repeat(40) };
  const response = await handleCrmPilot(incoming("inbox"), changed, noFetch);
  assert.equal(response.status, 401);
});
