import { createFileRoute } from "@tanstack/react-router";
import { handleCrmPilot } from "@/lib/crm-pilot.server";

const proxy = ({ request }: { request: Request }) => handleCrmPilot(request, process.env);
export const Route = createFileRoute("/api/public/crm-pilot/$")({
  server: { handlers: { GET: proxy, POST: proxy } },
});
