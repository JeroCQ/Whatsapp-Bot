import { createClient } from "https://esm.sh/@supabase/supabase-js@2.53.0";

const backendUrl = (Deno.env.get("DASHBOARD_BACKEND_URL") ?? "").replace(/\/$/, "");
const dashboardKey = Deno.env.get("DASHBOARD_API_KEY") ?? "";
const allowedOrigin = (Deno.env.get("DASHBOARD_FRONTEND_ORIGIN") ?? "").replace(/\/$/, "");
const allowedRoutes = new Set([
  "generate-si-changes",
  "format-and-save-si",
  "si-history",
  "upload-catalog",
]);

function corsHeaders(origin: string | null): HeadersInit {
  const headers: Record<string, string> = {
    "Access-Control-Allow-Headers": "authorization, content-type, x-client-info, apikey",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Vary": "Origin",
  };
  if (origin && origin.replace(/\/$/, "") === allowedOrigin) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  return headers;
}

function jsonError(status: number, detail: string, cors: HeadersInit): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

Deno.serve(async (request) => {
  const origin = request.headers.get("origin");
  const cors = corsHeaders(origin);
  if (request.method === "OPTIONS") {
    return new Response(null, { status: origin && origin.replace(/\/$/, "") === allowedOrigin ? 204 : 403, headers: cors });
  }
  if (!backendUrl || !dashboardKey || !allowedOrigin) {
    return jsonError(500, "El proxy administrativo no está configurado", cors);
  }

  const authorization = request.headers.get("authorization") ?? "";
  if (!authorization.startsWith("Bearer ")) {
    return jsonError(401, "Sesión requerida", cors);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY")!;
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const userClient = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authorization } },
    auth: { persistSession: false },
  });
  const { data: userData, error: userError } = await userClient.auth.getUser();
  if (userError || !userData.user) {
    return jsonError(401, "Sesión inválida", cors);
  }

  const adminClient = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false } });
  const { data: admin, error: adminError } = await adminClient
    .from("dashboard_admins")
    .select("user_id")
    .eq("user_id", userData.user.id)
    .maybeSingle();
  if (adminError || !admin) {
    return jsonError(403, "Usuario no autorizado para administrar el dashboard", cors);
  }

  const incomingUrl = new URL(request.url);
  const marker = "/dashboard-api/";
  const markerIndex = incomingUrl.pathname.indexOf(marker);
  const route = markerIndex >= 0 ? incomingUrl.pathname.slice(markerIndex + marker.length).replace(/^\/+|\/+$/g, "") : "";
  if (!allowedRoutes.has(route)) {
    return jsonError(404, "Ruta administrativa desconocida", cors);
  }

  const target = `${backendUrl}/api/${route}${incomingUrl.search}`;
  const headers = new Headers();
  headers.set("X-Dashboard-API-Key", dashboardKey);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
    });
    const responseHeaders = new Headers(cors);
    responseHeaders.set("Content-Type", upstream.headers.get("content-type") ?? "application/json");
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return jsonError(502, "No fue posible comunicarse con el backend administrativo", cors);
  }
});
