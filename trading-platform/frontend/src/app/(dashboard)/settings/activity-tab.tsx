"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { describeAction, describeDevice } from "@/lib/format";
import { When } from "@/components/when";
import { useSession } from "@/lib/session";
import type { Activity } from "@/lib/types";

const PAGE_SIZE = 25;

export function ActivityTab() {
  const { me } = useSession();
  const [entries, setEntries] = useState<Activity[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load(beforeId?: number) {
    setBusy(true);
    try {
      const page = await api<Activity[]>("/users/me/activity", { query: { limit: PAGE_SIZE, before_id: beforeId } });
      setEntries((current) => (beforeId && current ? [...current, ...page] : page));
      setHasMore(page.length === PAGE_SIZE);
    } catch (err) {
      toast.error(errorMessage(err));
      setEntries((current) => current ?? []);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Account activity</CardTitle>
        <CardDescription>A permanent record of sign-ins and security changes. It cannot be edited or deleted.</CardDescription>
      </CardHeader>
      <CardContent>
        {entries === null ? (
          <Skeleton className="h-48 w-full" />
        ) : entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No activity recorded yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Event</TableHead>
                <TableHead>When</TableHead>
                <TableHead className="hidden sm:table-cell">By</TableHead>
                <TableHead className="hidden md:table-cell">Device</TableHead>
                <TableHead className="hidden md:table-cell">IP address</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => (
                <TableRow key={entry.id}>
                  <TableCell>{describeAction(entry.action)}</TableCell>
                  <TableCell className="tabular-nums text-muted-foreground">
                    <When iso={entry.created_at} timeZone={me.user.timezone} />
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground sm:table-cell">
                    {entry.actor === "you" ? "You" : entry.actor === "staff" ? "Support" : "—"}
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground md:table-cell">
                    {entry.actor === "staff" ? "—" : describeDevice(entry.user_agent)}
                  </TableCell>
                  <TableCell className="hidden font-mono text-xs text-muted-foreground md:table-cell">
                    {entry.ip ?? "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {hasMore && entries ? (
          <Button
            variant="outline"
            className="mt-4"
            disabled={busy}
            onClick={() => void load(entries[entries.length - 1].id)}
          >
            Load older activity
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
