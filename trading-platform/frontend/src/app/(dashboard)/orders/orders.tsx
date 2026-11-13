"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Empty, ErrorNote, PageHeader, PaperBadge, RefreshButton, StateBadge, selectClass, useApiData } from "@/components/trading-ui";
import { When } from "@/components/when";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import { fmt, OPEN_STATES, RULE_LABELS, type Order, type OrderDetail } from "@/lib/trading";

function OrderDetailView({ id }: { id: string }) {
  const { me } = useSession();
  const { data, error } = useApiData<OrderDetail>(`/orders/${id}`);
  if (error) return <ErrorNote message={error} />;
  if (!data) return null;
  return (
    <div className="grid gap-4">
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          {data.side ?? data.intent.toUpperCase()} {fmt(data.quantity)} {data.broker_symbol} <StateBadge state={data.state} />
        </DialogTitle>
        <DialogDescription>
          {data.account_label} · id <span className="font-mono">{data.client_order_id}</span>
          {data.broker_order_id ? <> · broker <span className="font-mono">{data.broker_order_id}</span></> : null}
        </DialogDescription>
      </DialogHeader>
      {data.reason ? <p className="text-sm">{data.rule ? <strong>{RULE_LABELS[data.rule] ?? data.rule}: </strong> : null}{data.reason}</p> : null}
      <div>
        <p className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">History</p>
        <ol className="grid gap-1 text-sm">
          {data.events.map((e, i) => (
            <li key={i} className="grid grid-cols-[9rem_1fr] gap-2">
              <span className="tabular-nums text-muted-foreground"><When iso={e.at} timeZone={me.user.timezone} /></span>
              <span><span className="font-mono text-xs">{e.to}</span>{e.message ? <span className="text-muted-foreground"> — {e.message}</span> : null}</span>
            </li>
          ))}
        </ol>
      </div>
      {data.fills.length ? (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">Fills</p>
          {data.fills.map((f) => (
            <p key={f.trade_id} className="font-mono text-xs">
              {fmt(f.quantity)} @ {fmt(f.price, 6)} · fee {fmt(f.fee)} · {f.trade_id}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function OrdersTable({ paper, title, description }: { paper?: boolean; title: string; description: string }) {
  const { me } = useSession();
  const [state, setState] = useState("open");
  const { data, error, reload } = useApiData<Order[]>("/orders", { pollMs: 5000, query: { state: state || undefined, paper, limit: 100 } });
  const [detail, setDetail] = useState<string | null>(null);

  async function act(order: Order, action: "approve" | "reject" | "cancel") {
    try {
      await api(`/orders/${order.id}/${action}`, { method: "POST" });
      toast.success(action === "approve" ? "Approved; queued for execution" : action === "reject" ? "Rejected" : "Cancelled");
      reload();
    } catch (err) {
      toast.error(errorMessage(err));
      reload();
    }
  }

  return (
    <div>
      <PageHeader
        title={title}
        description={description}
        actions={
          <>
            <select aria-label="Filter by state" className={`${selectClass} w-44`} value={state} onChange={(e) => setState(e.target.value)}>
              <option value="open">In progress</option>
              <option value="">All</option>
              {["AWAITING_APPROVAL", "FILLED", "REJECTED", "FAILED", "UNKNOWN", "EXPIRED", "CANCELLED"].map((s) => (
                <option key={s} value={s}>{s.replaceAll("_", " ").toLowerCase()}</option>
              ))}
            </select>
            <RefreshButton onClick={reload} />
          </>
        }
      />
      <ErrorNote message={error} />
      <Card>
        <CardContent>
          {data === null ? null : data.length === 0 ? (
            <Empty>{state === "open" ? "No orders in progress." : "No orders match."}</Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Account</TableHead>
                  <TableHead>Order</TableHead>
                  <TableHead className="text-right">Qty</TableHead>
                  <TableHead className="hidden text-right md:table-cell">Avg price</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="hidden lg:table-cell">Reason</TableHead>
                  <TableHead className="text-right"><span className="sr-only">Actions</span></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((o) => (
                  <TableRow key={o.id} className="cursor-pointer" onClick={() => setDetail(o.id)}>
                    <TableCell className="tabular-nums text-muted-foreground"><When iso={o.created_at} timeZone={me.user.timezone} /></TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5">{o.account_label} <PaperBadge paper={o.is_paper} /></span>
                    </TableCell>
                    <TableCell>
                      <span className={o.side === "BUY" ? "text-positive" : o.side === "SELL" ? "text-negative" : ""}>
                        {o.side ?? o.intent.toUpperCase()}
                      </span>{" "}
                      <span className="font-mono text-xs">{o.broker_symbol}</span>
                      <span className="text-xs text-muted-foreground"> {o.order_type !== "MARKET" ? o.order_type : ""}{o.source !== "signal" ? ` · ${o.source}` : ""}</span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(o.filled_quantity)} / {fmt(o.quantity)}</TableCell>
                    <TableCell className="hidden text-right tabular-nums md:table-cell">{fmt(o.average_price, 6)}</TableCell>
                    <TableCell><StateBadge state={o.state} /></TableCell>
                    <TableCell className="hidden max-w-72 truncate text-xs text-muted-foreground lg:table-cell" title={o.reason ?? undefined}>
                      {o.rule ? `${RULE_LABELS[o.rule] ?? o.rule}: ` : ""}{o.reason}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right" onClick={(e) => e.stopPropagation()}>
                      {o.state === "AWAITING_APPROVAL" ? (
                        <>
                          <Button size="icon-xs" aria-label="Approve" onClick={() => act(o, "approve")}><Check aria-hidden /></Button>{" "}
                          <Button size="icon-xs" variant="destructive" aria-label="Reject" onClick={() => act(o, "reject")}><X aria-hidden /></Button>
                        </>
                      ) : OPEN_STATES.has(o.state) && (o.state === "QUEUED" || (o.is_paper && o.state === "SUBMITTED")) ? (
                        <Button size="xs" variant="outline" onClick={() => act(o, "cancel")}>Cancel</Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <Dialog open={detail !== null} onOpenChange={(open) => (open ? null : setDetail(null))}>
        <DialogContent className="sm:max-w-xl">{detail ? <OrderDetailView id={detail} /> : null}</DialogContent>
      </Dialog>
    </div>
  );
}

export function Orders() {
  return (
    <OrdersTable
      title="Orders"
      description="Every order and why it is where it is. Submitted means the broker accepted it; filled means the broker reported the execution. Unknown orders are being checked with the broker and are never re-sent blindly."
    />
  );
}
