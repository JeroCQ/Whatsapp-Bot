const allowedOrigin = (Deno.env.get("DASHBOARD_FRONTEND_ORIGIN") ?? "").replace(/\/$/, "");

type ClientConfig = { password: string; backendUrl: string; apiKey: string };

function env(primary: string, legacy: string): string {
  return Deno.env.get(primary) ?? Deno.env.get(legacy) ?? "";
}

const clients: Record<string, ClientConfig> = Object.fromEntries(
  ["tanaka", "memos", "velvet"].map((client) => {
    const prefix = client.toUpperCase();
    return [client, {
      password: env(`PASSWORD_${prefix}`, `${prefix}_DASHBOARD_PASSWORD`),
      backendUrl: env(`${prefix}_DASHBOARD_BACKEND_URL`, "DASHBOARD_BACKEND_URL").replace(/\/$/, ""),
      apiKey: env(`${prefix}_DASHBOARD_API_KEY`, "DASHBOARD_API_KEY"),
    }];
  }),
);

function corsHeaders(origin: string | null): HeadersInit {
  const headers: Record<string, string> = {
    "Access-Control-Allow-Headers": "authorization, content-type, x-dashboard-password",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Vary": "Origin",
  };
  if (origin && origin.replace(/\/$/, "") === allowedOrigin) headers["Access-Control-Allow-Origin"] = origin;
  return headers;
}

function jsonResponse(status: number, body: Record<string, unknown>, cors: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

async function passwordMatches(received: string, expected: string): Promise<boolean> {
  if (!received || !expected) return false;
  const encoder = new TextEncoder();
  const [receivedHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(received)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const left = new Uint8Array(receivedHash);
  const right = new Uint8Array(expectedHash);
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left[index] ^ right[index];
  return difference === 0;
}

async function authenticatedClient(password: string): Promise<string | null> {
  let matched: string | null = null;
  for (const [client, config] of Object.entries(clients)) {
    if (await passwordMatches(password, config.password)) matched = client;
  }
  return matched;
}

function requestedRoute(url: URL): string {
  const marker = "/dashboard-api/";
  const index = url.pathname.indexOf(marker);
  return index >= 0 ? url.pathname.slice(index + marker.length).replace(/^\/+|\/+$/g, "") : "";
}

function routeAllowed(route: string, method: string): boolean {
  if (route === "login") return method === "POST";
  if (["generate-si-changes", "format-and-save-si", "upload-catalog"].includes(route)) return method === "POST";
  if (["current-si", "si-history", "dashboard-health", "current-catalog", "catalog-prompt-preview"].includes(route)) return method === "GET";
  if (route === "catalogs") return ["GET", "POST"].includes(method);
  if (/^catalogs\/catalogo_[a-z0-9_]{1,52}$/.test(route)) return ["PATCH", "DELETE"].includes(method);
  if (/^catalogs\/catalogo_[a-z0-9_]{1,52}\/file$/.test(route)) return ["GET", "POST"].includes(method);
  return false;
}

Deno.serve(async (request) => {
  const origin = request.headers.get("origin");
  const cors = corsHeaders(origin);
  if (request.method === "OPTIONS") {
    const allowed = origin && origin.replace(/\/$/, "") === allowedOrigin;
    return new Response(null, { status: allowed ? 204 : 403, headers: cors });
  }
  if (!allowedOrigin || Object.values(clients).some((item) => !item.password || !item.backendUrl || !item.apiKey)) {
    return jsonResponse(500, { detail: "El proxy administrativo no está configurado" }, cors);
  }

  const incomingUrl = new URL(request.url);
  const route = requestedRoute(incomingUrl);
  if (!routeAllowed(route, request.method)) return jsonResponse(404, { detail: "Ruta administrativa desconocida" }, cors);

  const password = request.headers.get("authorization") ?? request.headers.get("x-dashboard-password") ?? "";
  const client = await authenticatedClient(password.replace(/^Bearer\s+/i, ""));
  if (!client) return jsonResponse(401, { detail: "Contraseña incorrecta" }, cors);
  if (route === "login") return jsonResponse(200, { success: true, client_name: client }, cors);

  const clientConfig = clients[client];
  incomingUrl.searchParams.set("client_name", client);
  const contentType = request.headers.get("content-type") ?? "";
  let body: BodyInit | undefined;
  try {
    if (request.method !== "GET" && request.method !== "HEAD") {
      if (contentType.includes("application/json")) {
        const payload = await request.json();
        if (payload && typeof payload === "object") delete payload.client_name;
        body = JSON.stringify(payload);
      } else if (contentType.includes("multipart/form-data")) {
        const form = await request.formData();
        form.delete("client_name");
        form.append("client_name", client);
        body = form;
      } else {
        body = await request.arrayBuffer();
      }
    }
  } catch {
    return jsonResponse(422, { detail: "Solicitud inválida" }, cors);
  }

  const target = `${clientConfig.backendUrl}/api/${route}?${incomingUrl.searchParams.toString()}`;
  const headers = new Headers({
    "X-Dashboard-API-Key": clientConfig.apiKey,
    "X-Client-Name": client,
  });
  if (contentType.includes("application/json")) headers.set("Content-Type", "application/json");
  try {
    const upstream = await fetch(target, { method: request.method, headers, body });
    if (upstream.status >= 500) {
      const detail = await upstream.text();
      return jsonResponse(424, { detail: detail || "El backend administrativo falló" }, cors);
    }
    const responseHeaders = new Headers(cors);
    responseHeaders.set("Content-Type", upstream.headers.get("content-type") ?? "application/json");
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return jsonResponse(502, { detail: "No fue posible comunicarse con el backend administrativo" }, cors);
  }
});
