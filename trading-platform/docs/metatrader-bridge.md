# MetaTrader 4 and 5: installing the bridge Expert Advisor

MetaTrader has no cloud API: orders can only be placed from inside a terminal.
The platform therefore never logs in to your MetaTrader account and never sees
its password. Instead, a small Expert Advisor (EA) runs in your own terminal,
asks the platform for work over HTTPS, checks each instruction against the
terminal's real symbol rules, executes it, and reports exactly what the
terminal answered.

**Status: the EAs are written but have not been compiled or run against a
terminal yet** (no MetaTrader is available in the build environment). Test them
on a demo account first, using the checklist at the end.

| File | For |
|---|---|
| `bridge/mt5/TradeBridgeEA.mq5` | MetaTrader 5 |
| `bridge/mt4/TradeBridgeEA.mq4` | MetaTrader 4 |

## Install

1. In the dashboard, **Broker Accounts → Add account → MetaTrader 5** (or 4).
   If your broker adds a suffix to symbols (for example `XAUUSDm`), enter it.
2. Press **Pair terminal**. The device token is shown **once**. Copy it.
   Pairing again issues a new token and the old one stops working.
3. In MetaTrader: **File → Open Data Folder → MQL5/Experts** (or `MQL4/Experts`),
   copy the `.mq5` / `.mq4` file there, and compile it in MetaEditor (F7).
4. **Tools → Options → Expert Advisors**: tick *Allow algorithmic trading* and
   *Allow WebRequest for listed URL*, and add your platform address, for
   example `https://app.example.com`.
5. Drag the EA onto any chart. In its inputs set:
   - `ApiUrl`: `https://app.example.com/api/v1/bridge`
   - `DeviceToken`: the token from step 2
   - `MaxVolumePerOrder`: optional local safety cap in lots
6. The account in the dashboard turns *connected* within a few seconds.

The terminal must stay running for orders to execute. A Windows VPS close to
your broker's server is the usual choice. If the terminal is silent for more
than two minutes, new orders for that account are refused with the reason
"the terminal has not been heard from", and orders already queued expire
rather than being traded late.

## What the EA does

Every second it sends a heartbeat (balance, equity, free margin, and its open
positions for reconciliation) and receives one of:

| Instruction | The EA |
|---|---|
| `order` with intent `open` | Checks the symbol exists, the volume is within min/max and a multiple of the step, prices are on the tick size; then sends a market or pending order with the platform's magic number and the instruction id as the comment. |
| `order` with intent `close` | Closes this platform's positions (matched by magic number) on the symbol, up to the volume given, oldest first. |
| `modify_sl` / `modify_tp` | Changes the stop loss or take profit of this platform's positions on the symbol. |
| `cancel` | Deletes this platform's pending orders on the symbol. |
| `lookup` | An earlier result was lost. Searches the terminal's history by instruction id and reports what happened, or `not_found`. |
| `idle` | Nothing. |

It reports `filled` (with deal, price and volume), `placed` (pending order
accepted), `rejected` (a definite refusal, with the terminal's reason),
`error` (no definite answer: the platform will ask for a lookup), `applied`,
`cancelled` or `not_found`.

It never changes an instruction. A volume that is not a multiple of the
symbol's step is rejected, not rounded.

## Differences from Zerodha

- Stop loss and take profit attach to the position.
- On hedging accounts, BUY while short opens a separate long position, as the
  terminal itself would. CLOSE closes this platform's positions on the symbol.
- MetaTrader 4 has no stop-limit orders; such instructions are rejected.
- Brokers sometimes replace order comments. The lookup also matches deals by
  their order's comment; if a broker overwrites both, a lost result cannot be
  matched and the order is marked failed after the check. Pick a broker that
  keeps comments, and test with the checklist below.

## Demo-account checklist

Run each on a demo account and confirm the dashboard shows the right state.

1. Pair; the account shows *connected* and the balance matches the terminal.
2. A BUY market alert fills; the order shows the deal, price and volume.
3. A BUY with stop loss and take profit; the position in the terminal has both.
4. MODIFY_SL changes the stop in the terminal.
5. A LIMIT alert creates a pending order; it fills when price reaches it and the
   dashboard moves from *submitted* to *filled*.
6. CANCEL_ORDER removes the pending order.
7. CLOSE with a quantity closes part of the position; CLOSE without one closes the rest.
8. A volume below the symbol's minimum is rejected with the reason.
9. With the market closed, an alert is rejected with the terminal's reason.
10. Remove the EA from the chart, send an alert, wait two minutes: the order
    expires instead of trading late.
11. While an order is executing, close the terminal: on restart the platform asks
    for a lookup and the order ends *filled* or *failed*, never duplicated.
