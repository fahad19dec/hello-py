"use client";

import { PageHeader } from "@/components/trading-ui";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const FIELDS: [string, string, string][] = [
  ["action", "required", "BUY, SELL, CLOSE, CLOSE_LONG, CLOSE_SHORT, CLOSE_ALL, MODIFY_SL, MODIFY_TP, CANCEL_ORDER, or an alias set on the strategy (LONG, SHORT, EXIT_LONG, EXIT_SHORT by default)."],
  ["symbol", "required (except CLOSE_ALL)", "TradingView symbol. An exchange prefix like NSE: is dropped. Mapped to the broker's instrument per account."],
  ["timestamp", "required", "When the alert fired: {{timenow}}, ISO 8601 with a time zone, or Unix seconds or milliseconds. Older than the age limit (60 s by default) is rejected."],
  ["signal_id", "recommended", "Unique per alert. A repeat is acknowledged and never executed again. Without it, the hash of the exact message is used."],
  ["strategy_id", "optional", "If present, must equal the strategy's ID, or the alert is rejected."],
  ["quantity", "depends", "Size when the account sizes orders from the alert: shares or contracts for Zerodha and paper, lots for MetaTrader. Must already be a valid multiple of the lot or step."],
  ["order_type", "optional", "MARKET (default), LIMIT, STOP or STOP_LIMIT."],
  ["price", "depends", "LIMIT and STOP_LIMIT: the limit price. MARKET and STOP: the current price ({{close}}), used for paper fills, sizing and exposure."],
  ["trigger_price", "for STOP", "Trigger price of STOP and STOP_LIMIT orders."],
  ["stop_loss / take_profit", "optional", "Prices. Attached on MetaTrader and paper; Zerodha refuses attached stops (send an exit alert instead). Required for MODIFY_SL / MODIFY_TP."],
  ["passphrase", "if set", "Required when the webhook has a passphrase. Removed before the alert is stored."],
];

const STRATEGY_EXAMPLE = `{
  "strategy_id": "NIFTY_BREAKOUT",
  "signal_id": "{{strategy.order.id}}-{{timenow}}",
  "symbol": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "order_type": "MARKET",
  "quantity": "{{strategy.order.contracts}}",
  "price": "{{close}}",
  "timestamp": "{{timenow}}",
  "timeframe": "{{interval}}"
}`;

const PINE_EXAMPLE = `//@version=6
strategy("Breakout", overlay = true)
longCondition = ta.crossover(close, ta.highest(high, 20)[1])
exitCondition = ta.crossunder(close, ta.sma(close, 20))

if longCondition
    strategy.entry("Long", strategy.long, qty = 75,
         alert_message = '{"action":"BUY","symbol":"' + syminfo.ticker + '","quantity":75,"price":' + str.tostring(close) + ',"stop_loss":' + str.tostring(math.round_to_mintick(low)) + ',"timestamp":"{{timenow}}","signal_id":"long-{{timenow}}"}')
if exitCondition
    strategy.close("Long",
         alert_message = '{"action":"CLOSE","symbol":"' + syminfo.ticker + '","price":' + str.tostring(close) + ',"timestamp":"{{timenow}}","signal_id":"exit-{{timenow}}"}')

// In the alert dialog: Message = {{strategy.order.alert_message}}`;

const INDICATOR_EXAMPLE = `{"action": "SELL", "symbol": "{{ticker}}", "quantity": 0.10, "price": {{close}},
 "stop_loss": 2665.5, "take_profit": 2620, "timestamp": "{{timenow}}", "signal_id": "gold-short-{{timenow}}"}`;

function Code({ children }: { children: string }) {
  return <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-xs leading-relaxed">{children}</pre>;
}

export function ApiDocs() {
  return (
    <div className="grid gap-5">
      <PageHeader title="Alert message format" description="What to put in a TradingView alert's Message box. The Webhook URL comes from the Webhook connections page." />
      <Card>
        <CardHeader>
          <CardTitle>Fields</CardTitle>
          <CardDescription>Unknown fields are ignored. Numbers may be sent as numbers or as text.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow><TableHead>Field</TableHead><TableHead>Needed</TableHead><TableHead>Meaning</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {FIELDS.map(([name, need, meaning]) => (
                <TableRow key={name}>
                  <TableCell className="whitespace-nowrap font-mono text-xs">{name}</TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{need}</TableCell>
                  <TableCell className="whitespace-normal text-sm">{meaning}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>From a TradingView strategy</CardTitle>
          <CardDescription>One alert covers every entry and exit: TradingView fills in the order&apos;s action and size.</CardDescription>
        </CardHeader>
        <CardContent><Code>{STRATEGY_EXAMPLE}</Code></CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Pine Script with your own messages</CardTitle>
          <CardDescription>Build the message in the script, then set the alert&apos;s Message to {"{{strategy.order.alert_message}}"}.</CardDescription>
        </CardHeader>
        <CardContent><Code>{PINE_EXAMPLE}</Code></CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>From an indicator alert</CardTitle>
          <CardDescription>A fixed message per alert condition.</CardDescription>
        </CardHeader>
        <CardContent><Code>{INDICATOR_EXAMPLE}</Code></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Responses</CardTitle></CardHeader>
        <CardContent className="grid gap-1 text-sm text-muted-foreground">
          <p><span className="font-mono text-foreground">202 accepted</span>: stored; orders are prepared within about a second.</p>
          <p><span className="font-mono text-foreground">200 duplicate</span>: this signal_id was already accepted; nothing new happens.</p>
          <p><span className="font-mono text-foreground">400 / 422 rejected</span>: malformed, unknown action, stale or from the future; the reason is in the Signal log.</p>
          <p><span className="font-mono text-foreground">401</span> wrong passphrase or signature · <span className="font-mono text-foreground">403</span> webhook disabled · <span className="font-mono text-foreground">404</span> unknown or revoked URL.</p>
          <p>Risk checks run per account after intake, so an accepted alert can still produce rejected orders; each shows its rule on the Orders page.</p>
        </CardContent>
      </Card>
    </div>
  );
}
