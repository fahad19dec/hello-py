"use client";

import { useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Field, FormError } from "@/components/form-bits";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import { ROLE_LABELS } from "@/lib/types";

export const selectClass =
  "h-8 w-full rounded-lg border border-input bg-input/30 px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";

export function ProfileTab() {
  const { me, refresh } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const timezones = useMemo(() => {
    // Browsers list some zones under older names (India as "Asia/Calcutta"),
    // so the current names people look for are added explicitly.
    const supported = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
    const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return Array.from(new Set(["UTC", "Asia/Kolkata", local, me.user.timezone, ...supported]));
  }, [me.user.timezone]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      await api("/users/me", {
        method: "PATCH",
        body: { display_name: form.get("display_name"), timezone: form.get("timezone") },
      });
      await refresh();
      toast.success("Profile saved");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="max-w-xl">
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>
          {me.user.email} · {ROLE_LABELS[me.user.role]}
        </CardDescription>
      </CardHeader>
      <form onSubmit={submit}>
        <CardContent className="grid gap-4">
          <FormError message={error} />
          <Field id="display_name" label="Name" defaultValue={me.user.display_name} maxLength={100} autoComplete="name" />
          <div className="grid gap-1.5">
            <Label htmlFor="timezone">Time zone</Label>
            <select id="timezone" name="timezone" defaultValue={me.user.timezone} className={selectClass}>
              {timezones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">Times across the dashboard are shown in this zone.</p>
          </div>
        </CardContent>
        <CardFooter className="mt-4">
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save changes"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
