"use client";

import { CircleAlert, CircleCheck, Laptop, MailCheck, ShieldCheck, ShieldOff } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { describeAction, describeDevice, formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Activity, SessionInfo, SystemStatus } from "@/lib/types";

// Trading figures the overview will show once signals and orders exist. They
// are listed so the layout is visible, and deliberately carry no numbers.
const TRADING_METRICS = [
  "Connected broker accounts",
  "Active strategies",
  "Active webhooks",
  "Signals received today",
  "Successful executions",
  "Failed executions",
  "Open positions",
  "Realised P&L today",
  "Unrealised P&L",
  "Trading volume",
  "Execution success rate",
  "Signal-to-submission latency",
];

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

  useEffect(() => {
    api<SessionInfo[]>("/auth/sessions").then(setSessions).catch(() => setSessions([]));
    api<Activity[]>("/users/me/activity", { query: { limit: 8 } }).then(setActivity).catch(() => setActivity([]));
    api<SystemStatus>("/system/status").then(setSystem).catch(() => setSystem(null));
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
          <CardTitle>Trading activity</CardTitle>
          <CardDescription>
            There is no trading data yet: webhooks, strategies and broker connections are not built. These figures will be
            calculated from stored signals, orders and fills, never from sample values.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-3 lg:grid-cols-4">
            {TRADING_METRICS.map((metric) => (
              <div key={metric} className="bg-card px-3 py-3">
                <dt className="text-xs text-muted-foreground">{metric}</dt>
                <dd className="mt-1 font-mono text-base text-muted-foreground/60" aria-label="No data">
                  —
                </dd>
              </div>
            ))}
          </dl>
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
                      {formatDateTime(entry.created_at, me.user.timezone)}
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
