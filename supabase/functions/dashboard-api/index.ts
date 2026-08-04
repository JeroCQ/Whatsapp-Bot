const backendUrl = (Deno.env.get("DASHBOARD_BACKEND_URL") ?? "").replace(/\/$/, "");
const dashboardKey = Deno.env.get("DASHBOARD_API_KEY") ?? "";
const allowedOrigin = (Deno.env.get("DASHBOARD_FRONTEND_ORIGIN") ?? "").replace(/\/$/, "");

const clientPasswords: Record<string, string> = {
  tanaka: Deno.env.get("TANAKA_DASHBOARD_PASSWORD") ?? "",
  memos: Deno.env.get("MEMOS_DASHBOARD_PASSWORD") ?? "",
};
const allowedRoutes = new Set([
  "login",
  "generate-si-changes",
  "format-and-save-si",
  "si-history",
  "upload-catalog",
]);

function corsHeaders(origin: string | null): HeadersInit {
  const headers: Record<string, string> = {
    "Access-Control-Allow-Headers": "content-type, x-dashboard-password",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Vary": "Origin",
  };
  if (origin && origin.replace(/\/$/, "") === allowedOrigin) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
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
  // Check every configured password so the matching client is not revealed by timing.
  let matched: string | null = null;
  for (const [client, expected] of Object.entries(clientPasswords)) {
    if (await passwordMatches(password, expected)) matched = client;
  }
  return matched;
}

function requestedRoute(url: URL): string {
  const marker = "/dashboard-api/";
  const markerIndex = url.pathname.indexOf(marker);
  return markerIndex >= 0
    ? url.pathname.slice(markerIndex + marker.length).replace(/^\/+|\/+$/g, "")
    : "";
}

Deno.serve(async (request) => {
  const origin = request.headers.get("origin");
  const cors = corsHeaders(origin);
  if (request.method === "OPTIONS") {
    const allowed = origin && origin.replace(/\/$/, "") === allowedOrigin;
    return new Response(null, { status: allowed ? 204 : 403, headers: cors });
  }
  if (!backendUrl || !dashboardKey || !allowedOrigin || Object.values(clientPasswords).some((value) => !value)) {
    return jsonResponse(500, { detail: "El proxy administrativo no está configurado" }, cors);
  }

  const incomingUrl = new URL(request.url);
  const route = requestedRoute(incomingUrl);
  if (!allowedRoutes.has(route)) {
    return jsonResponse(404, { detail: "Ruta administrativa desconocida" }, cors);
  }

  const client = await authenticatedClient(request.headers.get("x-dashboard-password") ?? "");
  if (!client) {
    return jsonResponse(401, { detail: "Contraseña incorrecta" }, cors);
  }
  if (route === "login") {
    return jsonResponse(200, { success: true, client_name: client }, cors);
  }

  const contentType = request.headers.get("content-type") ?? "";
  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const validationCopy = request.clone();
    body = await request.arrayBuffer();
    try {
      if (contentType.includes("application/json") && route === "format-and-save-si") {
        const payload = await validationCopy.json();
        if (payload.client_name !== client) {
          return jsonResponse(403, { detail: "La contraseña no permite administrar ese cliente" }, cors);
        }
      } else if (contentType.includes("multipart/form-data") && route === "upload-catalog") {
        const form = await validationCopy.formData();
        if (form.get("client_name") !== client) {
          return jsonResponse(403, { detail: "La contraseña no permite administrar ese cliente" }, cors);
        }
      }
    } catch {
      return jsonResponse(422, { detail: "Solicitud inválida" }, cors);
    }
  }
  if (route === "si-history" && incomingUrl.searchParams.get("client_name") !== client) {
    return jsonResponse(403, { detail: "La contraseña no permite administrar ese cliente" }, cors);
  }

  const target = `${backendUrl}/api/${route}${incomingUrl.search}`;
  const headers = new Headers({ "X-Dashboard-API-Key": dashboardKey });
  if (contentType) headers.set("Content-Type", contentType);
  try {
    const upstream = await fetch(target, { method: request.method, headers, body });
    const responseHeaders = new Headers(cors);
    responseHeaders.set("Content-Type", upstream.headers.get("content-type") ?? "application/json");
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return jsonResponse(502, { detail: "No fue posible comunicarse con el backend administrativo" }, cors);
  }
});
