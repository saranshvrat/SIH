import { Link, NavLink, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { useHealth } from "@/api/queries/hooks";

const NAV = [
  { to: "/explore", label: "Explore" },
  { to: "/process", label: "Process" },
  { to: "/session", label: "Your data" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { data: health } = useHealth();
  const { pathname } = useLocation();
  if (pathname === "/") return <>{children}</>;

  const degraded = health && health.status !== "ok";

  return (
    <div className="flex min-h-screen flex-col bg-void">
      <header className="sticky top-0 z-40 border-b border-line bg-void/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1800px] items-center gap-6 px-4 md:px-6">
          <Link to="/" className="flex shrink-0 items-center gap-2.5">
            <svg width="22" height="22" viewBox="0 0 32 32" fill="none" aria-hidden>
              <rect x="1.5" y="1.5" width="29" height="29" rx="4" stroke="#2a3542" strokeWidth="1.5" />
              <rect x="7" y="7" width="8" height="8" fill="#1d8d96" />
              <rect x="17" y="7" width="8" height="8" fill="#38d0d8" opacity="0.55" />
              <rect x="7" y="17" width="8" height="8" fill="#38d0d8" opacity="0.8" />
              <rect x="17" y="17" width="8" height="8" fill="#7fe8ee" />
            </svg>
            <span className="text-sm font-semibold tracking-tight">PixelForge</span>
          </Link>

          <nav className="flex items-center gap-1">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => cn(
                "rounded-md px-3 py-1.5 text-sm transition-colors",
                isActive ? "bg-raised text-bright" : "text-muted hover:text-bright")}>
                {n.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-4">
            {health && (
              <div className="hidden items-center gap-4 font-mono text-2xs text-muted sm:flex">
                <span title={`${health.models_available.length} model(s) ready`}>
                  {health.models_available.length} model
                  {health.models_available.length === 1 ? "" : "s"}
                </span>
                {!health.features.llm && <span title="No LLM key configured">analyst: template</span>}
                <span className={cn("inline-flex items-center gap-1.5",
                                    degraded ? "text-warn" : "text-ok")}>
                  <span className={cn("h-1.5 w-1.5 rounded-full",
                                      degraded ? "bg-warn" : "bg-ok")} />
                  {health.status}
                </span>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="flex-1">{children}</div>

      <footer className="border-t border-line px-4 py-3 text-2xs text-muted md:px-6">
        Session-scoped and temporary · nothing is stored permanently ·{" "}
        <Link to="/session" className="underline decoration-line-bright underline-offset-2 hover:text-dim">
          review or delete your data
        </Link>
      </footer>
    </div>
  );
}
