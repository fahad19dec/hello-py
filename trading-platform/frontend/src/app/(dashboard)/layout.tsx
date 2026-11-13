"use client";

import { AppShell } from "@/components/app-shell";
import { Skeleton } from "@/components/ui/skeleton";
import { SessionProvider } from "@/lib/session";

function LoadingShell() {
  return (
    <div className="flex min-h-screen" aria-busy="true" aria-label="Loading">
      <div className="hidden w-64 shrink-0 border-r border-sidebar-border bg-sidebar lg:block" />
      <div className="flex-1 space-y-4 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    </div>
  );
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider fallback={<LoadingShell />}>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
