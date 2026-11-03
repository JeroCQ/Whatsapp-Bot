/** Server-only pilot gateway. Never import this module from a browser component. */
import { createHmac, timingSafeEqual } from "node:crypto";

type Env = Record<string, string | undefined>;
type Fetcher = typeof fetch;
interface Config {
  business: string;
  actor: string;
  operatorSecret: string;
  jwtSecret: string;
  origin: string;
}
const PREFIX = "/api/public/crm-pilot/";
const COMMON_HEADERS = {
  "Content-Type": "application/json",
  "Cache-Control": "no-store",
  Vary: "Authorization",
};
const COMMAND_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$/;
const INBOX_PARAMS = new Set([
  "bucket",
  "search",
  "cursor",
  "limit",
  "priority",
  "assigned_to",
  "tag",
  "stage",
]);
function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: COMMON_HEADERS });
}
function equal(left: string, right: string): boolean {
  const a = Buffer.from(left),
    b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}
function config(env: Env): Config | null {
  if (env["CRM_PILOT_ENABLED"] !== "true") return null;
  const business = env["CRM_PILOT_BUSINESS_ID"] ?? "briosos";
  const actor = env["CRM_PILOT_OPERATOR_ID"] ?? "";
  const operatorSecret = env["CRM_PILOT_OPERATOR_SECRET"] ?? "";
  const jwtSecret = env["CRM_JWT_SECRET"] ?? "";
  if (
    !/^[a-z][a-z0-9_]{0,62}$/.test(business) ||
    !/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,199}$/.test(actor) ||
    actor === business ||
    ["admin", "tanaka", "memos", "velvet"].includes(actor) ||
    operatorSecret.length < 32 ||
    jwtSecret.length < 32 ||
    equal(operatorSecret, jwtSecret) ||
    Object.entries(env).some(
      ([key, value]) => key.startsWith("PASSWORD_") && value && equal(operatorSecret, value),
    )
  ) {
    throw new Error("pilot_configuration_invalid");
  }
  const target = new URL(env["CRM_BACKEND_URL"] ?? "");
  if (
    target.protocol !== "https:" ||
    target.username ||
    target.password ||
    target.search ||
    target.hash ||
    !["", "/"].includes(target.pathname)
  ) {
    throw new Error("pilot_origin_invalid");
  }
  return { business, actor, operatorSecret, jwtSecret, origin: target.origin };
}
function jwt(settings: Config): string {
  const at = Math.floor(Date.now() / 1000);
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const head = part({ alg: "HS256", typ: "JWT" });
  const body = part({
    iss: "chatty-catalog-compass",
    aud: "whatsapp-crm",
    profile: settings.actor,
    allowed_businesses: [settings.business],
    iat: at,
    exp: at + 120,
  });
  const signature = createHmac("sha256", settings.jwtSecret)
    .update(`${head}.${body}`)
    .digest("base64url");
  return `${head}.${body}.${signature}`;
}
interface Destination {
  path: string;
  methods: string[];
  query: Set<string>;
  body?: Set<string>;
  command?: boolean;
}
function destination(path: string, business: string): Destination | null {
  if (path === "capabilities")
    return { path: "/v1/capabilities", methods: ["GET"], query: new Set() };
  if (path === "inbox") return { path: "/v1/inbox", methods: ["GET"], query: INBOX_PARAMS };
  const match =
    /^conversations\/([^/]+)(?:\/(control|take|messages|close|read|commands\/([^/]+)))?$/.exec(
      path,
    );
  if (!match?.[1]) return null;
  let phone: string;
  try {
    phone = decodeURIComponent(match[1]);
  } catch {
    return null;
  }
  if (!/^[A-Za-z0-9_.:+-]{1,200}$/.test(phone)) return null;
  const base = `/v1/conversations/${encodeURIComponent(business)}/${encodeURIComponent(phone)}`;
  const suffix = match[2];
  if (!suffix) return { path: base, methods: ["GET"], query: new Set(["cursor", "limit"]) };
  if (suffix === "control") return { path: base + "/control", methods: ["GET"], query: new Set() };
  if (suffix.startsWith("commands/")) {
    if (!match[3] || !COMMAND_ID.test(match[3])) return null;
    return {
      path: base + "/commands/" + encodeURIComponent(match[3]),
      methods: ["GET"],
      query: new Set(),
    };
  }
  const body =
    suffix === "read"
      ? new Set(["read"])
      : new Set(["command_id", "expected_version", suffix === "messages" ? "text" : "reason"]);
  return {
    path: base + "/" + suffix,
    methods: ["POST"],
    query: new Set(),
    body,
    command: suffix !== "read",
  };
}
export async function handleCrmPilot(
  request: Request,
  env: Env,
  fetcher: Fetcher = fetch,
): Promise<Response> {
  const incoming = new URL(request.url);
  if (!incoming.pathname.startsWith(PREFIX)) return json({ detail: "Not found" }, 404);
  const path = incoming.pathname.slice(PREFIX.length);
  let settings: Config | null;
  try {
    settings = config(env);
  } catch {
    return json({ detail: "El piloto no está configurado correctamente." }, 503);
  }
  if (path === "availability" && request.method === "GET") {
    return json({ enabled: !!settings, business_id: settings?.business ?? null });
  }
  if (!settings) return json({ detail: "El piloto CRM está deshabilitado." }, 404);
  const origin = request.headers.get("origin");
  const site = request.headers.get("sec-fetch-site");
  if ((origin && origin !== incoming.origin) || (site && !["same-origin", "none"].includes(site))) {
    return json({ detail: "Solo se permiten llamadas desde este panel." }, 403);
  }
  const authorization = request.headers.get("authorization") ?? "";
  if (
    !authorization.startsWith("Bearer ") ||
    !equal(authorization.slice(7), settings.operatorSecret)
  ) {
    return json({ detail: "Credencial personal de piloto inválida." }, 401);
  }
  if (path === "session") {
    if (request.method !== "POST" || incoming.search)
      return json({ detail: "Método no permitido." }, 405);
    return json({ profile: settings.actor, business_id: settings.business });
  }
  const route = destination(path, settings.business);
  if (!route) return json({ detail: "Endpoint no permitido." }, 404);
  if (!route.methods.includes(request.method)) return json({ detail: "Método no permitido." }, 405);
  const target = new URL(route.path, settings.origin);
  for (const [key, value] of incoming.searchParams) {
    if (key === "business_id") {
      if (value !== settings.business)
        return json({ detail: "Empresa fuera del alcance del piloto." }, 403);
    } else if (!route.query.has(key)) return json({ detail: "Parámetro no permitido." }, 400);
    else target.searchParams.append(key, value);
  }
  if (path === "inbox") target.searchParams.set("business_id", settings.business);
  let body: string | undefined;
  let commandId: string | undefined;
  if (request.method !== "GET") {
    let input: unknown;
    try {
      input = await request.json();
    } catch {
      return json({ detail: "JSON inválido." }, 400);
    }
    if (!input || typeof input !== "object" || Array.isArray(input))
      return json({ detail: "Cuerpo inválido." }, 400);
    const payload = input as Record<string, unknown>;
    if (Object.keys(payload).some((key) => !route.body?.has(key)))
      return json({ detail: "Campo no permitido." }, 400);
    if (route.command) {
      if (typeof payload["command_id"] !== "string" || !COMMAND_ID.test(payload["command_id"])) {
        return json({ detail: "Identificador de comando inválido." }, 422);
      }
      commandId = payload["command_id"];
    }
    body = JSON.stringify(payload);
  }
  const headers: Record<string, string> = {
    Authorization: "Bearer " + jwt(settings),
    Accept: "application/json",
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // Build headers from scratch: Origin, Sec-Fetch-Site, the personal credential,
  // cookies and client-supplied destination/actor never reach the CRM.
  try {
    const upstream = await fetcher(target, {
      method: request.method,
      headers,
      ...(body === undefined ? {} : { body }),
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(55_000),
    });
    const data: unknown = await upstream.json();
    if (upstream.status >= 500) throw new Error("upstream_unavailable");
    return json(data, upstream.status);
  } catch {
    // The CRM may already have forwarded a POST. Never retry here or in the UI.
    if (commandId)
      return json(
        {
          command_id: commandId,
          status: "unknown",
          control: null,
          provider_message_id: null,
          error: "proxy_outcome_unknown_query_command",
        },
        202,
      );
    return json({ detail: "No se pudo consultar el CRM. No se cambia el control del bot." }, 503);
  }
}
