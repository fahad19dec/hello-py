"use client";

import { ChevronsUpDown, LogOut, Menu, Settings, ShieldCheck, ShieldOff } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Brand } from "@/components/brand";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { findNavItem, NAV_GROUPS, NAV_ITEMS } from "@/lib/nav";
import { useSession } from "@/lib/session";
import { ROLE_LABELS, type SystemStatus } from "@/lib/types";

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { me } = useSession();
  const isStaff = me.user.role !== "user";

  return (
    <nav aria-label="Main" className="flex flex-col gap-5 px-3 py-4">
      {NAV_GROUPS.filter((group) => group !== "Admin" || isStaff).map((group) => (
        <div key={group}>
          <p className="px-2 pb-1.5 text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">{group}</p>
          <ul className="flex flex-col gap-0.5">
            {NAV_ITEMS.filter((item) => item.group === group).map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors ${
                      active
                        ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                        : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                    }`}
                  >
                    <item.icon className={`size-4 shrink-0 ${active ? "text-primary" : "text-muted-foreground"}`} aria-hidden />
                    <span className="truncate">{item.label}</span>
                    {item.planned ? (
                      <span className="ml-auto shrink-0 text-[0.62rem] uppercase tracking-wide text-muted-foreground/70">
                        Soon
                      </span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function TradingModeBadges({ status }: { status: SystemStatus | null }) {
  if (!status) return null;
  return (
    <div className="hidden items-center gap-2 sm:flex">
      {status.live_trading_enabled ? (
        <Badge variant="outline" className="gap-1 border-warning/40 text-warning">
          <ShieldCheck className="size-3" aria-hidden /> Live trading enabled
        </Badge>
      ) : (
        <Badge variant="outline" className="gap-1 text-muted-foreground" title="No real orders can be sent">
          <ShieldOff className="size-3" aria-hidden /> Live trading off
        </Badge>
      )}
    </div>
  );
}

function UserMenu() {
  const { me, signOut } = useSession();
  const name = me.user.display_name || me.user.email;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="max-w-52 gap-2" aria-label="Account menu">
          <span className="grid size-6 shrink-0 place-items-center rounded-full bg-secondary text-xs font-medium uppercase">
            {name.slice(0, 1)}
          </span>
          <span className="hidden truncate sm:inline">{name}</span>
          <ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel className="grid gap-0.5">
          <span className="truncate text-sm font-medium text-foreground">{me.user.email}</span>
          <span className="text-xs font-normal text-muted-foreground">{ROLE_LABELS[me.user.role]}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/settings">
            <Settings aria-hidden /> Settings
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void signOut()}>
          <LogOut aria-hidden /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const current = findNavItem(pathname);

  useEffect(() => {
    api<SystemStatus>("/system/status")
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col overflow-y-auto border-r border-sidebar-border bg-sidebar lg:flex">
        <div className="flex h-14 shrink-0 items-center border-b border-sidebar-border px-5">
          <Brand />
        </div>
        <Navigation />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-background/90 px-4 backdrop-blur lg:px-6">
          <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu">
                <Menu aria-hidden />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-64 gap-0 overflow-y-auto bg-sidebar p-0">
              <SheetHeader className="h-14 justify-center border-b border-sidebar-border px-5 py-0">
                <SheetTitle>
                  <Brand />
                </SheetTitle>
                <SheetDescription className="sr-only">Main navigation</SheetDescription>
              </SheetHeader>
              <Navigation onNavigate={() => setMenuOpen(false)} />
            </SheetContent>
          </Sheet>
          <h1 className="truncate text-sm font-medium">{current?.label ?? "Dashboard"}</h1>
          <div className="ml-auto flex items-center gap-2">
            <TradingModeBadges status={status} />
            <UserMenu />
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 lg:px-6">{children}</main>
      </div>
    </div>
  );
}
