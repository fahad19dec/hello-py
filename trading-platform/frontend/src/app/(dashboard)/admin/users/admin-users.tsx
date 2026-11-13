"use client";

import { MoreHorizontal, Search } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { AdminGate } from "@/components/admin-gate";
import { Field, FormError } from "@/components/form-bits";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { describeDevice, formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import { ROLE_LABELS, type AdminUser, type AdminUserDetail, type Role } from "@/lib/types";

import { selectClass } from "../../settings/profile-tab";

const PAGE_SIZE = 25;

type Action =
  | { name: "view"; detail: AdminUserDetail }
  | { name: "suspend"; user: AdminUser }
  | { name: "role"; user: AdminUser };

function UsersTable() {
  const { me } = useSession();
  const isSuperAdmin = me.user.role === "super_admin";
  const [page, setPage] = useState<{ items: AdminUser[]; total: number } | null>(null);
  const [filters, setFilters] = useState({ search: "", role: "", status: "" });
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<{ items: AdminUser[]; total: number }>("/admin/users", {
      query: { ...filters, limit: PAGE_SIZE, offset },
    })
      .then(setPage)
      .catch((err) => toast.error(errorMessage(err)));
  }, [filters, offset]);

  useEffect(load, [load]);

  function close() {
    setAction(null);
    setError(null);
  }

  /** A staff member may change a user who is not themselves and, for support, not staff. */
  function canChange(user: AdminUser) {
    return user.id !== me.user.id && (isSuperAdmin || user.role === "user");
  }

  async function perform(work: () => Promise<string>) {
    setBusy(true);
    setError(null);
    try {
      toast.success(await work());
      close();
      load();
    } catch (err) {
      setError(errorMessage(err));
      if (!action) toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function view(user: AdminUser) {
    try {
      setAction({ name: "view", detail: await api<AdminUserDetail>(`/admin/users/${user.id}`) });
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  function suspend(event: FormEvent<HTMLFormElement>, user: AdminUser) {
    event.preventDefault();
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "").trim();
    void perform(async () => {
      await api(`/admin/users/${user.id}/suspend`, { method: "POST", body: { reason } });
      return `${user.email} suspended and signed out`;
    });
  }

  function changeRole(event: FormEvent<HTMLFormElement>, user: AdminUser) {
    event.preventDefault();
    const role = String(new FormData(event.currentTarget).get("role")) as Role;
    void perform(async () => {
      await api(`/admin/users/${user.id}/role`, { method: "PUT", body: { role } });
      return `${user.email} is now ${ROLE_LABELS[role].toLowerCase()}`;
    });
  }

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setOffset(0);
    setFilters({
      search: String(form.get("search") ?? "").trim(),
      role: String(form.get("role") ?? ""),
      status: String(form.get("status") ?? ""),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Users</CardTitle>
        <CardDescription>
          Every change here is recorded in the audit log with your name. Passwords, two-factor secrets and broker
          credentials are never visible to staff.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <form onSubmit={submitFilters} className="flex flex-wrap items-end gap-2">
          <div className="grid min-w-48 flex-1 gap-1.5">
            <Label htmlFor="search">Email contains</Label>
            <Input id="search" name="search" placeholder="name@example.com" autoComplete="off" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="role">Role</Label>
            <select id="role" name="role" className={selectClass}>
              <option value="">Any</option>
              <option value="user">User</option>
              <option value="support_admin">Support admin</option>
              <option value="super_admin">Super admin</option>
            </select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="status">Status</Label>
            <select id="status" name="status" className={selectClass}>
              <option value="">Any</option>
              <option value="active">Active</option>
              <option value="suspended">Suspended</option>
            </select>
          </div>
          <Button type="submit" variant="outline">
            <Search aria-hidden /> Search
          </Button>
        </form>

        {page === null ? (
          <Skeleton className="h-48 w-full" />
        ) : page.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No users match.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="hidden md:table-cell">Two-factor</TableHead>
                <TableHead className="hidden lg:table-cell">Last sign-in</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.items.map((user) => (
                <TableRow key={user.id}>
                  <TableCell>
                    <div className="font-medium">{user.email}</div>
                    {user.display_name ? <div className="text-xs text-muted-foreground">{user.display_name}</div> : null}
                  </TableCell>
                  <TableCell>{ROLE_LABELS[user.role]}</TableCell>
                  <TableCell>
                    {user.status === "suspended" ? (
                      <Badge variant="destructive">Suspended</Badge>
                    ) : user.email_verified ? (
                      <Badge variant="secondary">Active</Badge>
                    ) : (
                      <Badge variant="outline">Unconfirmed</Badge>
                    )}
                  </TableCell>
                  <TableCell className="hidden text-muted-foreground md:table-cell">{user.mfa_enabled ? "On" : "Off"}</TableCell>
                  <TableCell className="hidden tabular-nums text-muted-foreground lg:table-cell">
                    {formatDateTime(user.last_login_at, me.user.timezone)}
                  </TableCell>
                  <TableCell className="text-right">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${user.email}`}>
                          <MoreHorizontal aria-hidden />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => void view(user)}>View account</DropdownMenuItem>
                        {canChange(user) ? (
                          <>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              onSelect={() =>
                                void perform(async () => {
                                  const { count } = await api<{ count: number }>(
                                    `/admin/users/${user.id}/revoke-sessions`,
                                    { method: "POST" },
                                  );
                                  return `Ended ${count} session${count === 1 ? "" : "s"} for ${user.email}`;
                                })
                              }
                            >
                              Sign out everywhere
                            </DropdownMenuItem>
                            {isSuperAdmin ? (
                              <DropdownMenuItem onSelect={() => setAction({ name: "role", user })}>Change role</DropdownMenuItem>
                            ) : null}
                            {user.status === "suspended" ? (
                              <DropdownMenuItem
                                onSelect={() =>
                                  void perform(async () => {
                                    await api(`/admin/users/${user.id}/unsuspend`, { method: "POST" });
                                    return `${user.email} reinstated`;
                                  })
                                }
                              >
                                Reinstate
                              </DropdownMenuItem>
                            ) : (
                              <DropdownMenuItem variant="destructive" onSelect={() => setAction({ name: "suspend", user })}>
                                Suspend
                              </DropdownMenuItem>
                            )}
                          </>
                        ) : null}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        {page && page.total > PAGE_SIZE ? (
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span className="tabular-nums">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= page.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>

      <Dialog open={action !== null} onOpenChange={(open) => (open ? null : close())}>
        <DialogContent>
          {action?.name === "view" ? (
            <div className="grid gap-4">
              <DialogHeader>
                <DialogTitle>{action.detail.user.email}</DialogTitle>
                <DialogDescription>Opening this account was recorded in the audit log.</DialogDescription>
              </DialogHeader>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
                <dt className="text-muted-foreground">Role</dt>
                <dd>{ROLE_LABELS[action.detail.user.role]}</dd>
                <dt className="text-muted-foreground">Status</dt>
                <dd>
                  {action.detail.user.status}
                  {action.detail.user.suspended_reason ? ` — ${action.detail.user.suspended_reason}` : ""}
                </dd>
                <dt className="text-muted-foreground">Email confirmed</dt>
                <dd>{action.detail.user.email_verified ? "Yes" : "No"}</dd>
                <dt className="text-muted-foreground">Two-factor</dt>
                <dd>{action.detail.user.mfa_enabled ? "On" : "Off"}</dd>
                <dt className="text-muted-foreground">Created</dt>
                <dd className="tabular-nums">{formatDateTime(action.detail.user.created_at, me.user.timezone)}</dd>
                <dt className="text-muted-foreground">User id</dt>
                <dd className="break-all font-mono text-xs">{action.detail.user.id}</dd>
              </dl>
              <div>
                <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Active sessions ({action.detail.sessions.length})
                </p>
                {action.detail.sessions.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Not signed in anywhere.</p>
                ) : (
                  <ul className="grid gap-1 text-sm">
                    {action.detail.sessions.map((session) => (
                      <li key={session.id} className="flex justify-between gap-3">
                        <span>{describeDevice(session.user_agent)}</span>
                        <span className="tabular-nums text-muted-foreground">
                          {formatDateTime(session.last_seen_at, me.user.timezone)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          ) : null}

          {action?.name === "suspend" ? (
            <form onSubmit={(event) => suspend(event, action.user)} className="grid gap-4">
              <DialogHeader>
                <DialogTitle>Suspend {action.user.email}</DialogTitle>
                <DialogDescription>
                  They are signed out immediately and cannot sign in until reinstated. The reason is stored in the audit log.
                </DialogDescription>
              </DialogHeader>
              <FormError message={error} />
              <Field id="reason" label="Reason" minLength={3} maxLength={500} autoFocus required />
              <DialogFooter>
                <Button type="submit" variant="destructive" disabled={busy}>
                  Suspend account
                </Button>
              </DialogFooter>
            </form>
          ) : null}

          {action?.name === "role" ? (
            <form onSubmit={(event) => changeRole(event, action.user)} className="grid gap-4">
              <DialogHeader>
                <DialogTitle>Change role for {action.user.email}</DialogTitle>
                <DialogDescription>They are signed out so the new role starts from a fresh sign-in.</DialogDescription>
              </DialogHeader>
              <FormError message={error} />
              <div className="grid gap-1.5">
                <Label htmlFor="new-role">Role</Label>
                <select id="new-role" name="role" defaultValue={action.user.role} className={selectClass}>
                  <option value="user">User</option>
                  <option value="support_admin">Support admin — manage regular users</option>
                  <option value="super_admin">Super admin — everything, including roles</option>
                </select>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={busy}>
                  Save role
                </Button>
              </DialogFooter>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export function AdminUsers() {
  return (
    <AdminGate>
      <UsersTable />
    </AdminGate>
  );
}
