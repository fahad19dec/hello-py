"use client";

import { Copy, KeyRound, LogIn, MoreHorizontal, Plug, Plus, Power, RefreshCw, ShieldAlert, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Field, FormError } from "@/components/form-bits";
import {
  Empty,
  ErrorNote,
  PageHeader,
  PaperBadge,
  RefreshButton,
  selectClass,
  useApiData,
} from "@/components/trading-ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import { BROKER_LABELS, fmt, money, type Broker, type BrokerAccount, type InstrumentMapping } from "@/lib/trading";

type Dialogs =
  | { name: "add" }
  | { name: "kite"; account: BrokerAccount }
  | { name: "paired"; account: BrokerAccount; token: string; apiUrl: string }
  | { name: "instruments"; account: BrokerAccount }
  | { name: "kill"; account: BrokerAccount };

function ConnectionBadge({ account }: { account: BrokerAccount }) {
  if (account.is_paper) return <Badge variant="secondary">Simulated</Badge>;
  const map: Record<BrokerAccount["connection_status"], [string, string]> = {
    connected: ["Connected", "border-positive/30 text-positive"],
    login_required: ["Login needed", "border-warning/40 text-warning"],
    not_connected: ["Not connected", "text-muted-foreground"],
    error: ["Error", "border-negative/30 text-negative"],
  };
  let [label, cls] = map[account.connection_status];
  if ((account.broker === "mt5" || account.broker === "mt4") && account.connection_status === "connected" && !account.terminal_online) {
    [label, cls] = ["Terminal offline", "border-warning/40 text-warning"];
  }
  return (
    <Badge variant="outline" className={cls}>
      {label}
    </Badge>
  );
}

function AccountCard({ account, onChange, open }: { account: BrokerAccount; onChange: () => void; open: (d: Dialogs) => void }) {
  const { me } = useSession();
  const [busy, setBusy] = useState(false);

  async function act(work: () => Promise<unknown>, success: string) {
    setBusy(true);
    try {
      await work();
      toast.success(success);
      onChange();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const id = account.id;
  const isBridge = account.broker === "mt5" || account.broker === "mt4";

  return (
    <Card size="sm" className={account.kill_switch || account.circuit_open ? "ring-negative/40" : ""}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {account.label}
          <PaperBadge paper={account.is_paper} />
          <ConnectionBadge account={account} />
          {account.status === "paused" ? <Badge variant="outline">Paused</Badge> : null}
          {account.kill_switch ? (
            <Badge variant="outline" className="border-negative/40 text-negative">
              Kill switch on
            </Badge>
          ) : null}
          {account.circuit_open ? (
            <Badge variant="outline" className="border-negative/40 text-negative">
              Stopped after failures
            </Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          {BROKER_LABELS[account.broker]}
          {account.connection_message ? ` · ${account.connection_message}` : ""}
        </CardDescription>
        <CardAction>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${account.label}`} disabled={busy}>
                <MoreHorizontal aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuItem onSelect={() => open({ name: "instruments", account })}>Instrument mappings</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => act(() => api(`/broker-accounts/${id}/refresh`, { method: "POST" }), "Balance updated")}>
                <RefreshCw aria-hidden /> Refresh balance
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() =>
                  act(
                    () => api(`/broker-accounts/${id}`, { method: "PATCH", body: { status: account.status === "paused" ? "active" : "paused" } }),
                    account.status === "paused" ? "Account resumed" : "Account paused",
                  )
                }
              >
                <Power aria-hidden /> {account.status === "paused" ? "Resume" : "Pause"}
              </DropdownMenuItem>
              {account.circuit_open ? (
                <DropdownMenuItem onSelect={() => act(() => api(`/broker-accounts/${id}/reset-circuit`, { method: "POST" }), "Circuit breaker reset")}>
                  Reset after failures
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuSeparator />
              <DropdownMenuItem
                disabled={!account.has_open_positions}
                onSelect={() => {
                  if (window.confirm(`Send market orders to close every position on ${account.label}?`))
                    void act(() => api(`/broker-accounts/${id}/close-all`, { method: "POST" }), "Close orders queued");
                }}
              >
                Close all positions
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                onSelect={() => {
                  if (window.confirm(`Archive ${account.label}? Its history is kept; it stops trading.`))
                    void act(() => api(`/broker-accounts/${id}/archive`, { method: "POST" }), "Account archived");
                }}
              >
                <Trash2 aria-hidden /> Archive
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-3">
        <dl className="grid grid-cols-3 gap-2 text-sm">
          <div>
            <dt className="text-xs text-muted-foreground">Balance</dt>
            <dd className="font-mono tabular-nums">{money(account.balance)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Equity</dt>
            <dd className="font-mono tabular-nums">{money(account.equity)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Margin free</dt>
            <dd className="font-mono tabular-nums">{money(account.margin_available)}</dd>
          </div>
        </dl>
        <p className="text-xs text-muted-foreground">
          {account.currency ? `${account.currency} · ` : ""}
          {account.snapshot_at ? `updated ${formatDateTime(account.snapshot_at, me.user.timezone)}` : "no balance reported yet"}
        </p>
        <div className="flex flex-wrap gap-2">
          {account.broker === "kite" ? (
            <>
              <Button size="sm" variant="outline" onClick={() => open({ name: "kite", account })}>
                <KeyRound aria-hidden /> {account.has_api_secret ? "Change API key" : "Add API key"}
              </Button>
              <Button
                size="sm"
                disabled={!account.has_api_secret || busy}
                onClick={async () => {
                  try {
                    const { login_url } = await api<{ login_url: string }>(`/broker-accounts/${id}/kite/login`, { method: "POST" });
                    window.location.assign(login_url);
                  } catch (err) {
                    toast.error(errorMessage(err));
                  }
                }}
              >
                <LogIn aria-hidden /> {account.connection_status === "connected" ? "Log in again" : "Log in to Zerodha"}
              </Button>
            </>
          ) : null}
          {isBridge ? (
            <Button
              size="sm"
              variant={account.bridge_paired ? "outline" : "default"}
              disabled={busy}
              onClick={async () => {
                if (account.bridge_paired && !window.confirm("Pairing again stops the current terminal until you enter the new token. Continue?")) return;
                try {
                  const result = await api<{ device_token: string; api_url: string; account: BrokerAccount }>(
                    `/broker-accounts/${id}/bridge/pair`,
                    { method: "POST" },
                  );
                  open({ name: "paired", account: result.account, token: result.device_token, apiUrl: result.api_url });
                  onChange();
                } catch (err) {
                  toast.error(errorMessage(err));
                }
              }}
            >
              <Plug aria-hidden /> {account.bridge_paired ? "Pair again" : "Pair terminal"}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant={account.kill_switch ? "default" : "destructive"}
            disabled={busy}
            onClick={() =>
              account.kill_switch
                ? act(() => api(`/broker-accounts/${id}/kill-switch`, { method: "POST", body: { enabled: false } }), "Kill switch off")
                : open({ name: "kill", account })
            }
          >
            <ShieldAlert aria-hidden /> {account.kill_switch ? "Turn kill switch off" : "Kill switch"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function AddAccountForm({ onDone }: { onDone: () => void }) {
  const [broker, setBroker] = useState<Broker>("paper");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const settings: Record<string, string> = {};
    for (const key of ["starting_balance", "slippage_pct", "fee_per_order", "fee_pct", "currency", "api_key", "symbol_suffix"]) {
      const value = String(form.get(key) ?? "").trim();
      if (value) settings[key] = value;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/broker-accounts", { method: "POST", body: { broker, label: form.get("label"), settings } });
      toast.success("Account added");
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Add a broker account</DialogTitle>
        <DialogDescription>Each account has its own limits, kill switch and connection.</DialogDescription>
      </DialogHeader>
      <FormError message={error} />
      <div className="grid gap-1.5">
        <Label htmlFor="broker">Broker</Label>
        <select id="broker" className={selectClass} value={broker} onChange={(e) => setBroker(e.target.value as Broker)}>
          {Object.entries(BROKER_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <Field id="label" label="Name" placeholder={broker === "paper" ? "Paper INR" : "Main account"} maxLength={60} required />
      {broker === "paper" ? (
        <div className="grid grid-cols-2 gap-3">
          <Field id="starting_balance" label="Starting balance" inputMode="decimal" placeholder="1000000" />
          <Field id="currency" label="Currency" placeholder="INR" maxLength={10} />
          <Field id="slippage_pct" label="Slippage %" inputMode="decimal" placeholder="0.05" />
          <Field id="fee_per_order" label="Fee per order" inputMode="decimal" placeholder="0" />
        </div>
      ) : null}
      {broker === "kite" ? (
        <p className="text-sm text-muted-foreground">
          After adding it, enter your Kite Connect API key and secret, then log in to Zerodha. Orders also need your
          server&apos;s IP registered in your Kite developer profile.
        </p>
      ) : null}
      {broker === "mt5" || broker === "mt4" ? (
        <Field id="symbol_suffix" label="Symbol suffix (optional)" placeholder="m" maxLength={16} hint="Added to alert symbols, e.g. XAUUSD becomes XAUUSDm." />
      ) : null}
      <DialogFooter>
        <Button type="submit" disabled={busy}>
          Add account
        </Button>
      </DialogFooter>
    </form>
  );
}

function KiteCredentialsForm({ account, onDone }: { account: BrokerAccount; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api(`/broker-accounts/${account.id}/kite/credentials`, {
        method: "PUT",
        body: { api_key: form.get("api_key"), api_secret: form.get("api_secret") },
      });
      toast.success("Saved. Now log in to Zerodha.");
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    }
  }
  const redirect = typeof window !== "undefined" ? `${window.location.origin}/broker-accounts/kite/callback` : "";
  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Kite Connect API key</DialogTitle>
        <DialogDescription>
          From your app at developers.kite.trade. The secret is encrypted and never shown again, not even to support.
        </DialogDescription>
      </DialogHeader>
      <FormError message={error} />
      <Field id="api_key" label="API key" defaultValue={String(account.settings.api_key ?? "")} autoComplete="off" required />
      <Field id="api_secret" label="API secret" type="password" autoComplete="off" required />
      <p className="text-xs text-muted-foreground">
        Set your app&apos;s redirect URL to <span className="break-all font-mono text-foreground">{redirect}</span>
      </p>
      <DialogFooter>
        <Button type="submit">Save</Button>
      </DialogFooter>
    </form>
  );
}

function KillSwitchForm({ account, onDone }: { account: BrokerAccount; onDone: () => void }) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await api(`/broker-accounts/${account.id}/kill-switch`, {
        method: "POST",
        body: { enabled: true, reason: new FormData(event.currentTarget).get("reason") },
      });
      toast.success(`Kill switch on for ${account.label}`);
      onDone();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }
  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Kill switch for {account.label}</DialogTitle>
        <DialogDescription>
          Every automated order for this account is refused, including ones already queued. You can still close positions from the
          account menu.
        </DialogDescription>
      </DialogHeader>
      <Field id="reason" label="Reason (optional)" maxLength={300} />
      <DialogFooter>
        <Button type="submit" variant="destructive">
          Stop trading this account
        </Button>
      </DialogFooter>
    </form>
  );
}

function PairedDialog({ token, apiUrl, account }: { token: string; apiUrl: string; account: BrokerAccount }) {
  return (
    <div className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Terminal pairing for {account.label}</DialogTitle>
        <DialogDescription>Copy the token now. It is shown once; pairing again replaces it.</DialogDescription>
      </DialogHeader>
      <div className="grid gap-1.5">
        <Label>Device token</Label>
        <div className="flex gap-2">
          <code className="flex-1 break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 font-mono text-xs">{token}</code>
          <Button variant="outline" size="icon" aria-label="Copy token" onClick={() => navigator.clipboard.writeText(token).then(() => toast.success("Copied"))}>
            <Copy aria-hidden />
          </Button>
        </div>
      </div>
      <div className="grid gap-1.5">
        <Label>ApiUrl input</Label>
        <code className="break-all rounded-md border border-border bg-muted/40 px-2 py-1.5 font-mono text-xs">{apiUrl}</code>
      </div>
      <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
        <li>Copy TradeBridgeEA into the terminal&apos;s Experts folder and compile it.</li>
        <li>Tools → Options → Expert Advisors: allow algorithmic trading and WebRequest for this site.</li>
        <li>Attach the EA to any chart with the two values above.</li>
      </ol>
    </div>
  );
}

function InstrumentsDialog({ account }: { account: BrokerAccount }) {
  const { data, error, reload } = useApiData<InstrumentMapping[]>(`/broker-accounts/${account.id}/instruments`);
  const [formError, setFormError] = useState<string | null>(null);
  const isKite = account.broker === "kite";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const element = event.currentTarget;
    const form = new FormData(element);
    const body: Record<string, string> = {};
    for (const [key, value] of form.entries()) if (String(value).trim()) body[key] = String(value).trim();
    try {
      await api(`/broker-accounts/${account.id}/instruments`, { method: "PUT", body });
      element.reset();
      setFormError(null);
      reload();
    } catch (err) {
      setFormError(errorMessage(err));
    }
  }

  return (
    <div className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Instrument mappings · {account.label}</DialogTitle>
        <DialogDescription>
          How a TradingView symbol maps to this broker&apos;s instrument.{" "}
          {isKite
            ? "Required for Zerodha: alerts for unmapped symbols are rejected, never guessed."
            : "Optional; without one the alert symbol is used as is and the broker's own rules apply."}
        </DialogDescription>
      </DialogHeader>
      <ErrorNote message={error} />
      {data && data.length > 0 ? (
        <div className="max-h-56 overflow-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Alert</TableHead>
                <TableHead>Broker</TableHead>
                <TableHead>Lot / step</TableHead>
                <TableHead>Tick</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((m) => (
                <TableRow key={m.id}>
                  <TableCell className="font-mono text-xs">{m.alert_symbol}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {m.exchange ? `${m.exchange}:` : ""}
                    {m.broker_symbol} {m.product ? <span className="text-muted-foreground">{m.product}</span> : null}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {fmt(m.lot_size)} / {fmt(m.quantity_step)}
                  </TableCell>
                  <TableCell className="tabular-nums">{fmt(m.tick_size, 6)}</TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`Remove ${m.alert_symbol}`}
                      onClick={() => api(`/broker-accounts/${account.id}/instruments/${m.id}`, { method: "DELETE" }).then(reload)}
                    >
                      <Trash2 aria-hidden />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <Empty>No mappings yet.</Empty>
      )}
      <form onSubmit={submit} className="grid gap-3 border-t border-border pt-4">
        <FormError message={formError} />
        <div className="grid grid-cols-2 gap-3">
          <Field id="alert_symbol" label="Alert symbol" placeholder="NIFTY1!" required />
          <Field id="broker_symbol" label="Broker symbol" placeholder="NIFTY24DECFUT" required />
          {isKite ? (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor="exchange">Exchange</Label>
                <select id="exchange" name="exchange" className={selectClass} defaultValue="NSE">
                  {["NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "BCD"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="product">Product</Label>
                <select id="product" name="product" className={selectClass} defaultValue="MIS">
                  {["MIS", "CNC", "NRML", "MTF"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </div>
            </>
          ) : null}
          <Field id="lot_size" label="Lot size" inputMode="decimal" placeholder="1" />
          <Field id="quantity_step" label="Quantity step" inputMode="decimal" placeholder={isKite ? "lot size" : "0.01"} />
          <Field id="tick_size" label="Tick size" inputMode="decimal" placeholder="0.05" />
          <Field id="contract_size" label="Value per point per unit" inputMode="decimal" placeholder="1" />
          <Field id="min_quantity" label="Min quantity" inputMode="decimal" />
          <Field id="max_quantity" label="Max quantity" inputMode="decimal" />
        </div>
        <Button type="submit" className="justify-self-start">
          <Plus aria-hidden /> Save mapping
        </Button>
      </form>
    </div>
  );
}

export function BrokerAccounts() {
  const { data, error, reload } = useApiData<BrokerAccount[]>("/broker-accounts", { pollMs: 10000 });
  const [dialog, setDialog] = useState<Dialogs | null>(null);
  const close = () => setDialog(null);
  const done = () => {
    close();
    reload();
  };

  return (
    <div>
      <PageHeader
        title="Broker accounts"
        description="Paper accounts simulate fills from alert prices. Live accounts place real orders only when live trading is enabled on the platform."
        actions={
          <>
            <RefreshButton onClick={reload} />
            <Button size="sm" onClick={() => setDialog({ name: "add" })}>
              <Plus aria-hidden /> Add account
            </Button>
          </>
        }
      />
      <ErrorNote message={error} />
      {data === null ? null : data.length === 0 ? (
        <Empty>No accounts yet. Start with a paper account: it needs no broker and never sends real orders.</Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {data.map((account) => (
            <AccountCard key={account.id} account={account} onChange={reload} open={setDialog} />
          ))}
        </div>
      )}
      <Dialog open={dialog !== null} onOpenChange={(open) => (open ? null : close())}>
        <DialogContent className={dialog?.name === "instruments" ? "sm:max-w-2xl" : undefined}>
          {dialog?.name === "add" ? <AddAccountForm onDone={done} /> : null}
          {dialog?.name === "kite" ? <KiteCredentialsForm account={dialog.account} onDone={done} /> : null}
          {dialog?.name === "kill" ? <KillSwitchForm account={dialog.account} onDone={done} /> : null}
          {dialog?.name === "paired" ? <PairedDialog account={dialog.account} token={dialog.token} apiUrl={dialog.apiUrl} /> : null}
          {dialog?.name === "instruments" ? <InstrumentsDialog account={dialog.account} /> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
