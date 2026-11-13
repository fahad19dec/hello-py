"use client";

import Link from "next/link";

import { PageHeader, useApiData } from "@/components/trading-ui";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { money, pnlClass, type BrokerAccount } from "@/lib/trading";

import { OrdersTable } from "../orders/orders";
import { PositionsTable } from "../positions/positions";

export function PaperTrading() {
  const { data } = useApiData<BrokerAccount[]>("/broker-accounts");
  const paper = (data ?? []).filter((a) => a.is_paper);
  return (
    <div className="grid gap-6">
      <PageHeader
        title="Paper trading"
        description="Simulated accounts: orders fill at the price your alert carries, moved by the slippage you set, plus fees. Limit and stop orders, stop losses and take profits trigger on later alert prices. Nothing here ever reaches a broker."
        actions={<Button asChild size="sm" variant="outline"><Link href="/broker-accounts">Add a paper account</Link></Button>}
      />
      <div className="grid gap-3 md:grid-cols-3">
        {paper.map((a) => {
          const pnl = Number(a.equity ?? 0) - Number(a.settings.starting_balance ?? 0);
          return (
            <Card key={a.id} size="sm">
              <CardHeader>
                <CardTitle>{a.label}</CardTitle>
                <CardDescription>Started with {money(a.settings.starting_balance, a.currency)}</CardDescription>
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-2 text-sm">
                <div><p className="text-xs text-muted-foreground">Equity</p><p className="font-mono tabular-nums">{money(a.equity)}</p></div>
                <div><p className="text-xs text-muted-foreground">Since start</p><p className={`font-mono tabular-nums ${pnlClass(pnl)}`}>{money(pnl)}</p></div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      <section>
        <h3 className="mb-2 text-sm font-medium">Simulated positions</h3>
        <PositionsTable paper />
      </section>
      <OrdersTable paper title="Simulated orders" description="Paper orders only." />
    </div>
  );
}
