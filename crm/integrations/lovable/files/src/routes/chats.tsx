import { createFileRoute, useRouterState } from "@tanstack/react-router";
import { CrmPilotLink, CrmPilotPane } from "@/components/CrmPilotPane";
import { queryOptions, useQuery, useQueryClient, useSuspenseQuery } from "@tanstack/react-query";
import { Component, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate } from "@tanstack/react-router";
import { AppNav } from "@/components/AppNav";
import { useClient } from "@/context/ClientContext";
import { chatBotsForProfile, type ChatBotId } from "@/lib/access-profiles";

const BOT_LABELS: Record<ChatBotId, string> = { tanaka: "Tanaka", memos: "Memo's", velvet: "Velvet" };

import comprobante from "@/assets/comprobante-pago.jpg";
import {
  buildConversations,
  contactNames,
  formatPhone,
  type Conversation,
  type LogMessage,
  type Role,
} from "@/data/messages";
import { fetchConversation, fetchMessageLogs, searchMessageLogs } from "@/lib/logs.functions";
import { useIsMobile } from "@/hooks/use-mobile";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { logQueryKeys, type Business } from "@/lib/logs-business";

const logsQueryOptions = (business: Business, password: string) => queryOptions({
  queryKey: logQueryKeys.recent(business),
  queryFn: () => fetchMessageLogs({ data: { business, password } }),
});

export const Route = createFileRoute("/chats")({
  ssr: false,
  head: () => ({
    meta: [
      { title: "Chats del bot de WhatsApp — Panel de bots" },
      {
        name: "description",
        content:
          "Visualiza las conversaciones del bot de WhatsApp por número, con nombre, hora exacta y eventos del sistema.",
      },
      { property: "og:title", content: "Chats del bot de WhatsApp — Panel de bots" },
      {
        property: "og:description",
        content: "Revisa los logs del bot: número, nombre, timestamps y handoffs a asesor.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ChatsPage,
});

function ChatsPage() {
  const search = useRouterState({ select: (state) => state.location.searchStr });
  return new URLSearchParams(search).get("crm_pilot") === "1" ? <CrmPilotPane /> : <LegacyChatsPage />;
}

function LegacyChatsPage() {
  const { ready, profile, password } = useClient();
  if (!ready) return null;
  if (!profile || !password) return <Navigate to="/" />;
  const allowed = chatBotsForProfile(profile);
  return (
    <div className="flex h-[100dvh] flex-col">
      <AppNav />
      <CrmPilotLink />
      {allowed.length === 0 ? (
        <div className="grid flex-1 place-items-center p-8 text-center text-sm text-muted-foreground">
          Este perfil todavía no tiene visor de chats.
        </div>
      ) : (
        <ErrorBoundaryLite>
          <Suspense
            fallback={
              <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
                Cargando conversaciones…
              </div>
            }
          >
            <Index key={profile} allowed={allowed} password={password} />
          </Suspense>
        </ErrorBoundaryLite>
      )}
    </div>
  );
}

class ErrorBoundaryLite extends Component<{ children: ReactNode }, { error: Error | null }> {
  override state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  override render() {
    if (this.state.error)
      return (
        <div className="grid flex-1 place-items-center p-8 text-center text-sm text-muted-foreground">
          <div>
            <p>No se pudieron cargar los logs.</p>
            <p className="mt-1 text-xs">{this.state.error.message}</p>
            <Button variant="outline" size="sm" className="mt-3" onClick={() => this.setState({ error: null })}>
              Reintentar
            </Button>
          </div>
        </div>
      );
    return this.props.children;
  }
}

const DAY_FMT = new Intl.DateTimeFormat("es-CO", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "America/Bogota",
});
const SHORT_DAY_FMT = new Intl.DateTimeFormat("es-CO", {
  day: "2-digit",
  month: "short",
  timeZone: "America/Bogota",
});

function time(iso: string) {
  return new Date(iso).toLocaleTimeString("es-CO", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "America/Bogota",
  });
}
function hhmm(iso: string) {
  return new Date(iso).toLocaleTimeString("es-CO", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "America/Bogota",
  });
}
function dayKey(iso: string) {
  return new Date(iso).toLocaleDateString("en-CA", { timeZone: "America/Bogota" });
}
function isToday(iso: string, all: LogMessage[]) {
  if (all.length === 0) return false;
  const newest = all.reduce((max, m) => (m.createdAt > max ? m.createdAt : max), all[0]!.createdAt);
  return dayKey(iso) === dayKey(newest);
}


const ROLE_LABEL: Record<Role, string> = {
  user: "Cliente",
  model: "Bot (IA)",
  asesor: "Asesor (humano)",
  system: "Sistema",
};

function initials(convo: Conversation) {
  if (convo.name) {
    return convo.name
      .split(" ")
      .slice(0, 2)
      .map((p) => p[0])
      .join("");
  }
  return convo.phone.slice(-2);
}

function eventKind(content: string) {
  if (content.startsWith("RESOLVED:")) return "resolved" as const;
  if (content.startsWith("HANDOFF:")) return "handoff" as const;
  return "info" as const;
}

function Index({ allowed, password }: { allowed: ChatBotId[]; password: string }) {
  const [business, setBusiness] = useState<Business>(allowed[0]!);
  const queryClient = useQueryClient();
  return <BusinessInbox key={business} business={business} allowed={allowed} password={password} onBusinessChange={(next) => {
    if (next === business) return;
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: logQueryKeys.recent(next) }),
      queryClient.invalidateQueries({ queryKey: ["message-logs-search", next] }),
      queryClient.invalidateQueries({ queryKey: ["conversation", next] }),
    ]);
    setBusiness(next);
  }} />;
}

function BusinessInbox({ business, allowed, password, onBusinessChange }: { business: Business; allowed: ChatBotId[]; password: string; onBusinessChange: (business: Business) => void }) {
  const { data, refetch, isFetching } = useSuspenseQuery(logsQueryOptions(business, password));
  const allMessages = data.messages;
  const conversations = useMemo(() => buildConversations(allMessages, business === "tanaka" ? contactNames : {}), [allMessages, business]);
  const [selectedPhone, setSelectedPhone] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState(false);

  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 350);
    return () => clearTimeout(t);
  }, [query]);

  const remote = useQuery({
    queryKey: logQueryKeys.search(business, debounced),
    queryFn: () => searchMessageLogs({ data: { business, password, q: debounced } }),
    enabled: debounced.length >= 2,
    staleTime: 60_000,
  });

  // Conversations from the loaded page + anything the server search found
  const allConversations = useMemo(() => {
    const extra = remote.data?.messages ?? [];
    if (extra.length === 0) return conversations;
    const byId = new Map<number, LogMessage>();
    for (const m of allMessages) byId.set(m.id, m);
    for (const m of extra) byId.set(m.id, m);
    return buildConversations([...byId.values()], business === "tanaka" ? contactNames : {});
  }, [conversations, allMessages, remote.data, business]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return conversations;
    const digits = q.replace(/[^0-9]/g, "");
    return allConversations.filter(
      (c) =>
        (digits.length > 0 && c.phone.includes(digits)) ||
        (c.name ?? "").toLowerCase().includes(q) ||
        c.messages.some((m) => m.content.toLowerCase().includes(q)),
    );
  }, [conversations, allConversations, query]);

  const isMobile = useIsMobile();
  const selected = selectedPhone ?? "";
  const setSelected = setSelectedPhone;
  const listConvo = allConversations.find((c) => c.phone === selected);

  // Full thread for the selected number (the initial page may only contain its latest events)
  const thread = useQuery({
    queryKey: logQueryKeys.conversation(business, selected),
    queryFn: () => fetchConversation({ data: { business, password, phone: selected } }),
    enabled: selected.length > 0,
    staleTime: 60_000,
  });

  const convo = useMemo(() => {
    const full = thread.data?.messages ?? [];
    if (!listConvo && full.length === 0) return undefined;
    const byId = new Map<number, LogMessage>();
    for (const m of listConvo?.messages ?? []) byId.set(m.id, m);
    for (const m of full) byId.set(m.id, m);
    const merged = buildConversations([...byId.values()], business === "tanaka" ? contactNames : {}).find((c) => c.phone === selected);
    return merged ?? listConvo;
  }, [listConvo, thread.data, selected, business]);

  const grouped = useMemo(() => {
    const groups: { day: string; items: LogMessage[] }[] = [];
    for (const m of convo?.messages ?? []) {
      const key = dayKey(m.createdAt);
      const last = groups[groups.length - 1];
      if (last && last.day === key) last.items.push(m);
      else groups.push({ day: key, items: [m] });
    }
    return groups;
  }, [convo]);

  const copyNumber = () => {
    if (!convo) return;
    void navigator.clipboard?.writeText(convo.phone);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const logError = data.error ?? remote.data?.error ?? thread.data?.error;

  return (
    <div className="flex min-h-0 flex-1 bg-panel-muted text-foreground font-sans overflow-hidden">
      {/* Sidebar: Conversation List */}
      <aside
        className={`w-full md:w-80 md:flex-shrink-0 border-r border-border bg-panel-muted flex-col ${
          isMobile && selectedPhone ? "hidden" : "flex"
        }`}
      >
        <div className="p-4 border-b border-border bg-panel">
          <h1 className="text-lg font-semibold text-brand flex items-center gap-2">
            Conversaciones
            <span className="text-muted-foreground font-normal text-sm">WhatsApp Bot</span>
          </h1>
          {allowed.length > 1 && <Tabs value={business} onValueChange={(value) => {
            if ((allowed as string[]).includes(value)) onBusinessChange(value as Business);
          }} className="mt-3">
            <TabsList aria-label="Negocio" className="grid w-full" style={{ gridTemplateColumns: `repeat(${allowed.length}, minmax(0, 1fr))` }}>
              {allowed.map((b) => (
                <TabsTrigger key={b} value={b}>{BOT_LABELS[b]}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>}
          {logError && (
            <div role="alert" className="mt-3 rounded border border-event-warn-border bg-event-warn p-3 text-xs text-event-warn-foreground break-words">
              <p>{logError}</p>
              <Button variant="outline" size="sm" className="mt-2" onClick={() => void refetch()} disabled={isFetching}>
                {isFetching ? "Actualizando…" : "Reintentar"}
              </Button>
            </div>
          )}
          <div className="mt-3 flex items-center gap-2 rounded border border-border bg-panel-muted px-2.5 py-1.5">
            <span className="font-mono text-xs text-muted-foreground">⌕</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Buscar número, nombre o mensaje…"
              aria-label="Buscar conversaciones"
              className="w-full bg-transparent text-xs outline-none placeholder:text-muted-foreground"
            />
          </div>
          <p className="mt-2 text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
            {filtered.length} conversaciones · {allMessages.length} mensajes · ordenadas por última actividad
          </p>
        </div>

        <div className="flex-1 overflow-y-auto">
          {filtered.map((c) => {
            const active = c.phone === selected;
            return (
              <button
                key={c.phone}
                onClick={() => setSelected(c.phone)}
                className={`w-full text-left p-4 border-b border-border cursor-pointer transition-colors ${
                  active
                    ? "bg-panel border-l-4 border-l-brand-accent"
                    : "hover:bg-secondary border-l-4 border-l-transparent"
                }`}
              >
                <div className="flex justify-between items-start mb-1 gap-2">
                  <span
                    className={`font-mono text-xs tabular-nums ${active ? "font-semibold text-foreground" : "text-muted-foreground"}`}
                  >
                    {formatPhone(c.phone)}
                  </span>
                  <span className="text-[10px] font-mono text-muted-foreground shrink-0 tabular-nums">
                    {isToday(c.last.createdAt, allMessages)
                      ? hhmm(c.last.createdAt)
                      : SHORT_DAY_FMT.format(new Date(c.last.createdAt))}
                  </span>
                </div>
                <p className="font-semibold text-sm truncate text-foreground">
                  {c.name ?? `Contacto ${c.phone.slice(-4)}`}
                </p>
                <p
                  className={`text-xs text-muted-foreground truncate mt-1 ${c.last.role === "system" ? "italic" : ""}`}
                >
                  <span className="font-mono">{c.last.role}:</span> {c.last.content}
                </p>
                <div className="mt-2 flex gap-1">
                  {c.status === "resolved" && (
                    <span className="px-1.5 py-0.5 bg-event-closed text-event-closed-foreground text-[9px] font-bold rounded uppercase tracking-wider">
                      Resuelta
                    </span>
                  )}
                  {c.status === "handoff" && (
                    <span className="px-1.5 py-0.5 bg-event-warn text-event-warn-foreground text-[9px] font-bold rounded uppercase tracking-wider">
                      Handoff
                    </span>
                  )}
                  {c.status === "active" && (
                    <span className="px-1.5 py-0.5 bg-bubble-user text-bubble-user-foreground text-[9px] font-bold rounded uppercase tracking-wider">
                      Activa
                    </span>
                  )}
                  {c.hasAsesor && (
                    <span className="px-1.5 py-0.5 bg-event-info text-event-info-foreground text-[9px] font-bold rounded uppercase tracking-wider">
                      Asesor
                    </span>
                  )}
                </div>
              </button>
            );
          })}
          {filtered.length === 0 && (
            <p className="p-4 text-xs text-muted-foreground">
              {remote.isFetching ? "Buscando en todo el historial…" : query ? `Sin resultados para “${query}”.` : "Todavía no hay mensajes en los logs."}
            </p>
          )}
        </div>
      </aside>

      {/* Main Chat Area */}
      <main
        className={`flex-1 flex-col bg-chat-canvas relative min-w-0 ${
          isMobile && !selectedPhone ? "hidden" : "flex"
        }`}
      >
        {!convo ? (
          <div className="grid flex-1 place-items-center p-8 text-center text-sm text-muted-foreground">
            {logError ? "No se pudieron cargar las conversaciones." : "Selecciona una conversación."}
          </div>
        ) : <>
        <header className="bg-panel border-b border-border px-3 py-2 md:h-16 md:px-6 md:py-0 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 z-10 shrink-0">
          <div className="flex items-center gap-2 md:gap-4 min-w-0">
            <button
              onClick={() => setSelected(null)}
              aria-label="Volver a conversaciones"
              className="md:hidden shrink-0 rounded px-1.5 py-1 text-lg text-muted-foreground hover:text-foreground"
            >
              ←
            </button>
            <div className="w-9 h-9 md:w-10 md:h-10 shrink-0 rounded-full bg-secondary grid place-items-center font-bold text-muted-foreground uppercase text-xs">
              {initials(convo)}
            </div>
            <div className="min-w-0">
              <h2 className="font-bold text-foreground flex items-center gap-2 min-w-0">
                <span className="truncate text-sm md:text-base">
                  {convo.name ?? `Contacto ${convo.phone.slice(-4)}`}
                </span>
                <span className="hidden sm:inline text-[10px] px-1.5 py-0.5 rounded bg-secondary text-muted-foreground border border-border uppercase tracking-wider font-mono tabular-nums shrink-0">
                  {formatPhone(convo.phone)}
                </span>
              </h2>
              <p className="text-[11px] md:text-xs text-muted-foreground font-medium truncate">
                <span className="sm:hidden font-mono tabular-nums">
                  {formatPhone(convo.phone)} ·{" "}
                </span>
                {convo.status === "resolved"
                  ? "Resuelta"
                  : convo.status === "handoff"
                    ? "Con asesor"
                    : "Activa"}{" "}
                <span className="hidden md:inline">
                  • Última actividad {DAY_FMT.format(new Date(convo.last.createdAt))} ·{" "}
                </span>
                <span className="font-mono tabular-nums">{time(convo.last.createdAt)}</span>
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 md:gap-3 shrink-0">
            <div className="hidden sm:block px-3 py-1 bg-event-info text-event-info-foreground text-xs font-semibold rounded-full border border-event-info-border">
              {convo.messages.length} mensajes
            </div>
            <div className="hidden md:block h-8 w-px bg-border" />
            <button
              onClick={() => void refetch()}
              className="rounded border border-border bg-secondary px-2.5 py-1 text-xs font-semibold text-foreground transition-colors hover:bg-panel-muted"
            >
              {isFetching ? "…" : "↻"}
              <span className="hidden md:inline"> Actualizar</span>
            </button>
            <button
              onClick={copyNumber}
              className="hidden md:inline text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {copied ? "Copiado ✓" : "Copiar número"}
            </button>
          </div>
        </header>

        {/* Message History */}
        <div className="flex-1 overflow-y-auto px-3 py-3 md:px-6 md:py-4">
          {grouped.map((group) => (
            <div key={group.day} className="space-y-2">
              <div className="flex justify-center my-6">
                <span className="px-3 py-1 bg-panel/60 backdrop-blur rounded text-[10px] font-bold text-muted-foreground uppercase tracking-widest">
                  {DAY_FMT.format(new Date(group.items[0]!.createdAt))}
                </span>
              </div>

              {group.items.map((m) => {
                if (m.role === "system") {
                  const kind = eventKind(m.content);
                  if (kind === "resolved") {
                    return (
                      <div key={m.id} className="flex justify-center">
                        <div className="bg-event-closed text-event-closed-foreground px-6 py-2 rounded-full text-center shadow-lg">
                          <p className="text-[11px] font-bold flex items-center gap-2 uppercase tracking-wider">
                            <span className="w-2 h-2 rounded-full bg-brand-accent" />
                            Conversación cerrada por asesor
                          </p>
                          <p className="text-[9px] text-event-closed-foreground/60 mt-0.5 font-mono tabular-nums">
                            ID: {m.id} • {time(m.createdAt)}
                          </p>
                        </div>
                      </div>
                    );
                  }
                  if (kind === "handoff") {
                    const [head, reason] = m.content.split("Razón:");
                    return (
                      <div key={m.id} className="flex justify-center">
                        <div className="bg-event-warn border border-event-warn-border px-4 py-2 rounded-md text-center max-w-sm shadow-sm">
                          <p className="text-[11px] text-event-warn-foreground font-semibold">
                            {head?.replace("HANDOFF:", "HANDOFF:").trim()}
                          </p>
                          {reason && (
                            <p className="text-[10px] text-event-warn-foreground/80 mt-0.5">
                              Razón: {reason.trim()}
                            </p>
                          )}
                          <p className="text-[9px] text-event-warn-foreground/60 mt-1 font-mono tabular-nums">
                            ID: {m.id} • {time(m.createdAt)}
                          </p>
                        </div>
                      </div>
                    );
                  }
                  return (
                    <div key={m.id} className="flex justify-center">
                      <div className="bg-event-info border border-event-info-border px-4 py-2 rounded-md text-center max-w-sm shadow-sm">
                        <p className="text-[11px] text-event-info-foreground font-medium">
                          📎 {m.content}
                        </p>
                        <p className="text-[9px] text-event-info-foreground/60 mt-1 font-mono uppercase tabular-nums">
                          Evento del sistema • ID: {m.id} • {time(m.createdAt)}
                        </p>
                      </div>
                    </div>
                  );
                }

                if (m.content === "[Imagen enviada sin texto]") {
                  return (
                    <div key={m.id} className="flex flex-col items-end">
                      <div className="w-64">
                        <img
                          src={comprobante}
                          alt="Comprobante de pago enviado por el cliente"
                          loading="lazy"
                          width={512}
                          height={700}
                          className="w-full rounded-lg shadow-sm mb-1 bg-panel"
                        />
                        <div className="flex justify-end gap-2">
                          <span className="text-[9px] text-muted-foreground font-mono">
                            ID: {m.id}
                          </span>
                          <span className="text-[9px] text-muted-foreground font-mono tabular-nums">
                            {time(m.createdAt)}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                }

                const isRight = m.role === "user" || m.role === "asesor";
                const bubble =
                  m.role === "user"
                    ? "bg-bubble-user text-bubble-user-foreground rounded-tr-none"
                    : m.role === "asesor"
                      ? "bg-bubble-asesor text-bubble-asesor-foreground rounded-tr-none"
                      : "bg-bubble-model text-bubble-model-foreground rounded-tl-none";

                return (
                  <div
                    key={m.id}
                    className={`flex flex-col ${isRight ? "items-end" : "items-start"}`}
                  >
                    <div
                      className={`max-w-[85%] md:max-w-md p-3 rounded-lg shadow-sm relative group ${bubble}`}
                    >
                      <span
                        className={`absolute -top-5 text-[10px] text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity font-mono ${isRight ? "right-0" : "left-0"}`}
                      >
                        ID: {m.id}
                      </span>
                      <p className="text-sm whitespace-pre-line leading-relaxed">{m.content}</p>
                      <div
                        className={`flex mt-1 items-center gap-2 ${isRight ? "justify-end" : "justify-start"}`}
                      >
                        <span
                          className={`text-[9px] font-bold uppercase tracking-tighter ${
                            m.role === "model"
                              ? "text-brand-accent"
                              : m.role === "asesor"
                                ? "text-bubble-asesor-foreground/80"
                                : "text-bubble-user-foreground/70"
                          }`}
                        >
                          {ROLE_LABEL[m.role]}
                        </span>
                        <span
                          className={`text-[9px] font-mono tabular-nums ${m.role === "asesor" ? "text-bubble-asesor-foreground/70" : "text-muted-foreground"}`}
                        >
                          {time(m.createdAt)}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>

        {/* Metadata Inspector */}
        <footer className="bg-panel border-t border-border p-2 md:p-3 flex flex-wrap items-center justify-between gap-2 text-[10px] md:text-[11px] text-muted-foreground shrink-0">
          <div className="flex gap-3 md:gap-6 flex-wrap min-w-0">
            <span>
              <strong className="text-foreground">Mensajes:</strong> {convo.messages.length}
            </span>
            <span>
              <strong className="text-foreground">Número:</strong>{" "}
              <span className="font-mono tabular-nums">{convo.phone}</span>
            </span>
            <span>
              <strong className="text-foreground">Cliente:</strong> {convo.counts.user} ·{" "}
              <strong className="text-foreground">Bot:</strong> {convo.counts.model} ·{" "}
              <strong className="text-foreground">Asesor:</strong> {convo.counts.asesor} ·{" "}
              <strong className="text-foreground">Sistema:</strong> {convo.counts.system}
            </span>
          </div>
          <div className="flex gap-2">
            <span className="px-2 py-1 bg-secondary rounded border border-border font-mono tabular-nums">
              Primer mensaje {time(convo.messages[0]!.createdAt)}
            </span>
          </div>
        </footer>
        </>}
      </main>
    </div>
  );
}
