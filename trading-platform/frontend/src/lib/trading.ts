/** Types and helpers for the trading screens. Numbers from the API arrive as JSON numbers or strings. */

export type Num = number | string | null;

export type Broker = "paper" | "kite" | "mt5" | "mt4";

export type BrokerAccount = {
  id: string;
  broker: Broker;
  label: string;
  status: "active" | "paused" | "archived";
  kill_switch: boolean;
  kill_switch_reason: string | null;
  settings: Record<string, string | number>;
  connection_status: "not_connected" | "connected" | "login_required" | "error";
  connection_message: string | null;
  last_heartbeat_at: string | null;
  terminal_online: boolean;
  currency: string | null;
  balance: Num;
  equity: Num;
  margin_available: Num;
  snapshot_at: string | null;
  consecutive_failures: number;
  circuit_open: boolean;
  is_paper: boolean;
  has_api_secret: boolean;
  kite_session_expires_at: string | null;
  bridge_paired: boolean;
  has_open_positions: boolean;
  created_at: string;
};

export type InstrumentMapping = {
  id: string;
  alert_symbol: string;
  broker_symbol: string;
  exchange: string | null;
  product: string | null;
  lot_size: Num;
  quantity_step: Num;
  min_quantity: Num;
  max_quantity: Num;
  tick_size: Num;
  contract_size: Num;
};

export type StrategyLink = {
  broker_account_id: string;
  label: string;
  broker: Broker;
  account_status: string;
  enabled: boolean;
  approval_required: boolean;
  sizing_mode: "signal" | "fixed" | "percent_capital" | "risk_per_trade";
  sizing_value: Num;
  execution_mode: "paper" | "semi_automatic" | "automatic";
};

export type Strategy = {
  id: string;
  code: string;
  name: string;
  description: string;
  status: "active" | "paused" | "archived";
  kill_switch: boolean;
  default_order_type: "MARKET" | "LIMIT";
  default_stop_loss_pct: Num;
  default_take_profit_pct: Num;
  action_aliases: Record<string, string>;
  version: number;
  accounts: StrategyLink[];
  webhook_count: number;
};

export type Webhook = {
  id: string;
  name: string;
  strategy_id: string;
  strategy_code: string | null;
  strategy_name: string | null;
  url_hint: string;
  has_passphrase: boolean;
  requires_signature: boolean;
  enabled: boolean;
  revoked: boolean;
  last_received_at: string | null;
  created_at: string;
};

export type Order = {
  id: string;
  client_order_id: string;
  signal_id: string | null;
  source: "signal" | "manual" | "protective";
  account_id: string;
  account_label: string | null;
  broker: Broker | null;
  strategy_code: string | null;
  is_paper: boolean;
  intent: string;
  side: "BUY" | "SELL" | null;
  symbol: string;
  broker_symbol: string;
  order_type: string;
  quantity: Num;
  price: Num;
  trigger_price: Num;
  stop_loss: Num;
  take_profit: Num;
  reference_price: Num;
  state: string;
  reason: string | null;
  rule: string | null;
  broker_order_id: string | null;
  filled_quantity: Num;
  average_price: Num;
  approval_expires_at: string | null;
  created_at: string;
  submitted_at: string | null;
  completed_at: string | null;
  latency_ms: number | null;
};

export type OrderDetail = Order & {
  events: { at: string; from: string | null; to: string; message: string | null }[];
  fills: { at: string; quantity: Num; price: Num; fee: Num; trade_id: string; realized_pnl: Num }[];
};

export type SignalRow = {
  id: string;
  client_signal_id: string;
  webhook_name: string | null;
  received_at: string;
  signal_time: string | null;
  action: string | null;
  symbol: string | null;
  status: "received" | "processed" | "ignored" | "rejected";
  reason: string | null;
  duplicate_count: number;
  order_count: number;
  payload: Record<string, unknown>;
};

export type PositionRow = {
  id: string;
  account_id: string;
  account_label: string | null;
  broker: Broker | null;
  symbol: string;
  quantity: Num;
  average_price: Num;
  last_price: Num;
  unrealized_pnl: Num;
  realized_pnl: Num;
  stop_loss: Num;
  take_profit: Num;
  is_paper: boolean;
  broker_quantity: Num;
  mismatch: boolean;
};

export type TradeRow = {
  id: string;
  order_id: string;
  account_label: string | null;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: Num;
  price: Num;
  fee: Num;
  realized_pnl: Num;
  is_paper: boolean;
  trade_id: string;
  filled_at: string;
};

export const BROKER_LABELS: Record<Broker, string> = {
  paper: "Paper (simulated)",
  kite: "Zerodha Kite",
  mt5: "MetaTrader 5",
  mt4: "MetaTrader 4",
};

export function num(value: Num | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Up to `max` decimals, trailing zeros dropped. */
export function fmt(value: Num | undefined, max = 4): string {
  const n = num(value);
  if (n === null) return "—";
  return n.toLocaleString("en-IN", { maximumFractionDigits: max });
}

export function money(value: Num | undefined, currency?: string | null): string {
  const n = num(value);
  if (n === null) return "—";
  const text = n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${text} ${currency}` : text;
}

export function pnlClass(value: Num | undefined): string {
  const n = num(value);
  if (n === null || n === 0) return "text-muted-foreground";
  return n > 0 ? "text-positive" : "text-negative";
}

export const OPEN_STATES = new Set(["AWAITING_APPROVAL", "QUEUED", "EXECUTING", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN"]);

export function stateTone(state: string): "good" | "bad" | "wait" | "neutral" {
  if (state === "FILLED" || state === "APPLIED" || state === "processed") return "good";
  if (["REJECTED", "FAILED", "rejected"].includes(state)) return "bad";
  if (["UNKNOWN", "AWAITING_APPROVAL", "PARTIALLY_FILLED", "EXECUTING", "QUEUED", "SUBMITTED", "received"].includes(state))
    return "wait";
  return "neutral";
}

export const RULE_LABELS: Record<string, string> = {
  platform_halted: "Platform halt",
  global_kill_switch: "Your kill switch",
  account_paused: "Account paused",
  account_kill_switch: "Account kill switch",
  strategy_kill_switch: "Strategy kill switch",
  circuit_breaker: "Circuit breaker",
  live_trading_disabled: "Live trading off",
  paper_trading_disabled: "Paper trading off",
  broker_not_connected: "Broker not connected",
  signal_too_old: "Signal too old",
  market_closed: "Market closed",
  outside_trading_session: "Outside session",
  symbol_not_allowed: "Instrument not allowed",
  max_order_quantity: "Max order quantity",
  max_order_lots: "Max lots",
  max_open_positions: "Max open positions",
  max_daily_trades: "Max daily trades",
  daily_loss_limit: "Daily loss lock",
  max_exposure: "Max exposure",
  min_available_margin: "Minimum margin",
  instrument_unmapped: "No instrument mapping",
  invalid_order: "Invalid size or price",
  not_supported: "Not supported by broker",
  broker_rejected: "Broker refused",
  user_rejected: "Rejected by you",
  internal_error: "Internal error",
};
