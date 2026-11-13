"use client";

import { useState } from "react";

import { Empty, ErrorNote, PageHeader, PaperBadge, RefreshButton, selectClass, useApiData } from "@/components/trading-ui";
import { When } from "@/components/when";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSession } from "@/lib/session";
import { fmt, money, pnlClass, type TradeRow } from "@/lib/trading";

export function TradesTable({ paper }: { paper?: boolean }) {
  const { me } = useSession();
  const { data, error, reload } = useApiData<TradeRow[]>("/trades", { query: { paper, limit: 200 } });
  return (
    <>
      <ErrorNote message={error} />
      <Card>
        <CardContent>
          {data === null ? null : data.length === 0 ? (
            <Empty>No fills recorded yet.</Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Account</TableHead>
                  <TableHead>Fill</TableHead>
                  <TableHead className="text-right">Price</TableHead>
                  <TableHead className="text-right">Fee</TableHead>
                  <TableHead className="text-right">Realised P&amp;L</TableHead>
                  <TableHead className="hidden lg:table-cell">Trade id</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell className="tabular-nums text-muted-foreground"><When iso={t.filled_at} timeZone={me.user.timezone} /></TableCell>
                    <TableCell><span className="inline-flex items-center gap-1.5">{t.account_label} <PaperBadge paper={t.is_paper} /></span></TableCell>
                    <TableCell>
                      <span className={t.side === "BUY" ? "text-positive" : "text-negative"}>{t.side}</span> {fmt(t.quantity)}{" "}
                      <span className="font-mono text-xs">{t.symbol}</span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(t.price, 6)}</TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">{money(t.fee)}</TableCell>
                    <TableCell className={`text-right tabular-nums ${pnlClass(t.realized_pnl)}`}>{Number(t.realized_pnl) ? money(t.realized_pnl) : "—"}</TableCell>
                    <TableCell className="hidden font-mono text-xs text-muted-foreground lg:table-cell">{t.trade_id}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <div className="mt-2 flex justify-end"><RefreshButton onClick={reload} /></div>
    </>
  );
}

export function Trades() {
  const [kind, setKind] = useState("");
  return (
    <div>
      <PageHeader
        title="Trade history"
        description="Every recorded fill. Live fills come from the broker; paper fills are simulated. They are never mixed in totals."
        actions={
          <select aria-label="Paper or live" className={`${selectClass} w-36`} value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Paper and live</option>
            <option value="live">Live only</option>
            <option value="paper">Paper only</option>
          </select>
        }
      />
      <TradesTable key={kind} paper={kind === "" ? undefined : kind === "paper"} />
    </div>
  );
}
