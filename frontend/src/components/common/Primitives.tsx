import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function Panel({ children, className, title, subtitle, actions }: {
  children: ReactNode; className?: string; title?: string;
  subtitle?: string; actions?: ReactNode;
}) {
  return (
    <section className={cn("panel", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-medium text-bright">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, unit, hint, tone = "default" }: {
  label: string; value: ReactNode; unit?: string; hint?: string;
  tone?: "default" | "good" | "warn" | "muted";
}) {
  const toneClass = {
    default: "text-bright", good: "text-ok", warn: "text-warn", muted: "text-muted",
  }[tone];
  return (
    <div className="min-w-0">
      <div className="label-mono">{label}</div>
      <div className={cn("mt-1 font-mono text-lg tabular-nums", toneClass)}>
        {value}
        {unit && <span className="ml-1 text-xs text-muted">{unit}</span>}
      </div>
      {hint && <div className="mt-1 text-xs leading-snug text-muted">{hint}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral", className }: {
  children: ReactNode; tone?: "neutral" | "signal" | "warn" | "danger" | "ok";
  className?: string;
}) {
  const tones = {
    neutral: "border-line-bright bg-raised text-dim",
    signal: "border-signal/40 bg-signal/10 text-signal",
    warn: "border-warn/40 bg-warn/10 text-warn",
    danger: "border-danger/40 bg-danger/10 text-danger",
    ok: "border-ok/40 bg-ok/10 text-ok",
  };
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded border px-2 py-0.5",
                        "font-mono text-2xs uppercase tracking-wider", tones[tone], className)}>
      {children}
    </span>
  );
}

/**
 * Used wherever the backend says something is unavailable.
 * Showing the REASON is the point — an empty box teaches a judge nothing.
 */
export function Unavailable({ title, reason, hint }: {
  title: string; reason?: string | null; hint?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-line-bright bg-abyss/60 p-5">
      <div className="flex items-start gap-3">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="1.8" className="mt-0.5 shrink-0 text-muted">
          <circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.01" strokeLinecap="round" />
        </svg>
        <div className="min-w-0">
          <div className="text-sm font-medium text-dim">{title}</div>
          {reason && <p className="mt-1.5 text-xs leading-relaxed text-muted">{reason}</p>}
          {hint && <div className="mt-2 text-xs text-muted">{hint}</div>}
        </div>
      </div>
    </div>
  );
}

export function ErrorBox({ code, message, details, onRetry }: {
  code?: string; message: string; details?: Record<string, any>; onRetry?: () => void;
}) {
  return (
    <div className="rounded-lg border border-danger/35 bg-danger/[0.07] p-4">
      <div className="flex items-start gap-3">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             strokeWidth="1.8" className="mt-0.5 shrink-0 text-danger">
          <path d="M12 9v4M12 17h.01M10.3 3.9 2.4 17.5A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"
                strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <div className="min-w-0 flex-1">
          <p className="text-sm text-bright">{message}</p>
          {code && <div className="mt-1.5 font-mono text-2xs text-muted">{code}</div>}
          {details && Object.keys(details).length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer font-mono text-2xs text-muted hover:text-dim">
                details
              </summary>
              <pre className="mt-2 overflow-x-auto rounded bg-void/70 p-2 font-mono text-2xs text-dim">
                {JSON.stringify(details, null, 2)}
              </pre>
            </details>
          )}
          {onRetry && (
            <button onClick={onRetry} className="btn-ghost mt-3 !px-3 !py-1.5 text-xs">
              Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-raised", className)} />;
}

/**
 * U0 of the uncertainty ladder (master plan §7H): the disclaimer that ships before
 * any uncertainty maths exists. Non-dismissible on results pages by design.
 */
export function ProvenanceNotice({ text, compact = false }: {
  text: string; compact?: boolean;
}) {
  return (
    <div className={cn("flex items-start gap-2.5 rounded-md border border-warn/25",
                       "bg-warn/[0.06] text-warn", compact ? "px-3 py-2" : "px-4 py-3")}>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.8" className="mt-px shrink-0">
        <circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.01" strokeLinecap="round" />
      </svg>
      <p className={cn("leading-relaxed", compact ? "text-2xs" : "text-xs")}>{text}</p>
    </div>
  );
}
