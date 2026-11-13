"use client";

import { Search } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { AdminGate } from "@/components/admin-gate";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AuditEntry } from "@/lib/types";

const PAGE_SIZE = 50;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function shortId(id: string | null) {
  return id ? `${id.slice(0, 8)}…` : "—";
}

function AuditTable() {
  const { me } = useSession();
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [filters, setFilters] = useState({ action: "", user_id: "" });
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load(current: typeof filters, beforeId?: number) {
    setBusy(true);
    try {
      const page = await api<AuditEntry[]>("/admin/audit", {
        query: { ...current, limit: PAGE_SIZE, before_id: beforeId },
      });
      setEntries((existing) => (beforeId && existing ? [...existing, ...page] : page));
      setHasMore(page.length === PAGE_SIZE);
    } catch (err) {
      toast.error(errorMessage(err));
      setEntries((existing) => existing ?? []);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load({ action: "", user_id: "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = {
      action: String(form.get("action") ?? "").trim(),
      user_id: String(form.get("user_id") ?? "").trim(),
    };
    if (next.user_id && !UUID.test(next.user_id)) {
      toast.error("User id must be the full id shown on the user's account.");
      return;
    }
    setFilters(next);
    void load(next);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Audit log</CardTitle>
        <CardDescription>
          Sign-ins, security changes and every staff action. Entries cannot be edited or deleted by anyone.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <div className="grid min-w-40 flex-1 gap-1.5">
            <Label htmlFor="action">Action starts with</Label>
            <Input id="action" name="action" placeholder="admin." autoComplete="off" />
          </div>
          <div className="grid min-w-56 flex-1 gap-1.5">
            <Label htmlFor="user_id">User id</Label>
            <Input id="user_id" name="user_id" placeholder="00000000-0000-…" autoComplete="off" className="font-mono text-xs" />
          </div>
          <Button type="submit" variant="outline" disabled={busy}>
            <Search aria-hidden /> Filter
          </Button>
        </form>

        {entries === null ? (
          <Skeleton className="h-64 w-full" />
        ) : entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No entries match.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Account</TableHead>
                <TableHead className="hidden md:table-cell">Done by</TableHead>
                <TableHead className="hidden lg:table-cell">IP address</TableHead>
                <TableHead className="hidden lg:table-cell">Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => (
                <TableRow key={entry.id}>
                  <TableCell className="tabular-nums text-muted-foreground">
                    {formatDateTime(entry.created_at, me.user.timezone)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{entry.action}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground" title={entry.user_id ?? undefined}>
                    {shortId(entry.user_id)}
                  </TableCell>
                  <TableCell className="hidden text-xs text-muted-foreground md:table-cell" title={entry.actor_user_id ?? undefined}>
                    {entry.actor_user_id
                      ? entry.actor_user_id === entry.user_id
                        ? "the user"
                        : `${entry.actor_role ?? "staff"} ${shortId(entry.actor_user_id)}`
                      : "system"}
                  </TableCell>
                  <TableCell className="hidden font-mono text-xs text-muted-foreground lg:table-cell">{entry.ip ?? "—"}</TableCell>
                  <TableCell className="hidden max-w-64 truncate font-mono text-xs text-muted-foreground lg:table-cell">
                    {Object.keys(entry.detail).length ? JSON.stringify(entry.detail) : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {hasMore && entries ? (
          <Button
            variant="outline"
            className="justify-self-start"
            disabled={busy}
            onClick={() => void load(filters, entries[entries.length - 1].id)}
          >
            Load older entries
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function AdminAudit() {
  return (
    <AdminGate>
      <AuditTable />
    </AdminGate>
  );
}
