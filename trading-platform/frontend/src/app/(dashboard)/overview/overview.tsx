"use client";

import { CircleAlert, CircleCheck, Laptop, MailCheck, ShieldCheck, ShieldOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { describeAction, describeDevice } from "@/lib/format";
import { When } from "@/components/when";
import { useSession } from "@/lib/session";
import { money, pnlClass, type Num } from "@/lib/trading";
import type { Activity, SessionInfo, SystemStatus } from "@/lib/types";
import { PaperBadge } from "@/components/trading-ui";

type Split = {
  orders_today: number;
  successful_executions: number;
  failed_executions: number;
  open_positions: number;
  realized_pnl_today: Num;
  unrealized_pnl: Num;
  volume_today: Num;
  success_rate: number | null;
  avg_signal_to_submission_ms: number | null;
};
type OverviewData = {
  accounts: { total: number; connected: number; paper: number; live: number; kill_switched: number };
  active_strategies: number;
  active_webhooks: number;
  signals_today: { total: number; processed?: number; rejected?: number };
  awaiting_approval: number;
  paper: Split;
  live: Split;
};

function TradingFigures({ data, kind }: { data: Split; kind: "paper" | "live" }) {
  const cells: [string, string, string?][] = [
    ["Orders today", String(data.orders_today)],
    ["Filled or applied", String(data.successful_executions)],
    ["Rejected or failed", String(data.failed_executions)],
    ["Success rate", data.success_rate === null ? "—" : `${data.success_rate}%`],
    ["Open positions", String(data.open_positions)],
    ["Realised P&L today", money(data.realized_pnl_today), pnlClass(data.realized_pnl_today)],
    ["Unrealised P&L", money(data.unrealized_pnl), pnlClass(data.unrealized_pnl)],
    ["Volume today", money(data.volume_today)],
    ["Signal to submission", data.avg_signal_to_submission_ms === null ? "—" : `${data.avg_signal_to_submission_ms} ms`],
  ];
  return (
    <div>
      <p className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
        <PaperBadge paper={kind === "paper"} /> {kind === "paper" ? "simulated fills" : "broker fills"}
      </p>
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-3">
        {cells.map(([label, value, cls]) => (
          <div key={label} className="bg-card px-3 py-2.5">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className={`mt-0.5 font-mono text-base tabular-nums ${cls ?? ""}`}>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function StatusCard({
  icon,
  label,
  value,
  tone = "neutral",
  children,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  tone?: "neutral" | "good" | "attention";
  children?: ReactNode;
}) {
  const toneClass = tone === "good" ? "text-positive" : tone === "attention" ? "text-warning" : "text-foreground";
  return (
    <Card size="sm">
      <CardContent className="flex flex-col gap-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {icon}
          {label}
        </div>
        <p className={`text-lg font-semibold tracking-tight ${toneClass}`}>{value}</p>
        {children ? <div className="text-xs text-muted-foreground">{children}</div> : null}
      </CardContent>
    </Card>
  );
}

export function Overview() {
  const { me } = useSession();
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [activity, setActivity] = useState<Activity[] | null>(null);
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [trading, setTrading] = useState<OverviewData | null>(null);

  useEffect(() => {
    api<SessionInfo[]>("/auth/sessions").then(setSessions).catch(() => setSessions([]));
    api<Activity[]>("/users/me/activity", { query: { limit: 8 } }).then(setActivity).catch(() => setActivity([]));
    api<SystemStatus>("/system/status").then(setSystem).catch(() => setSystem(null));
    const load = () => api<OverviewData>("/overview").then(setTrading).catch(() => undefined);
    void load();
    const timer = window.setInterval(load, 10000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="grid gap-6">
      <section aria-label="Account status" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatusCard
          icon={<MailCheck className="size-3.5" aria-hidden />}
          label="Email"
          value={me.user.email_verified ? "Confirmed" : "Not confirmed"}
          tone={me.user.email_verified ? "good" : "attention"}
        >
          <span className="break-all">{me.user.email}</span>
        </StatusCard>
        <StatusCard
          icon={me.user.mfa_enabled ? <ShieldCheck className="size-3.5" aria-hidden /> : <CircleAlert className="size-3.5" aria-hidden />}
          label="Two-factor authentication"
          value={me.user.mfa_enabled ? "On" : "Off"}
          tone={me.user.mfa_enabled ? "good" : "attention"}
        >
          {me.user.mfa_enabled ? (
            "Sign-in needs your authenticator app."
          ) : (
            <Link href="/settings?tab=security" className="text-primary underline-offset-4 hover:underline">
              Turn it on before connecting a broker
            </Link>
          )}
        </StatusCard>
        <StatusCard
          icon={<Laptop className="size-3.5" aria-hidden />}
          label="Signed-in devices"
          value={sessions === null ? "…" : String(sessions.length)}
        >
          <Link href="/settings?tab=sessions" className="text-primary underline-offset-4 hover:underline">
            Review sessions
          </Link>
        </StatusCard>
        <StatusCard
          icon={<ShieldOff className="size-3.5" aria-hidden />}
          label="Live trading"
          value={system === null ? "…" : system.live_trading_enabled ? "Enabled" : "Off"}
          tone={system?.live_trading_enabled ? "attention" : "neutral"}
        >
          {system?.live_trading_enabled
            ? "Real orders can be sent."
            : "No real orders can be sent. It stays off until a compliance review is signed off."}
        </StatusCard>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Trading today</CardTitle>
          <CardDescription>
            {trading
              ? `${trading.accounts.total} account(s), ${trading.accounts.connected} connected · ${trading.active_strategies} active strateg${trading.active_strategies === 1 ? "y" : "ies"} · ${trading.active_webhooks} webhook(s) · ${trading.signals_today.total} signal(s) today`
              : "Loading…"}
            {trading && trading.awaiting_approval ? (
              <>
                {" · "}
                <Link href="/orders" className="text-warning underline-offset-4 hover:underline">
                  {trading.awaiting_approval} order(s) waiting for your approval
                </Link>
              </>
            ) : null}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5 lg:grid-cols-2">
          {trading ? (
            <>
              <TradingFigures data={trading.live} kind="live" />
              <TradingFigures data={trading.paper} kind="paper" />
            </>
          ) : (
            <Skeleton className="h-40 w-full lg:col-span-2" />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent account activity</CardTitle>
          <CardDescription>Sign-ins and security changes on your account. Tell support if something is not yours.</CardDescription>
        </CardHeader>
        <CardContent>
          {activity === null ? (
            <Skeleton className="h-40 w-full" />
          ) : activity.length === 0 ? (
            <p className="text-sm text-muted-foreground">No activity recorded yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Event</TableHead>
                  <TableHead>When</TableHead>
                  <TableHead className="hidden md:table-cell">Device</TableHead>
                  <TableHead className="hidden md:table-cell">IP address</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {activity.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell>
                      <span className="inline-flex items-center gap-2">
                        {entry.action.includes("failed") ? (
                          <CircleAlert className="size-3.5 text-warning" aria-hidden />
                        ) : (
                          <CircleCheck className="size-3.5 text-muted-foreground" aria-hidden />
                        )}
                        {describeAction(entry.action)}
                        {entry.actor === "staff" ? <span className="text-xs text-muted-foreground">(support)</span> : null}
                      </span>
                    </TableCell>
                    <TableCell className="tabular-nums text-muted-foreground">
                      <When iso={entry.created_at} timeZone={me.user.timezone} />
                    </TableCell>
                    <TableCell className="hidden text-muted-foreground md:table-cell">
                      {entry.actor === "staff" ? "—" : describeDevice(entry.user_agent)}
                    </TableCell>
                    <TableCell className="hidden font-mono text-xs text-muted-foreground md:table-cell">
                      {entry.ip ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <Button asChild variant="link" className="mt-2 px-0">
            <Link href="/settings?tab=activity">See all activity</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
