import {
  BarChart3,
  BookOpen,
  CandlestickChart,
  CreditCard,
  FlaskConical,
  History,
  LayoutDashboard,
  LifeBuoy,
  ListOrdered,
  type LucideIcon,
  Radio,
  ScrollText,
  Settings,
  ShieldAlert,
  Users,
  Wallet,
  Webhook,
  Workflow,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  group: "Trading" | "Control" | "Account" | "Admin";
  /** Set while the section is not built. Describes what it will do and when. */
  planned?: { phase: number; summary: string; will: string[] };
};

export const NAV_ITEMS: NavItem[] = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard, group: "Trading" },
  {
    href: "/broker-accounts",
    label: "Broker Accounts",
    icon: Wallet,
    group: "Trading",
    planned: {
      phase: 4,
      summary: "Connect Zerodha, MT5 and MT4 accounts, each with its own execution mode and limits.",
      will: [
        "Connect a Zerodha account through Kite Connect's own login",
        "Pair an MT4 or MT5 terminal using the bridge Expert Advisor, without sharing the broker password",
        "Show balance, margin, connection health and the last heartbeat per account",
        "Pause an account or trip its kill switch",
      ],
    },
  },
  {
    href: "/strategies",
    label: "Strategies",
    icon: Workflow,
    group: "Trading",
    planned: {
      phase: 2,
      summary: "Define what a TradingView alert should do, on which accounts, and under which rules.",
      will: [
        "Create strategies with default size, stop loss, take profit and schedule",
        "Choose paper, semi-automatic or fully automatic execution",
        "Pause, resume and edit, with a version history of every change",
      ],
    },
  },
  {
    href: "/webhooks",
    label: "Webhook Connections",
    icon: Webhook,
    group: "Trading",
    planned: {
      phase: 2,
      summary: "Private webhook URLs for TradingView alerts, one or more per strategy.",
      will: [
        "Create, disable, rotate and revoke webhook credentials",
        "Send a test alert and see how it would be validated",
        "Copyable alert message examples for TradingView",
      ],
    },
  },
  {
    href: "/signals",
    label: "Signal Logs",
    icon: Radio,
    group: "Trading",
    planned: {
      phase: 2,
      summary: "Every alert received, whether it was accepted, and exactly why if it was not.",
      will: [
        "Incoming alerts with validation result and reason",
        "Duplicate, stale and replayed alerts marked as such",
        "The orders each accepted signal produced, per account",
      ],
    },
  },
  {
    href: "/orders",
    label: "Live Orders",
    icon: ListOrdered,
    group: "Trading",
    planned: {
      phase: 3,
      summary: "Orders moving through submission, acceptance and fill, updated in real time.",
      will: [
        "Each order's state, from queued to filled, rejected or unknown",
        "Broker order ids and fill evidence",
        "Approve or reject orders waiting in semi-automatic mode",
      ],
    },
  },
  {
    href: "/positions",
    label: "Open Positions",
    icon: CandlestickChart,
    group: "Trading",
    planned: {
      phase: 5,
      summary: "Positions per account, reconciled against what the broker reports.",
      will: ["Quantity, average price and unrealised P&L", "Differences between our records and the broker's", "Close a position or all positions on an account"],
    },
  },
  {
    href: "/trades",
    label: "Trade History",
    icon: History,
    group: "Trading",
    planned: {
      phase: 5,
      summary: "Completed trades with their fills, costs and realised P&L.",
      will: ["Filter by account, strategy, instrument and date", "Export to CSV", "Paper and live trades always shown separately"],
    },
  },
  {
    href: "/paper-trading",
    label: "Paper Trading",
    icon: FlaskConical,
    group: "Trading",
    planned: {
      phase: 3,
      summary: "A simulated account that runs your strategies without sending real orders.",
      will: [
        "Simulated market, limit and stop orders with stop loss and take profit",
        "Configurable fees and slippage",
        "Realised and unrealised P&L, clearly labelled as simulated",
      ],
    },
  },
  {
    href: "/risk",
    label: "Risk Management",
    icon: ShieldAlert,
    group: "Control",
    planned: {
      phase: 5,
      summary: "Limits checked before any live order is sent, and the switches that stop trading.",
      will: [
        "Maximum order size, open positions, daily loss and daily trades",
        "Allowed instruments and trading sessions",
        "Kill switches for everything, one strategy or one account",
        "Every rejected order recorded with the exact rule that stopped it",
      ],
    },
  },
  {
    href: "/analytics",
    label: "Analytics",
    icon: BarChart3,
    group: "Control",
    planned: {
      phase: 6,
      summary: "Performance and execution quality, computed only from recorded fills.",
      will: ["P&L by day, week and month", "Win rate, profit factor and drawdown", "Execution latency and rejected-order analysis"],
    },
  },
  {
    href: "/api-docs",
    label: "API Documentation",
    icon: BookOpen,
    group: "Account",
    planned: {
      phase: 2,
      summary: "The alert message format and how to set up alerts in TradingView.",
      will: ["The signal fields and every supported action", "Copyable Pine Script alert examples", "How authentication and duplicate protection work"],
    },
  },
  {
    href: "/billing",
    label: "Billing",
    icon: CreditCard,
    group: "Account",
    planned: {
      phase: 6,
      summary: "Plans, invoices and payment method.",
      will: ["Free paper-trading plan and paid plans", "Upgrade, renew and cancel", "What happens to open positions if a plan expires"],
    },
  },
  { href: "/settings", label: "Settings", icon: Settings, group: "Account" },
  {
    href: "/support",
    label: "Support",
    icon: LifeBuoy,
    group: "Account",
    planned: {
      phase: 6,
      summary: "Contact support and follow your tickets.",
      will: ["Open a ticket with the relevant order or signal attached", "See replies and status"],
    },
  },
  { href: "/admin/users", label: "Users", icon: Users, group: "Admin" },
  { href: "/admin/audit", label: "Audit Log", icon: ScrollText, group: "Admin" },
];

export const NAV_GROUPS: NavItem["group"][] = ["Trading", "Control", "Account", "Admin"];

export function findNavItem(pathname: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
}

