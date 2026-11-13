"use client";

import { Hammer } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NAV_ITEMS } from "@/lib/nav";

/**
 * Shown for sections that are not built yet. It states plainly that nothing
 * here works, and what the section will do; it never shows sample data.
 */
export function PlannedSection({ href }: { href: string }) {
  const item = NAV_ITEMS.find((entry) => entry.href === href);
  if (!item?.planned) return null;
  const Icon = item.icon;
  return (
    <Card className="mx-auto max-w-2xl">
      <CardHeader>
        <div className="mb-2 flex items-center gap-3">
          <span className="grid size-9 place-items-center rounded-md bg-muted">
            <Icon className="size-4.5 text-muted-foreground" aria-hidden />
          </span>
          <span className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-0.5 text-xs text-muted-foreground">
            <Hammer className="size-3" aria-hidden /> Not built yet
          </span>
        </div>
        <CardTitle>{item.label}</CardTitle>
        <CardDescription>{item.planned.summary}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">What it will do</p>
          <ul className="grid gap-1.5 text-sm">
            {item.planned.will.map((line) => (
              <li key={line} className="flex gap-2">
                <span aria-hidden className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground" />
                {line}
              </li>
            ))}
          </ul>
        </div>
        <p className="border-t border-border pt-3 text-xs text-muted-foreground">
          Scheduled for phase {item.planned.phase} of the build. Nothing on this page is functional and no data is
          shown until it is.
        </p>
      </CardContent>
    </Card>
  );
}
