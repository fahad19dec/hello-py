"use client";

import { Empty, ErrorNote, PageHeader, PaperBadge, RefreshButton, useApiData } from "@/components/trading-ui";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmt, money, pnlClass, type PositionRow } from "@/lib/trading";

export function PositionsTable({ paper }: { paper?: boolean }) {
  const { data, error, reload } = useApiData<PositionRow[]>("/positions", { pollMs: 5000 });
  const rows = (data ?? []).filter((p) => paper === undefined || p.is_paper === paper);
  return (
    <>
      <ErrorNote message={error} />
      <Card>
        <CardContent>
          {data === null ? null : rows.length === 0 ? (
            <Empty>No open positions.</Empty>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Account</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead className="text-right">Quantity</TableHead>
                  <TableHead className="text-right">Avg price</TableHead>
                  <TableHead className="text-right">Last price</TableHead>
                  <TableHead className="text-right">Unrealised</TableHead>
                  <TableHead className="hidden md:table-cell">Stop / target</TableHead>
                  <TableHead className="hidden lg:table-cell">Broker check</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell><span className="inline-flex items-center gap-1.5">{p.account_label} <PaperBadge paper={p.is_paper} /></span></TableCell>
                    <TableCell className="font-mono text-xs">{p.symbol}</TableCell>
                    <TableCell className={`text-right tabular-nums ${Number(p.quantity) > 0 ? "text-positive" : Number(p.quantity) < 0 ? "text-negative" : ""}`}>{fmt(p.quantity)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(p.average_price, 6)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(p.last_price, 6)}</TableCell>
                    <TableCell className={`text-right tabular-nums ${pnlClass(p.unrealized_pnl)}`}>{money(p.unrealized_pnl)}</TableCell>
                    <TableCell className="hidden tabular-nums text-muted-foreground md:table-cell">{fmt(p.stop_loss, 6)} / {fmt(p.take_profit, 6)}</TableCell>
                    <TableCell className="hidden lg:table-cell">
                      {p.mismatch ? (
                        <Badge variant="outline" className="border-warning/40 text-warning">Broker shows {fmt(p.broker_quantity)}</Badge>
                      ) : p.broker_quantity !== null ? (
                        <span className="text-xs text-muted-foreground">matches</span>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
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

export function Positions() {
  return (
    <div>
      <PageHeader title="Open positions" description="Positions the platform opened, kept from recorded fills. Where the broker reports its own positions (MetaTrader), any difference is flagged. Unrealised P&L uses the last price seen in an alert or fill." />
      <PositionsTable />
    </div>
  );
}
