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
  },
  {
    href: "/strategies",
    label: "Strategies",
    icon: Workflow,
    group: "Trading",
  },
  {
    href: "/webhooks",
    label: "Webhook Connections",
    icon: Webhook,
    group: "Trading",
  },
  {
    href: "/signals",
    label: "Signal Logs",
    icon: Radio,
    group: "Trading",
  },
  {
    href: "/orders",
    label: "Live Orders",
    icon: ListOrdered,
    group: "Trading",
  },
  {
    href: "/positions",
    label: "Open Positions",
    icon: CandlestickChart,
    group: "Trading",
  },
  {
    href: "/trades",
    label: "Trade History",
    icon: History,
    group: "Trading",
  },
  {
    href: "/paper-trading",
    label: "Paper Trading",
    icon: FlaskConical,
    group: "Trading",
  },
  {
    href: "/risk",
    label: "Risk Management",
    icon: ShieldAlert,
    group: "Control",
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

