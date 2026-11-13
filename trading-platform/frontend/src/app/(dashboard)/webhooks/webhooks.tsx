"use client";

import { Copy, FlaskConical, Plus, RotateCw, Ban } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Field, FormError } from "@/components/form-bits";
import { Empty, ErrorNote, PageHeader, RefreshButton, selectClass, useApiData } from "@/components/trading-ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Strategy, Webhook } from "@/lib/trading";

type Report = { valid: boolean; problems: string[]; action?: string; signal_age_seconds?: number;
  accounts: { account: string; broker: string; result: string; quantity?: string }[] };

function sampleMessage(code: string) {
  return JSON.stringify(
    { strategy_id: code, signal_id: "{{strategy.order.id}}-{{timenow}}", symbol: "{{ticker}}", action: "{{strategy.order.action}}",
      order_type: "MARKET", quantity: "{{strategy.order.contracts}}", price: "{{close}}", timestamp: "{{timenow}}", timeframe: "{{interval}}" },
    null, 2,
  );
}

function Copyable({ value, label }: { value: string; label: string }) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      <div className="flex gap-2">
        <code className="flex-1 break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 font-mono text-xs">{value}</code>
        <Button variant="outline" size="icon" aria-label={`Copy ${label}`} onClick={() => navigator.clipboard.writeText(value).then(() => toast.success("Copied"))}>
          <Copy aria-hidden />
        </Button>
      </div>
    </div>
  );
}

function CreateForm({ strategies, initial, onCreated }: { strategies: Strategy[]; initial?: string | null; onCreated: (r: { url: string; signing_secret: string | null; code: string }) => void }) {
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const strategyId = String(form.get("strategy_id"));
    try {
      const result = await api<{ url: string; signing_secret: string | null }>("/webhooks", {
        method: "POST",
        body: { strategy_id: strategyId, name: form.get("name"), passphrase: String(form.get("passphrase") ?? "") || null,
          require_signature: form.get("require_signature") === "on" },
      });
      onCreated({ ...result, code: strategies.find((s) => s.id === strategyId)?.code ?? "" });
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  if (strategies.length === 0)
    return (
      <div className="grid gap-3">
        <DialogHeader>
          <DialogTitle>Create a strategy first</DialogTitle>
          <DialogDescription>Every webhook belongs to a strategy.</DialogDescription>
        </DialogHeader>
        <Button asChild>
          <Link href="/strategies">Go to strategies</Link>
        </Button>
      </div>
    );
  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>New webhook</DialogTitle>
        <DialogDescription>The URL is shown once. Anyone who has it can send alerts, so treat it like a password.</DialogDescription>
      </DialogHeader>
      <FormError message={error} />
      <div className="grid gap-1.5">
        <Label htmlFor="strategy_id">Strategy</Label>
        <select id="strategy_id" name="strategy_id" className={selectClass} defaultValue={initial ?? strategies[0].id}>
          {strategies.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.code})
            </option>
          ))}
        </select>
      </div>
      <Field id="name" label="Name" placeholder="TradingView 15m" maxLength={100} required />
      <Field id="passphrase" label="Passphrase (optional)" type="password" minLength={8} autoComplete="off"
        hint='If set, alerts must include "passphrase". A leaked URL alone is then not enough.' />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="require_signature" className="size-4 accent-primary" />
        Require an HMAC signature header (not possible from TradingView)
      </label>
      <DialogFooter>
        <Button type="submit">Create webhook</Button>
      </DialogFooter>
    </form>
  );
}

function TestForm({ webhook, code }: { webhook: Webhook; code: string }) {
  const [message, setMessage] = useState(() =>
    JSON.stringify({ strategy_id: code, symbol: "RELIANCE", action: "BUY", quantity: 1, price: 2500, timestamp: new Date().toISOString() }, null, 2),
  );
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    try {
      setReport(await api<Report>(`/webhooks/${webhook.id}/test`, { method: "POST", body: { message } }));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  return (
    <div className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Test {webhook.name}</DialogTitle>
        <DialogDescription>Checks a message exactly as a real alert would be checked. Nothing is stored or executed.</DialogDescription>
      </DialogHeader>
      <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={8}
        className="rounded-lg border border-input bg-input/30 p-2 font-mono text-xs outline-none focus-visible:border-ring" />
      <Button onClick={run} className="justify-self-start">
        <FlaskConical aria-hidden /> Check message
      </Button>
      <ErrorNote message={error} />
      {report ? (
        <div className="grid gap-2 text-sm">
          <p className={report.valid ? "text-positive" : "text-negative"}>
            {report.valid ? `Valid ${report.action} alert.` : "This alert would be rejected:"}
          </p>
          {report.problems.map((p) => (
            <p key={p} className="text-negative">• {p}</p>
          ))}
          {report.accounts.map((a) => (
            <p key={a.account} className="text-muted-foreground">
              <span className="text-foreground">{a.account}</span>: {a.result}
              {a.quantity ? ` (quantity ${a.quantity})` : ""}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function Webhooks() {
  const { me } = useSession();
  const preselect = useSearchParams().get("strategy");
  const { data, error, reload } = useApiData<Webhook[]>("/webhooks");
  const strategies = useApiData<Strategy[]>("/strategies");
  const [dialog, setDialog] = useState<
    { name: "create" } | { name: "created"; url: string; signing_secret: string | null; code: string } | { name: "test"; webhook: Webhook } | null
  >(preselect ? { name: "create" } : null);

  async function act(work: () => Promise<{ url?: string } | unknown>, message: string, code?: string) {
    try {
      const result = (await work()) as { url?: string };
      if (result && result.url) setDialog({ name: "created", url: result.url, signing_secret: null, code: code ?? "" });
      toast.success(message);
      reload();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  const codeOf = (w: Webhook) => w.strategy_code ?? "";

  return (
    <div>
      <PageHeader
        title="Webhook connections"
        description="Paste a webhook URL into a TradingView alert's Webhook URL field. Each alert is checked, stored once, and traded on the strategy's accounts."
        actions={
          <>
            <RefreshButton onClick={reload} />
            <Button size="sm" onClick={() => setDialog({ name: "create" })}>
              <Plus aria-hidden /> New webhook
            </Button>
          </>
        }
      />
      <ErrorNote message={error} />
      <Card>
        <CardContent>
          {data === null ? null : data.length === 0 ? (
            <Empty>No webhooks yet.</Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Strategy</TableHead>
                  <TableHead className="hidden md:table-cell">URL</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden lg:table-cell">Last alert</TableHead>
                  <TableHead className="text-right">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((w) => (
                  <TableRow key={w.id} className={w.revoked ? "opacity-60" : undefined}>
                    <TableCell>
                      {w.name}
                      <div className="flex gap-1 pt-0.5">
                        {w.has_passphrase ? <Badge variant="outline" className="text-[0.6rem]">passphrase</Badge> : null}
                        {w.requires_signature ? <Badge variant="outline" className="text-[0.6rem]">signed</Badge> : null}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{w.strategy_code}</TableCell>
                    <TableCell className="hidden font-mono text-xs text-muted-foreground md:table-cell">{w.url_hint}</TableCell>
                    <TableCell>
                      {w.revoked ? <Badge variant="outline" className="text-negative">Revoked</Badge> : w.enabled ? (
                        <Badge variant="outline" className="border-positive/30 text-positive">Enabled</Badge>
                      ) : (
                        <Badge variant="outline">Disabled</Badge>
                      )}
                    </TableCell>
                    <TableCell className="hidden tabular-nums text-muted-foreground lg:table-cell">
                      {formatDateTime(w.last_received_at, me.user.timezone)}
                    </TableCell>
                    <TableCell className="space-x-1 whitespace-nowrap text-right">
                      {!w.revoked ? (
                        <>
                          <Button size="xs" variant="outline" onClick={() => setDialog({ name: "test", webhook: w })}>Test</Button>
                          <Button size="xs" variant="outline" onClick={() =>
                            act(() => api(`/webhooks/${w.id}`, { method: "PATCH", body: { enabled: !w.enabled } }), w.enabled ? "Disabled" : "Enabled")}>
                            {w.enabled ? "Disable" : "Enable"}
                          </Button>
                          <Button size="icon-xs" variant="ghost" aria-label={`New URL for ${w.name}`} onClick={() => {
                            if (window.confirm("Create a new URL? The current one stops working immediately."))
                              void act(() => api(`/webhooks/${w.id}/rotate`, { method: "POST" }), "New URL created", codeOf(w));
                          }}>
                            <RotateCw aria-hidden />
                          </Button>
                          <Button size="icon-xs" variant="ghost" aria-label={`Revoke ${w.name}`} onClick={() => {
                            if (window.confirm("Revoke permanently? Use this if the URL may have leaked."))
                              void act(() => api(`/webhooks/${w.id}/revoke`, { method: "POST" }), "Revoked");
                          }}>
                            <Ban aria-hidden />
                          </Button>
                        </>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <p className="mt-3 text-xs text-muted-foreground">
        Message format and examples: <Link href="/api-docs" className="text-primary hover:underline">API documentation</Link>.
      </p>
      <Dialog open={dialog !== null} onOpenChange={(open) => (open ? null : setDialog(null))}>
        <DialogContent className="sm:max-w-xl">
          {dialog?.name === "create" ? (
            <CreateForm strategies={(strategies.data ?? []).filter((s) => s.status !== "archived")} initial={preselect}
              onCreated={(r) => { setDialog({ name: "created", ...r }); reload(); }} />
          ) : null}
          {dialog?.name === "created" ? (
            <div className="grid gap-4">
              <DialogHeader>
                <DialogTitle>Webhook URL</DialogTitle>
                <DialogDescription>Shown once. Paste it into the TradingView alert&apos;s Webhook URL field.</DialogDescription>
              </DialogHeader>
              <Copyable value={dialog.url} label="Webhook URL" />
              {dialog.signing_secret ? <Copyable value={dialog.signing_secret} label="Signing secret" /> : null}
              <Copyable value={sampleMessage(dialog.code)} label="Alert message for a TradingView strategy" />
            </div>
          ) : null}
          {dialog?.name === "test" ? <TestForm webhook={dialog.webhook} code={codeOf(dialog.webhook)} /> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
