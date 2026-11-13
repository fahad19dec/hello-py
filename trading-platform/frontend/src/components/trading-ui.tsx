"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, errorMessage } from "@/lib/api";
import { stateTone } from "@/lib/trading";

type Query = Record<string, string | number | boolean | undefined | null>;

/** Load data from the API, with reload and optional polling. */
export function useApiData<T>(path: string | null, options: { pollMs?: number; query?: Query } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The query is compared by value, so callers can pass a fresh object on every render.
  const queryKey = JSON.stringify(options.query ?? {});

  const load = useCallback(async () => {
    if (!path) return;
    const query = JSON.parse(queryKey) as Query;
    const params: Record<string, string> = {};
    for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null) params[key] = String(value);
    try {
      setData(await api<T>(path, { query: params }));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [path, queryKey]);

  useEffect(() => {
    // Loading on mount is the point of this hook; the state is set after the request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    if (!options.pollMs) return;
    const timer = window.setInterval(() => void load(), options.pollMs);
    return () => window.clearInterval(timer);
  }, [load, options.pollMs]);

  return { data, error, reload: load, setData };
}

export function StateBadge({ state }: { state: string }) {
  const tone = stateTone(state);
  const cls =
    tone === "good"
      ? "border-positive/30 bg-positive/10 text-positive"
      : tone === "bad"
        ? "border-negative/30 bg-negative/10 text-negative"
        : tone === "wait"
          ? "border-warning/30 bg-warning/10 text-warning"
          : "text-muted-foreground";
  return (
    <Badge variant="outline" className={`font-mono text-[0.68rem] tracking-wide ${cls}`}>
      {state.replaceAll("_", " ")}
    </Badge>
  );
}

export function PaperBadge({ paper }: { paper: boolean }) {
  return paper ? (
    <Badge variant="outline" className="border-primary/30 text-[0.65rem] text-primary">
      PAPER
    </Badge>
  ) : (
    <Badge variant="outline" className="border-warning/40 text-[0.65rem] text-warning">
      LIVE
    </Badge>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {description ? <p className="mt-0.5 max-w-3xl text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function RefreshButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="outline" size="sm" onClick={onClick}>
      <RefreshCw aria-hidden /> Refresh
    </Button>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">{message}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">{children}</p>;
}

export const selectClass =
  "h-8 w-full rounded-lg border border-input bg-input/30 px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";
