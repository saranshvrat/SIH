import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell } from "@/components/common/AppShell";
import { Landing } from "@/features/landing/Landing";

// The landing page is what a judge sees first, so it must not wait on MapLibre
// (~800 kB) or the charting library. Everything heavy is split out behind Suspense.
const Explore = lazy(() => import("@/features/explore/Explore").then((m) => ({ default: m.Explore })));
const Process = lazy(() => import("@/features/processing/Process").then((m) => ({ default: m.Process })));
const Results = lazy(() => import("@/features/results/Results").then((m) => ({ default: m.Results })));
const SessionPage = lazy(() => import("@/features/landing/SessionPage").then((m) => ({ default: m.SessionPage })));
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (count, err: any) =>
        // 4xx answers are information, not transient failures.
        err?.status >= 400 && err?.status < 500 ? false : count < 2,
      staleTime: 5_000,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppShell>
          <Suspense fallback={<RouteLoading />}>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/explore" element={<Explore />} />
            <Route path="/process" element={<Process />} />
            <Route path="/process/:jobId" element={<Process />} />
            <Route path="/results/:jobId" element={<Results />} />
            <Route path="/session" element={<SessionPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
          </Suspense>
        </AppShell>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);

function RouteLoading() {
  return (
    <div className="grid min-h-[60vh] place-items-center">
      <div className="flex items-center gap-3 text-sm text-muted">
        <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-signal border-t-transparent" />
        loading…
      </div>
    </div>
  );
}

function NotFound() {
  return (
    <div className="grid min-h-[60vh] place-items-center px-6 text-center">
      <div>
        <div className="label-mono mb-3">404</div>
        <h1 className="text-2xl font-semibold">That page does not exist.</h1>
        <a href="/" className="btn-ghost mt-6">Back to start</a>
      </div>
    </div>
  );
}
