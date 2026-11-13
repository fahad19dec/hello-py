"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { describeDevice, formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { SessionInfo } from "@/lib/types";

export function SessionsTab() {
  const { me } = useSession();
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<SessionInfo[]>("/auth/sessions")
      .then(setSessions)
      .catch((err) => toast.error(errorMessage(err)));
  }, []);

  useEffect(load, [load]);

  async function end(id: string) {
    setBusy(true);
    try {
      await api(`/auth/sessions/${id}`, { method: "DELETE" });
      toast.success("Session ended");
      load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function endOthers() {
    setBusy(true);
    try {
      const { count } = await api<{ count: number }>("/auth/sessions/revoke-others", { method: "POST" });
      toast.success(count === 1 ? "Signed out 1 other device" : `Signed out ${count} other devices`);
      load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const others = sessions?.filter((s) => !s.current).length ?? 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Signed-in devices</CardTitle>
        <CardDescription>End any session you do not recognise, then change your password.</CardDescription>
        <CardAction>
          <Button variant="outline" onClick={endOthers} disabled={busy || others === 0}>
            Sign out all other devices
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {sessions === null ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Device</TableHead>
                <TableHead className="hidden sm:table-cell">IP address</TableHead>
                <TableHead>Last active</TableHead>
                <TableHead className="hidden md:table-cell">Signed in</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessions.map((session) => (
                <TableRow key={session.id}>
                  <TableCell>
                    <span className="inline-flex items-center gap-2">
                      {describeDevice(session.user_agent)}
                      {session.current ? <Badge variant="secondary">This device</Badge> : null}
                    </span>
                  </TableCell>
                  <TableCell className="hidden font-mono text-xs text-muted-foreground sm:table-cell">
                    {session.ip ?? "—"}
                  </TableCell>
                  <TableCell className="tabular-nums text-muted-foreground">
                    {formatDateTime(session.last_seen_at, me.user.timezone)}
                  </TableCell>
                  <TableCell className="hidden tabular-nums text-muted-foreground md:table-cell">
                    {formatDateTime(session.created_at, me.user.timezone)}
                  </TableCell>
                  <TableCell className="text-right">
                    {session.current ? null : (
                      <Button variant="destructive" size="sm" onClick={() => end(session.id)} disabled={busy}>
                        End session
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
