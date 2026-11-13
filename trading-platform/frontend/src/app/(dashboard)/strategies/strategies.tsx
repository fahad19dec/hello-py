"use client";

import { Link2, Plus, ShieldAlert, Unlink } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Field, FormError } from "@/components/form-bits";
import { Empty, ErrorNote, PageHeader, PaperBadge, RefreshButton, selectClass, useApiData } from "@/components/trading-ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { fmt, type BrokerAccount, type Strategy, type StrategyLink } from "@/lib/trading";

const SIZING: Record<StrategyLink["sizing_mode"], string> = {
  signal: "Quantity from the alert",
  fixed: "Fixed size",
  percent_capital: "% of capital",
  risk_per_trade: "Risk per trade (amount)",
};

const MODE: Record<StrategyLink["execution_mode"], string> = {
  paper: "Paper",
  semi_automatic: "Semi-automatic (you approve)",
  automatic: "Fully automatic",
};

function StrategyForm({ onDone }: { onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body: Record<string, unknown> = { code: form.get("code"), name: form.get("name"), description: form.get("description") };
    for (const key of ["default_stop_loss_pct", "default_take_profit_pct"]) {
      const value = String(form.get(key) ?? "").trim();
      if (value) body[key] = value;
    }
    try {
      await api("/strategies", { method: "POST", body });
      toast.success("Strategy created");
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>New strategy</DialogTitle>
        <DialogDescription>A strategy receives alerts through its webhooks and trades the accounts you link to it.</DialogDescription>
      </DialogHeader>
      <FormError message={error} />
      <Field id="code" label="Strategy ID" placeholder="BTC_STRATEGY_01" pattern="[A-Za-z0-9_.\-]+" maxLength={60} required
        hint='Alerts may include it as "strategy_id"; a mismatch is rejected.' />
      <Field id="name" label="Name" maxLength={100} required />
      <Field id="description" label="Description" maxLength={2000} />
      <div className="grid grid-cols-2 gap-3">
        <Field id="default_stop_loss_pct" label="Default stop loss %" inputMode="decimal" placeholder="none" />
        <Field id="default_take_profit_pct" label="Default take profit %" inputMode="decimal" placeholder="none" />
      </div>
      <DialogFooter>
        <Button type="submit">Create</Button>
      </DialogFooter>
    </form>
  );
}

function LinkForm({ strategy, accounts, link, onDone }: { strategy: Strategy; accounts: BrokerAccount[]; link?: StrategyLink; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<StrategyLink["sizing_mode"]>(link?.sizing_mode ?? "signal");
  const linked = new Set(strategy.accounts.map((a) => a.broker_account_id));
  const choices = link ? accounts.filter((a) => a.id === link.broker_account_id) : accounts.filter((a) => !linked.has(a.id));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api(`/strategies/${strategy.id}/accounts/${form.get("account")}`, {
        method: "PUT",
        body: {
          enabled: form.get("enabled") === "on",
          approval_required: form.get("approval_required") === "on",
          sizing_mode: mode,
          sizing_value: mode === "signal" ? null : form.get("sizing_value"),
        },
      });
      toast.success("Saved");
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (choices.length === 0)
    return (
      <div className="grid gap-3">
        <DialogHeader>
          <DialogTitle>No accounts to link</DialogTitle>
          <DialogDescription>Every account is already linked, or you have none yet.</DialogDescription>
        </DialogHeader>
        <Button asChild variant="outline">
          <Link href="/broker-accounts">Add a broker account</Link>
        </Button>
      </div>
    );

  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>{link ? `Settings for ${link.label}` : "Link an account"}</DialogTitle>
        <DialogDescription>Each account sizes and approves orders independently.</DialogDescription>
      </DialogHeader>
      <FormError message={error} />
      <div className="grid gap-1.5">
        <Label htmlFor="account">Account</Label>
        <select id="account" name="account" className={selectClass} defaultValue={choices[0].id} disabled={!!link}>
          {choices.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label} ({a.is_paper ? "paper" : a.broker})
            </option>
          ))}
        </select>
        {link ? <input type="hidden" name="account" value={link.broker_account_id} /> : null}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="sizing_mode">Order size</Label>
          <select id="sizing_mode" className={selectClass} value={mode} onChange={(e) => setMode(e.target.value as StrategyLink["sizing_mode"])}>
            {Object.entries(SIZING).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        {mode !== "signal" ? (
          <Field id="sizing_value" label={mode === "fixed" ? "Size (qty or lots)" : mode === "percent_capital" ? "Percent" : "Amount at risk"}
            inputMode="decimal" defaultValue={link?.sizing_value ? String(link.sizing_value) : ""} required />
        ) : null}
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="approval_required" defaultChecked={link?.approval_required ?? false} className="size-4 accent-primary" />
        Semi-automatic: each order waits for my approval (5 minutes, then it expires)
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="enabled" defaultChecked={link?.enabled ?? true} className="size-4 accent-primary" />
        Enabled
      </label>
      <DialogFooter>
        <Button type="submit">Save</Button>
      </DialogFooter>
    </form>
  );
}

function StrategyCard({ strategy, accounts, reload }: { strategy: Strategy; accounts: BrokerAccount[]; reload: () => void }) {
  const [dialog, setDialog] = useState<{ link?: StrategyLink } | null>(null);

  async function act(work: () => Promise<unknown>, message: string) {
    try {
      await work();
      toast.success(message);
      reload();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {strategy.name}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground">{strategy.code}</code>
          {strategy.status !== "active" ? <Badge variant="outline">{strategy.status}</Badge> : null}
          {strategy.kill_switch ? (
            <Badge variant="outline" className="border-negative/40 text-negative">
              Kill switch on
            </Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          {strategy.description || "No description."} · version {strategy.version} · {strategy.webhook_count} webhook
          {strategy.webhook_count === 1 ? "" : "s"}
          {strategy.default_stop_loss_pct ? ` · default stop ${fmt(strategy.default_stop_loss_pct)}%` : ""}
        </CardDescription>
        <CardAction className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() =>
            act(() => api(`/strategies/${strategy.id}`, { method: "PATCH", body: { status: strategy.status === "active" ? "paused" : "active" } }),
              strategy.status === "active" ? "Strategy paused" : "Strategy resumed")}>
            {strategy.status === "active" ? "Pause" : "Resume"}
          </Button>
          <Button size="sm" variant={strategy.kill_switch ? "default" : "destructive"} onClick={() =>
            act(() => api(`/strategies/${strategy.id}/kill-switch`, { method: "POST", body: { enabled: !strategy.kill_switch } }),
              strategy.kill_switch ? "Kill switch off" : "Kill switch on")}>
            <ShieldAlert aria-hidden /> {strategy.kill_switch ? "Kill switch off" : "Kill switch"}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-3">
        {strategy.accounts.length === 0 ? (
          <Empty>No accounts linked: alerts for this strategy will be logged but not traded.</Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Account</TableHead>
                <TableHead>Mode</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>State</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {strategy.accounts.map((link) => (
                <TableRow key={link.broker_account_id}>
                  <TableCell>
                    <span className="inline-flex items-center gap-2">
                      {link.label} <PaperBadge paper={link.broker === "paper"} />
                    </span>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{MODE[link.execution_mode]}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {SIZING[link.sizing_mode]}
                    {link.sizing_value ? `: ${fmt(link.sizing_value)}` : ""}
                  </TableCell>
                  <TableCell>{link.enabled ? "Enabled" : <span className="text-muted-foreground">Disabled</span>}</TableCell>
                  <TableCell className="space-x-1 text-right">
                    <Button size="xs" variant="outline" onClick={() => setDialog({ link })}>
                      Edit
                    </Button>
                    <Button size="icon-xs" variant="ghost" aria-label={`Unlink ${link.label}`} onClick={() =>
                      act(() => api(`/strategies/${strategy.id}/accounts/${link.broker_account_id}`, { method: "DELETE" }), "Unlinked")}>
                      <Unlink aria-hidden />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => setDialog({})}>
            <Link2 aria-hidden /> Link account
          </Button>
          <Button size="sm" variant="ghost" asChild>
            <Link href={`/webhooks?strategy=${strategy.id}`}>Webhooks</Link>
          </Button>
        </div>
      </CardContent>
      <Dialog open={dialog !== null} onOpenChange={(open) => (open ? null : setDialog(null))}>
        <DialogContent>
          {dialog ? <LinkForm strategy={strategy} accounts={accounts} link={dialog.link} onDone={() => { setDialog(null); reload(); }} /> : null}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export function Strategies() {
  const { data, error, reload } = useApiData<Strategy[]>("/strategies");
  const accounts = useApiData<BrokerAccount[]>("/broker-accounts");
  const [creating, setCreating] = useState(false);
  return (
    <div>
      <PageHeader
        title="Strategies"
        description="A strategy turns TradingView alerts into orders on the accounts linked to it. Every change creates a new version."
        actions={
          <>
            <RefreshButton onClick={reload} />
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus aria-hidden /> New strategy
            </Button>
          </>
        }
      />
      <ErrorNote message={error} />
      {data === null ? null : data.length === 0 ? (
        <Empty>No strategies yet.</Empty>
      ) : (
        <div className="grid gap-4">
          {data.map((s) => (
            <StrategyCard key={s.id} strategy={s} accounts={accounts.data ?? []} reload={reload} />
          ))}
        </div>
      )}
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <StrategyForm onDone={() => { setCreating(false); reload(); }} />
        </DialogContent>
      </Dialog>
    </div>
  );
}
