"use client";

import { ShieldAlert } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Field, FormError } from "@/components/form-bits";
import { ErrorNote, PageHeader, RefreshButton, useApiData } from "@/components/trading-ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { fmt, type Num } from "@/lib/trading";

type Limits = {
  max_order_quantity: Num; max_order_lots: Num; max_open_positions: number | null; max_daily_loss: Num;
  max_daily_trades: number | null; max_exposure: Num; max_slippage_pct: Num; max_signal_age_seconds: number | null;
  max_consecutive_failures: number | null; min_available_margin: Num; allowed_symbols: string[] | null;
  trading_sessions: { days: number[]; start: string; end: string; timezone: string }[] | null;
};
type Scope = { scope_key: string; label: string; broker?: string; kill_switch?: boolean; circuit_open?: boolean; limits: Limits };
type RiskOverview = {
  kill_switch: { enabled: boolean; reason: string | null };
  platform_halted: boolean;
  platform_halt_reason: string | null;
  defaults: { max_signal_age_seconds: number; max_consecutive_failures: number };
  scopes: Scope[];
};

const FIELDS: { key: keyof Limits; label: string; hint: string }[] = [
  { key: "max_order_quantity", label: "Max order quantity", hint: "Shares or contracts (Zerodha, paper)" },
  { key: "max_order_lots", label: "Max lots per order", hint: "MetaTrader accounts" },
  { key: "max_open_positions", label: "Max open positions", hint: "Different instruments held at once" },
  { key: "max_daily_trades", label: "Max entries per day", hint: "Exits are never counted" },
  { key: "max_daily_loss", label: "Daily loss lock", hint: "Realised loss incl. fees; locks new entries" },
  { key: "max_exposure", label: "Max exposure", hint: "Open notional plus the new order" },
  { key: "max_slippage_pct", label: "Max slippage %", hint: "Market-order protection" },
  { key: "max_signal_age_seconds", label: "Max signal age (s)", hint: "Older alerts are refused" },
  { key: "max_consecutive_failures", label: "Stop after failures", hint: "Circuit breaker" },
  { key: "min_available_margin", label: "Min free margin", hint: "Below this, no new entries" },
];

function describe(limits: Limits): string[] {
  const parts: string[] = [];
  for (const f of FIELDS) {
    const value = limits[f.key];
    if (value !== null && value !== undefined && !Array.isArray(value)) parts.push(`${f.label}: ${fmt(value as Num)}`);
  }
  if (limits.allowed_symbols) parts.push(`Only ${limits.allowed_symbols.join(", ")}`);
  if (limits.trading_sessions) parts.push(`${limits.trading_sessions.length} trading session(s)`);
  return parts;
}

function LimitsForm({ scope, onDone }: { scope: Scope; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const limits = scope.limits;
  const session = limits.trading_sessions?.[0];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body: Record<string, unknown> = {};
    for (const f of FIELDS) {
      const value = String(form.get(f.key) ?? "").trim();
      if (value) body[f.key] = value;
    }
    const symbols = String(form.get("allowed_symbols") ?? "").split(/[\s,]+/).filter(Boolean);
    if (symbols.length) body.allowed_symbols = symbols;
    const start = String(form.get("session_start") ?? ""), end = String(form.get("session_end") ?? "");
    if (start && end) {
      body.trading_sessions = [{ days: [0, 1, 2, 3, 4], start, end, timezone: String(form.get("session_tz") || "Asia/Kolkata") }];
    }
    try {
      await api(`/risk/limits/${scope.scope_key}`, { method: "PUT", body });
      toast.success("Limits saved");
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Limits · {scope.label}</DialogTitle>
        <DialogDescription>Leave a field empty for no limit at this level. The strictest of all levels applies to each order.</DialogDescription>
      </DialogHeader>
      <FormError message={error} />
      <div className="grid grid-cols-2 gap-3">
        {FIELDS.map((f) => (
          <Field key={f.key} id={f.key} label={f.label} hint={f.hint} inputMode="decimal"
            defaultValue={limits[f.key] !== null && limits[f.key] !== undefined ? String(limits[f.key]) : ""} />
        ))}
      </div>
      <Field id="allowed_symbols" label="Allowed instruments (optional)" placeholder="RELIANCE, TCS, NIFTY1!"
        defaultValue={limits.allowed_symbols?.join(", ") ?? ""} hint="Empty means any instrument." />
      <div className="grid grid-cols-3 gap-3">
        <Field id="session_start" label="Session start" placeholder="09:20" defaultValue={session?.start ?? ""} />
        <Field id="session_end" label="Session end" placeholder="15:10" defaultValue={session?.end ?? ""} />
        <Field id="session_tz" label="Time zone" placeholder="Asia/Kolkata" defaultValue={session?.timezone ?? ""} />
      </div>
      <DialogFooter>
        <Button type="submit">Save limits</Button>
      </DialogFooter>
    </form>
  );
}

export function Risk() {
  const { data, error, reload } = useApiData<RiskOverview>("/risk");
  const [editing, setEditing] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggleKill() {
    if (!data) return;
    const enabling = !data.kill_switch.enabled;
    const reason = enabling ? window.prompt("Stop all automated trading on every account. Reason (optional):") : "";
    if (enabling && reason === null) return;
    setBusy(true);
    try {
      await api("/risk/kill-switch", { method: "POST", body: { enabled: enabling, reason: reason ?? "" } });
      toast.success(enabling ? "All automated trading stopped" : "Kill switch off");
      reload();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-5">
      <PageHeader title="Risk management" description="Every order passes these checks before it is queued, and the stop-trading checks again just before it is sent. A failed check rejects the order with the exact rule; nothing is resized for you." actions={<RefreshButton onClick={reload} />} />
      <ErrorNote message={error} />
      {data ? (
        <>
          {data.platform_halted ? (
            <p className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm text-negative">
              Trading is halted on the whole platform by an administrator: {data.platform_halt_reason ?? "no reason given"}.
            </p>
          ) : null}
          <Card className={data.kill_switch.enabled ? "ring-negative/50" : undefined}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                Emergency kill switch
                {data.kill_switch.enabled ? <Badge variant="outline" className="border-negative/40 text-negative">ON</Badge> : <Badge variant="outline">off</Badge>}
              </CardTitle>
              <CardDescription>
                Stops every automated order on all your accounts, including ones already queued. Closing positions from the
                dashboard stays possible.{data.kill_switch.reason ? ` Reason: ${data.kill_switch.reason}` : ""}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button variant={data.kill_switch.enabled ? "default" : "destructive"} disabled={busy} onClick={toggleKill}>
                <ShieldAlert aria-hidden /> {data.kill_switch.enabled ? "Resume automated trading" : "Stop all automated trading"}
              </Button>
            </CardContent>
          </Card>
          <div className="grid gap-3 md:grid-cols-2">
            {data.scopes.map((scope) => {
              const parts = describe(scope.limits);
              return (
                <Card key={scope.scope_key} size="sm">
                  <CardHeader>
                    <CardTitle className="flex flex-wrap items-center gap-2">
                      {scope.label}
                      <Badge variant="secondary" className="text-[0.62rem] uppercase">
                        {scope.scope_key === "global" ? "all accounts" : scope.scope_key.split(":")[0]}
                      </Badge>
                      {scope.kill_switch ? <Badge variant="outline" className="border-negative/40 text-negative">kill switch</Badge> : null}
                      {scope.circuit_open ? <Badge variant="outline" className="border-negative/40 text-negative">stopped after failures</Badge> : null}
                    </CardTitle>
                    <CardDescription>
                      {parts.length ? parts.join(" · ") : scope.scope_key === "global"
                        ? `No limits set. Defaults: signals older than ${data.defaults.max_signal_age_seconds}s are refused; an account stops after ${data.defaults.max_consecutive_failures} failures in a row.`
                        : "No limits at this level."}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Button size="sm" variant="outline" onClick={() => setEditing(scope)}>Edit limits</Button>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </>
      ) : null}
      <Dialog open={editing !== null} onOpenChange={(open) => (open ? null : setEditing(null))}>
        <DialogContent className="sm:max-w-xl">
          {editing ? <LimitsForm scope={editing} onDone={() => { setEditing(null); reload(); }} /> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
