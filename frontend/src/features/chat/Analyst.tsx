import { useEffect, useRef, useState } from "react";
import { useChat, useChatCapabilities } from "@/api/queries/hooks";
import { Badge } from "@/components/common/Primitives";
import type { ChatResponse } from "@/types/api";

interface Msg { role: "user" | "assistant"; content: string; meta?: ChatResponse }

export function Analyst({ jobId }: { jobId: string }) {
  const { data: caps } = useChatCapabilities();
  const chat = useChat(jobId);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); },
            [messages, chat.isPending]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || chat.isPending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: q }]);
    try {
      const res = await chat.mutateAsync(q);
      setMessages((m) => [...m, { role: "assistant", content: res.answer, meta: res }]);
    } catch (e: any) {
      setMessages((m) => [...m, {
        role: "assistant",
        content: `The analyst could not answer: ${e?.message ?? "unknown error"}`,
      }]);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <h3 className="text-sm font-medium text-bright">Satellite analyst</h3>
          <p className="mt-0.5 text-2xs text-muted">Answers only from this job's data</p>
        </div>
        <Badge tone={caps?.enabled ? "signal" : "neutral"}>
          {caps?.enabled ? "live" : "template"}
        </Badge>
      </header>

      {caps?.notice && (
        <p className="border-b border-line bg-warn/[0.06] px-4 py-2.5 text-2xs leading-relaxed text-warn">
          {caps.notice}
        </p>
      )}

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div>
            <p className="mb-3 text-xs leading-relaxed text-muted">
              {caps?.guarantee ??
               "The analyst may only state numbers returned by backend tools."}
            </p>
            <div className="space-y-1.5">
              {(caps?.suggestions ?? []).map((s) => (
                <button key={s} onClick={() => send(s)}
                  className="w-full rounded-md border border-line px-3 py-2 text-left text-xs text-dim transition-colors hover:border-signal/50 hover:text-bright">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i}>
            {m.role === "user" ? (
              <div className="ml-auto max-w-[85%] rounded-lg rounded-br-sm bg-signal/15 px-3 py-2 text-sm text-bright">
                {m.content}
              </div>
            ) : (
              <div className="max-w-[92%]">
                <div className="whitespace-pre-wrap rounded-lg rounded-bl-sm border border-line bg-panel px-3 py-2 text-sm leading-relaxed text-dim">
                  {m.content}
                </div>
                {m.meta && <ToolTrace meta={m.meta} />}
              </div>
            )}
          </div>
        ))}

        {chat.isPending && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-signal border-t-transparent" />
            consulting the job data…
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); send(input); }}
            className="border-t border-line p-3">
        <div className="flex gap-2">
          <input value={input} onChange={(e) => setInput(e.target.value)}
                 placeholder="Ask about these results…"
                 className="flex-1 rounded-md border border-line bg-void px-3 py-2 text-sm text-bright placeholder:text-muted focus:border-signal/60 focus:outline-none" />
          <button type="submit" disabled={!input.trim() || chat.isPending}
                  className="btn-primary !px-3">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M5 12h14M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      </form>
    </div>
  );
}

/** The transparency panel: exactly which backend tools produced this answer. */
function ToolTrace({ meta }: { meta: ChatResponse }) {
  const [open, setOpen] = useState(false);
  if (!meta.tool_calls?.length) return null;
  return (
    <div className="mt-1.5">
      <button onClick={() => setOpen(!open)}
              className="flex items-center gap-1.5 font-mono text-2xs text-muted hover:text-dim">
        <span>{open ? "▾" : "▸"}</span>
        data used · {meta.tool_calls.length} tool{meta.tool_calls.length === 1 ? "" : "s"}
        {!meta.provenance.verified && (
          <span className="ml-1 text-warn">· unverified figures</span>
        )}
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5 rounded border border-line bg-void/60 p-2">
          {meta.tool_calls.map((t, i) => (
            <details key={i}>
              <summary className="cursor-pointer font-mono text-2xs text-signal-dim hover:text-signal">
                {t.tool}
              </summary>
              <pre className="mt-1 max-h-40 overflow-auto rounded bg-void p-2 font-mono text-2xs text-muted">
                {JSON.stringify(t.result, null, 2)}
              </pre>
            </details>
          ))}
          {!meta.provenance.verified && (
            <p className="text-2xs leading-relaxed text-warn">
              These figures could not be traced to a tool result:{" "}
              {meta.provenance.unverified_numbers.join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
