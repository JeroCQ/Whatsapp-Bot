import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  BUCKET_LABELS,
  COMMAND_LABELS,
  canCompose,
  commandUnsettled,
  type CrmBucket,
  type CrmCommandResult,
  type CrmControl,
  type CrmConversation,
  type CrmDetail,
  type CrmMessage,
} from "@/lib/crm-pilot-contract";

const BASE = "/api/public/crm-pilot/";
interface Availability {
  enabled: boolean;
  business_id: string | null;
}
interface Session {
  profile: string;
  business_id: string;
}
interface Page {
  items: CrmConversation[];
  next_cursor: string | null;
}
class PilotError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
async function request<T>(
  path: string,
  credential = "",
  body?: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(BASE + path, {
    method: body === undefined ? "GET" : "POST",
    cache: "no-store",
    mode: "same-origin",
    headers: {
      ...(credential ? { Authorization: "Bearer " + credential } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    ...(signal ? { signal } : {}),
  });
  const data: unknown = await response.json();
  if (!response.ok) {
    const detail =
      data && typeof data === "object" ? (data as Record<string, unknown>)["detail"] : null;
    throw new PilotError(
      typeof detail === "string"
        ? detail
        : `No se pudo completar la consulta (${response.status}).`,
      response.status,
    );
  }
  return data as T;
}
function message(error: unknown): string {
  return error instanceof Error ? error.message : "No se pudo contactar el CRM.";
}
function casePath(phone: string): string {
  return "conversations/" + encodeURIComponent(phone);
}
function mergeMessages(previous: CrmMessage[], incoming: CrmMessage[]): CrmMessage[] {
  const byId = new Map(previous.map((item) => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id);
}
function waiting(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}
const INPUT = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

/** Only rendered when the server explicitly enables the isolated pilot. */
export function CrmPilotLink() {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    let active = true;
    void request<Availability>("availability")
      .then((data) => {
        if (active) setAvailable(data.enabled);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  return available ? (
    <div className="border-b border-border bg-panel px-4 py-2 text-sm">
      <a className="font-medium text-brand underline" href="/chats?crm_pilot=1">
        Abrir piloto de atención humana
      </a>
    </div>
  ) : null;
}

/** Personal pilot credential remains in this component's memory, never localStorage. */
export function CrmPilotPane() {
  const [availability, setAvailability] = useState<Availability | null>(null);
  const [credentialInput, setCredentialInput] = useState("");
  const [credential, setCredential] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [bucket, setBucket] = useState<CrmBucket>("awaiting_human");
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [conversations, setConversations] = useState<CrmConversation[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [moreLoaded, setMoreLoaded] = useState(false);
  const [inboxBusy, setInboxBusy] = useState(false);
  const [inboxTick, setInboxTick] = useState(0);
  const [selectedPhone, setSelectedPhone] = useState<string | null>(null);
  const [detail, setDetail] = useState<CrmDetail | null>(null);
  const [messages, setMessages] = useState<CrmMessage[]>([]);
  const [olderCursor, setOlderCursor] = useState<string | null>(null);
  const [control, setControl] = useState<CrmControl | null>(null);
  const [detailTick, setDetailTick] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [commands, setCommands] = useState<Record<string, CrmCommandResult>>({});
  const [posting, setPosting] = useState(false);
  const postingRef = useRef(false);
  const pagingRef = useRef(false);
  const polls = useRef<Record<string, number>>({});
  const originalCommands = useRef<
    Record<string, { phone: string; operation: string; body: Record<string, unknown> }>
  >({});
  const historyCursorInitialized = useRef(false);
  const selectedCommand = selectedPhone ? (commands[selectedPhone] ?? null) : null;
  const draft = selectedPhone ? (drafts[selectedPhone] ?? "") : "";

  useEffect(() => {
    let active = true;
    void request<Availability>("availability")
      .then((data) => {
        if (active) setAvailability(data);
      })
      .catch((reason: unknown) => {
        if (active) setError(message(reason));
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(search.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    setMoreLoaded(false);
  }, [bucket, debounced, credential]);
  useEffect(() => {
    if (!credential) return;
    const controller = new AbortController();
    let active = true;
    setInboxBusy(true);
    const query = new URLSearchParams({ bucket, limit: "30" });
    if (debounced) query.set("search", debounced);
    void request<Page>("inbox?" + query, credential, undefined, controller.signal)
      .then((page) => {
        if (active) {
          setConversations(page.items);
          setNextCursor(page.next_cursor);
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(message(reason));
      })
      .finally(() => {
        if (active) setInboxBusy(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [credential, bucket, debounced, inboxTick]);
  useEffect(() => {
    if (!credential || moreLoaded) return;
    const timer = window.setInterval(() => setInboxTick((value) => value + 1), 5000);
    return () => window.clearInterval(timer);
  }, [credential, moreLoaded]);
  useEffect(() => {
    setControl(null);
    setDetail(null);
    setMessages([]);
    setOlderCursor(null);
    setError("");
    historyCursorInitialized.current = false;
  }, [selectedPhone, credential]);
  useEffect(() => {
    if (!credential || !selectedPhone) return;
    let active = true;
    // A CRM read marker is independent of the operational classification.
    void request(casePath(selectedPhone) + "/read", credential, { read: true }).catch(
      (reason: unknown) => {
        if (active) setError(message(reason));
      },
    );
    return () => {
      active = false;
    };
  }, [selectedPhone, credential]);
  useEffect(() => {
    if (!credential || !selectedPhone) return;
    const controller = new AbortController();
    let active = true;
    void request<CrmDetail>(
      casePath(selectedPhone) + "?limit=100",
      credential,
      undefined,
      controller.signal,
    )
      .then((data) => {
        if (active) {
          setDetail(data);
          setMessages((previous) => mergeMessages(previous, data.messages));
          if (!historyCursorInitialized.current) {
            setOlderCursor(data.next_cursor);
            historyCursorInitialized.current = true;
          }
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(message(reason));
      });
    // Fresh authority, never source_state from the synchronized CRM detail.
    void request<CrmControl>(
      casePath(selectedPhone) + "/control",
      credential,
      undefined,
      controller.signal,
    )
      .then((data) => {
        if (active) setControl(data);
      })
      .catch((reason: unknown) => {
        if (active) {
          setControl(null);
          setError(message(reason));
        }
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [selectedPhone, credential, detailTick]);
  useEffect(() => {
    if (!credential || !selectedPhone) return;
    const timer = window.setInterval(() => setDetailTick((value) => value + 1), 3000);
    return () => window.clearInterval(timer);
  }, [credential, selectedPhone]);
  useEffect(() => {
    if (!credential || !selectedPhone || !selectedCommand) return;
    const current = selectedCommand;
    if (!["pending", "unknown", "accepted", "delivered"].includes(current.status)) return;
    let active = true;
    const timer = window.setInterval(() => {
      const count = polls.current[current.command_id] ?? 0;
      if (count >= 40) {
        window.clearInterval(timer);
        return;
      }
      polls.current[current.command_id] = count + 1;
      void request<CrmCommandResult>(
        casePath(selectedPhone) + "/commands/" + encodeURIComponent(current.command_id),
        credential,
      )
        .then((result) => {
          if (active) setCommands((previous) => ({ ...previous, [selectedPhone]: result }));
        })
        .catch((reason: unknown) => {
          if (active) setError(message(reason));
        });
    }, 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [credential, selectedPhone, selectedCommand]);

  async function login(event: FormEvent) {
    event.preventDefault();
    if (loginBusy) return;
    setLoginBusy(true);
    setError("");
    try {
      const token = credentialInput.trim();
      const identity = await request<Session>("session", token, {});
      setSession(identity);
      setCredential(token);
      setCredentialInput("");
    } catch (reason) {
      setError(message(reason));
    } finally {
      setLoginBusy(false);
    }
  }
  function logout() {
    setCredential("");
    setSession(null);
    setCredentialInput("");
    setSelectedPhone(null);
    setConversations([]);
    setCommands({});
    setDrafts({});
    setControl(null);
    setError("");
    polls.current = {};
    originalCommands.current = {};
  }
  async function loadMore() {
    if (!nextCursor || pagingRef.current) return;
    pagingRef.current = true;
    setInboxBusy(true);
    try {
      const query = new URLSearchParams({ bucket, limit: "30", cursor: nextCursor });
      if (debounced) query.set("search", debounced);
      const page = await request<Page>("inbox?" + query, credential);
      setConversations((previous) => [
        ...new Map([...previous, ...page.items].map((item) => [item.phone_number, item])).values(),
      ]);
      setNextCursor(page.next_cursor);
      setMoreLoaded(true);
    } catch (reason) {
      setError(message(reason));
    } finally {
      pagingRef.current = false;
      setInboxBusy(false);
    }
  }
  async function loadOlder() {
    if (!selectedPhone || !olderCursor || pagingRef.current) return;
    pagingRef.current = true;
    try {
      const query = new URLSearchParams({ limit: "100", cursor: olderCursor });
      const page = await request<CrmDetail>(casePath(selectedPhone) + "?" + query, credential);
      setMessages((previous) => mergeMessages(previous, page.messages));
      setOlderCursor(page.next_cursor);
    } catch (reason) {
      setError(message(reason));
    } finally {
      pagingRef.current = false;
    }
  }
  async function execute(operation: "take" | "messages" | "close") {
    if (
      !selectedPhone ||
      !control ||
      !session ||
      postingRef.current ||
      commandUnsettled(selectedCommand)
    )
      return;
    if (
      operation === "messages" &&
      (!canCompose(control, session.profile, selectedCommand) || !draft.trim())
    )
      return;
    const phone = selectedPhone;
    const commandId = crypto.randomUUID();
    const payload: Record<string, unknown> = {
      command_id: commandId,
      expected_version: control.version,
      ...(operation === "messages"
        ? { text: draft.trim() }
        : {
            reason:
              operation === "take"
                ? "Atención desde piloto CRM"
                : "Caso cerrado por agente del piloto",
          }),
    };
    // Retain the exact immutable request for diagnosis; ambiguous outcomes only use GET.
    originalCommands.current[commandId] = { phone, operation, body: Object.freeze({ ...payload }) };
    postingRef.current = true;
    setPosting(true);
    setError("");
    setCommands((previous) => ({
      ...previous,
      [phone]: { command_id: commandId, status: "pending", control: null },
    }));
    try {
      const result = await request<CrmCommandResult>(
        casePath(phone) + "/" + operation,
        credential,
        payload,
      );
      setCommands((previous) => ({ ...previous, [phone]: result }));
      if (operation === "messages" && ["accepted", "delivered", "read"].includes(result.status)) {
        setDrafts((previous) => ({ ...previous, [phone]: "" }));
      }
    } catch (reason) {
      const knownRejection =
        reason instanceof PilotError && [400, 401, 403, 404, 409, 422].includes(reason.status);
      setCommands((previous) => ({
        ...previous,
        [phone]: {
          command_id: commandId,
          status: knownRejection ? "rejected" : "unknown",
          control: null,
        },
      }));
      setError(message(reason));
    } finally {
      // One POST only. Fresh /control is mandatory before another human action.
      setControl(null);
      setDetailTick((value) => value + 1);
      setInboxTick((value) => value + 1);
      postingRef.current = false;
      setPosting(false);
    }
  }
  async function checkCommand() {
    if (!selectedPhone || !selectedCommand) return;
    try {
      const result = await request<CrmCommandResult>(
        casePath(selectedPhone) + "/commands/" + encodeURIComponent(selectedCommand.command_id),
        credential,
      );
      setCommands((previous) => ({ ...previous, [selectedPhone]: result }));
      setDetailTick((value) => value + 1);
    } catch (reason) {
      setError(message(reason));
    }
  }
  const mine =
    !!control &&
    !!session &&
    control.phase === "human" &&
    control.is_paused &&
    control.confirmed &&
    control.owner === session.profile;
  const compose = !!session && canCompose(control, session.profile, selectedCommand) && !posting;
  return (
    <div className="flex min-h-[100dvh] flex-col bg-panel-muted text-foreground">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-background px-4 py-3">
        <div>
          <h1 className="text-lg font-semibold text-brand">Conversaciones · Piloto CRM</h1>
          <p className="text-xs text-muted-foreground">
            {session
              ? `${session.business_id} · ${session.profile}`
              : "Acceso personal al piloto de atención humana"}
          </p>
        </div>
        <div className="flex gap-3 text-sm">
          <a className="underline" href="/chats">
            Volver al panel
          </a>
          {session && (
            <button className="underline" onClick={logout}>
              Cerrar sesión de piloto
            </button>
          )}
        </div>
      </header>
      {error && (
        <div
          role="alert"
          className="m-3 rounded border border-event-warn-border bg-event-warn p-3 text-sm"
        >
          {error}
        </div>
      )}
      {!availability ? (
        <p className="p-6 text-sm">Comprobando disponibilidad del piloto…</p>
      ) : !availability.enabled ? (
        <p className="p-6 text-sm">El piloto CRM está deshabilitado en este despliegue.</p>
      ) : !session ? (
        <form
          onSubmit={(event) => void login(event)}
          className="mx-auto mt-12 w-full max-w-md space-y-4 rounded-lg border border-border bg-panel p-6"
        >
          <h2 className="font-semibold">Entrar como agente de pruebas</h2>
          <p className="text-sm text-muted-foreground">
            Usa tu credencial personal del piloto. Las contraseñas compartidas por marca no
            identifican a un agente.
          </p>
          <label className="block text-sm" htmlFor="crm-pilot-credential">
            Credencial personal
          </label>
          <input
            id="crm-pilot-credential"
            type="password"
            value={credentialInput}
            onChange={(event) => setCredentialInput(event.target.value)}
            autoComplete="off"
            className={INPUT}
            required
          />
          <Button type="submit" disabled={loginBusy || !credentialInput.trim()}>
            {loginBusy ? "Verificando…" : "Entrar al piloto"}
          </Button>
        </form>
      ) : (
        <div className="grid flex-1 md:grid-cols-[340px_minmax(0,1fr)]">
          <aside className="border-r border-border bg-panel">
            <div className="space-y-3 border-b border-border p-4">
              <label className="block text-xs font-semibold" htmlFor="crm-bucket">
                Bandeja
              </label>
              <select
                id="crm-bucket"
                value={bucket}
                onChange={(event) => setBucket(event.target.value as CrmBucket)}
                className={INPUT}
              >
                {Object.entries(BUCKET_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Buscar nombre, teléfono o contenido"
                aria-label="Buscar conversaciones"
                className={INPUT}
              />
              <Button
                variant="outline"
                size="sm"
                disabled={inboxBusy}
                onClick={() => {
                  setMoreLoaded(false);
                  setInboxTick((value) => value + 1);
                }}
              >
                Actualizar
              </Button>
              <p className="text-xs text-muted-foreground">
                {moreLoaded
                  ? "Más páginas cargadas. Usa Actualizar para volver al inicio."
                  : "Se actualiza cada 5 segundos."}
              </p>
            </div>
            <ul className="max-h-[65vh] overflow-auto md:max-h-[80vh]">
              {conversations.map((item) => (
                <li key={item.phone_number}>
                  <button
                    onClick={() => setSelectedPhone(item.phone_number)}
                    className={`w-full border-b border-border p-4 text-left hover:bg-panel-muted ${item.phone_number === selectedPhone ? "bg-panel-muted" : ""}`}
                  >
                    <span className="block font-semibold">
                      {item.name || item.phone_number}
                      {!item.read && (
                        <span className="ml-2 text-brand" aria-label="No leído">
                          ●
                        </span>
                      )}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {item.phone_number} · {BUCKET_LABELS[item.bucket]}
                    </span>
                    {item.bucket === "awaiting_human" && (
                      <span className="mt-1 block text-xs">
                        Espera {waiting(item.waiting_seconds)} · {item.unanswered_customer_count}{" "}
                        sin responder · prioridad {item.priority}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
            {!conversations.length && (
              <p className="p-4 text-sm text-muted-foreground">
                {inboxBusy ? "Cargando…" : "No hay conversaciones en esta bandeja."}
              </p>
            )}
            {nextCursor && (
              <Button
                variant="outline"
                className="m-3"
                disabled={inboxBusy}
                onClick={() => void loadMore()}
              >
                Cargar más conversaciones
              </Button>
            )}
          </aside>
          {!selectedPhone ? (
            <main className="grid min-h-80 place-items-center p-6 text-sm text-muted-foreground">
              Selecciona una conversación.
            </main>
          ) : (
            <main className="flex min-w-0 flex-col bg-chat-canvas">
              <div className="space-y-3 border-b border-border bg-panel p-4">
                <h2 className="font-semibold">{detail?.name || selectedPhone}</h2>
                <p className="text-xs text-muted-foreground">
                  {selectedPhone} · {detail ? BUCKET_LABELS[detail.bucket] : "Consultando CRM…"}
                </p>
                <p className="text-sm">
                  {!control
                    ? "Consultando control del bot…"
                    : control.phase === "pausing"
                      ? "Confirmando pausa: falta conocer el resultado de un envío automático en curso."
                      : control.phase === "human"
                        ? `Control humano: ${control.owner ?? "sin responsable"}`
                        : control.phase === "waiting"
                          ? "Handoff recibido. Esperando agente."
                          : "La IA tiene el control."}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={
                      !control ||
                      posting ||
                      commandUnsettled(selectedCommand) ||
                      !["ai", "waiting"].includes(control.phase)
                    }
                    onClick={() => void execute("take")}
                  >
                    Tomar control
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!mine || posting || commandUnsettled(selectedCommand)}
                    onClick={() => void execute("close")}
                  >
                    Cerrar y devolver a IA
                  </Button>
                </div>
                {!!detail?.data_quality_warnings.length && (
                  <p className="text-xs text-muted-foreground">
                    Advertencias de datos: {detail.data_quality_warnings.join(", ")}
                  </p>
                )}
                {selectedCommand && (
                  <div aria-live="polite" className="rounded border border-border p-2 text-xs">
                    <p className="font-semibold">{COMMAND_LABELS[selectedCommand.status]}</p>
                    <p className="break-all">Comando: {selectedCommand.command_id}</p>
                    {selectedCommand.status === "accepted" && (
                      <p>WhatsApp aceptó el envío; todavía no confirma entrega.</p>
                    )}
                    {commandUnsettled(selectedCommand) && (
                      <p>No vuelvas a enviar. Se consulta el mismo comando sin repetir la orden.</p>
                    )}
                    <button className="mt-1 underline" onClick={() => void checkCommand()}>
                      Consultar resultado
                    </button>
                  </div>
                )}
              </div>
              <section
                aria-label="Historial de mensajes"
                className="max-h-[55vh] flex-1 space-y-3 overflow-auto p-4"
              >
                {olderCursor && (
                  <Button size="sm" variant="outline" onClick={() => void loadOlder()}>
                    Cargar mensajes anteriores
                  </Button>
                )}
                {messages.map((item) => (
                  <article
                    key={item.id}
                    className={`max-w-[90%] rounded-lg border border-border p-3 text-sm ${item.role === "user" ? "bg-panel" : "ml-auto bg-secondary"}`}
                  >
                    <div className="mb-1 text-xs font-semibold text-muted-foreground">
                      {item.role === "user"
                        ? "Cliente"
                        : item.role === "asesor"
                          ? "Asesor"
                          : item.role === "model"
                            ? "IA"
                            : "Sistema"}{" "}
                      · {new Date(item.created_at).toLocaleString("es-CO")}
                    </div>
                    <p className="whitespace-pre-wrap break-words">{item.content}</p>
                    {typeof item.metadata["delivery_status"] === "string" && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {COMMAND_LABELS[
                          item.metadata["delivery_status"] as keyof typeof COMMAND_LABELS
                        ] ?? item.metadata["delivery_status"]}
                      </p>
                    )}
                  </article>
                ))}
              </section>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void execute("messages");
                }}
                className="space-y-2 border-t border-border bg-panel p-4"
              >
                <label htmlFor="crm-reply" className="block text-sm font-semibold">
                  Respuesta humana
                </label>
                <Textarea
                  id="crm-reply"
                  value={draft}
                  maxLength={4096}
                  disabled={!compose}
                  onChange={(event) =>
                    setDrafts((previous) => ({ ...previous, [selectedPhone]: event.target.value }))
                  }
                  placeholder="Toma el control y espera la pausa confirmada para responder."
                />
                <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                  <span>{draft.length}/4096 · Solo texto dentro de la ventana de WhatsApp.</span>
                  <Button type="submit" disabled={!compose || !draft.trim()}>
                    Enviar respuesta
                  </Button>
                </div>
                {mine && !control?.can_send && (
                  <p className="text-xs">
                    El bot no habilita el envío: revisa la ventana de WhatsApp o un intento todavía
                    pendiente.
                  </p>
                )}
              </form>
            </main>
          )}
        </div>
      )}
    </div>
  );
}
