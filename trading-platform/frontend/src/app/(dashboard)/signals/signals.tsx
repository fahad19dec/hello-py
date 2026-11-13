"use client";

import { useState } from "react";

import { Empty, ErrorNote, PageHeader, RefreshButton, StateBadge, selectClass, useApiData } from "@/components/trading-ui";
import { When } from "@/components/when";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSession } from "@/lib/session";
import type { SignalRow } from "@/lib/trading";

export function Signals() {
  const { me } = useSession();
  const [status, setStatus] = useState("");
  const { data, error, reload } = useApiData<SignalRow[]>("/signals", { pollMs: 5000, query: { status: status || undefined, limit: 100 } });
  const [open, setOpen] = useState<SignalRow | null>(null);
  return (
    <div>
      <PageHeader
        title="Signal log"
        description="Every alert that reached one of your webhooks, whether it was accepted, and why if it was not. A repeated signal id is counted, never executed twice."
        actions={
          <>
            <select aria-label="Filter by status" className={`${selectClass} w-40`} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All</option>
              <option value="processed">Processed</option>
              <option value="rejected">Rejected</option>
              <option value="ignored">Ignored</option>
              <option value="received">Waiting</option>
            </select>
            <RefreshButton onClick={reload} />
          </>
        }
      />
      <ErrorNote message={error} />
      <Card>
        <CardContent>
          {data === null ? null : data.length === 0 ? (
            <Empty>No alerts received yet.</Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Received</TableHead>
                  <TableHead>Webhook</TableHead>
                  <TableHead>Alert</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="hidden md:table-cell">Detail</TableHead>
                  <TableHead className="text-right">Orders</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((s) => (
                  <TableRow key={s.id} className="cursor-pointer" onClick={() => setOpen(s)}>
                    <TableCell className="tabular-nums text-muted-foreground"><When iso={s.received_at} timeZone={me.user.timezone} /></TableCell>
                    <TableCell>{s.webhook_name}</TableCell>
                    <TableCell className="font-mono text-xs">{s.action ?? "?"} {s.symbol ?? ""}</TableCell>
                    <TableCell><StateBadge state={s.status} /></TableCell>
                    <TableCell className="hidden max-w-80 truncate text-xs text-muted-foreground md:table-cell" title={s.reason ?? undefined}>
                      {s.reason}{s.duplicate_count ? ` · ${s.duplicate_count} duplicate(s) ignored` : ""}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{s.order_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <Dialog open={open !== null} onOpenChange={(o) => (o ? null : setOpen(null))}>
        <DialogContent className="sm:max-w-xl">
          {open ? (
            <div className="grid gap-3">
              <DialogHeader>
                <DialogTitle>Signal {open.client_signal_id}</DialogTitle>
                <DialogDescription>{open.reason}</DialogDescription>
              </DialogHeader>
              <pre className="max-h-80 overflow-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-xs">{JSON.stringify(open.payload, null, 2)}</pre>
              <p className="text-xs text-muted-foreground">Passphrases are removed before an alert is stored.</p>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
