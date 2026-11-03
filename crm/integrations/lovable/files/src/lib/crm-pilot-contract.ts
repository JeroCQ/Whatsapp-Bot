/** Operational CRM wire contract. This module contains no credentials or server configuration. */
export type CrmBucket = "awaiting_human" | "human_active" | "ai_active" | "closed" | "all";
export interface CrmConversation {
  business_id: string;
  phone_number: string;
  name: string | null;
  bucket: Exclude<CrmBucket, "all">;
  waiting_seconds: number;
  unanswered_customer_count: number;
  priority: number;
  priority_reason: string;
  read: boolean;
  data_quality_warnings: string[];
}
export interface CrmMessage {
  id: number;
  role: "user" | "model" | "asesor" | "system";
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
}
export interface CrmDetail extends CrmConversation {
  messages: CrmMessage[];
  next_cursor: string | null;
}
export interface CrmControl {
  business_id: string;
  phone_number: string;
  provider: "crm";
  phase: "ai" | "waiting" | "pausing" | "human";
  version: number;
  owner: string | null;
  is_paused: boolean;
  confirmed: boolean;
  can_send: boolean;
  window_expires_at: string | null;
}
export type CrmCommandStatus =
  | "confirmed"
  | "pending"
  | "accepted"
  | "delivered"
  | "read"
  | "failed"
  | "unknown"
  | "rejected"
  | "not_forwarded";
export interface CrmCommandResult {
  command_id: string;
  status: CrmCommandStatus;
  control: CrmControl | null;
  provider_message_id?: string | null;
  error?: string;
}
export const BUCKET_LABELS: Record<CrmBucket, string> = {
  awaiting_human: "Por responder",
  human_active: "En gestión",
  ai_active: "IA atendiendo",
  closed: "Cerradas",
  all: "Todas",
};
export const COMMAND_LABELS: Record<CrmCommandStatus, string> = {
  confirmed: "Confirmado",
  pending: "Pendiente",
  accepted: "Aceptado por WhatsApp",
  delivered: "Entregado",
  read: "Leído",
  failed: "Envío fallido",
  unknown: "Resultado incierto",
  rejected: "Rechazado",
  not_forwarded: "No enviado al bot",
};
export function commandUnsettled(result: CrmCommandResult | null): boolean {
  return result?.status === "pending" || result?.status === "unknown";
}
export function canCompose(
  control: CrmControl | null,
  actor: string,
  result: CrmCommandResult | null,
): boolean {
  return (
    !!control &&
    control.phase === "human" &&
    control.is_paused &&
    control.confirmed &&
    control.can_send &&
    control.owner === actor &&
    !commandUnsettled(result)
  );
}
