"use client";

import { CircleCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Field, FormError } from "@/components/form-bits";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";

export function ResetPasswordForm() {
  const tokenFromUrl = useSearchParams().get("token");
  // Keep the token in memory and take it out of the address bar and history.
  const [token] = useState(tokenFromUrl);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (tokenFromUrl) window.history.replaceState(null, "", "/reset-password");
  }, [tokenFromUrl]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    if (password !== String(form.get("confirm") ?? "")) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/auth/password/reset", { method: "POST", body: { token, new_password: password } });
      setDone(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Card>
        <CardHeader>
          <CircleCheck className="mb-1 size-6 text-positive" aria-hidden />
          <CardTitle>Password changed</CardTitle>
          <CardDescription>
            Every device was signed out. Sign in with your new password; two-factor authentication is unchanged.
          </CardDescription>
        </CardHeader>
        <CardFooter>
          <Button asChild className="w-full">
            <Link href="/login">Sign in</Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }

  if (!token) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>This link is incomplete</CardTitle>
          <CardDescription>Open the link from your email again, or request a new one.</CardDescription>
        </CardHeader>
        <CardFooter>
          <Button asChild variant="outline" className="w-full">
            <Link href="/forgot-password">Request a new link</Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose a new password</CardTitle>
        <CardDescription>Changing your password signs out every device.</CardDescription>
      </CardHeader>
      <form onSubmit={submit}>
        <CardContent className="grid gap-4">
          <FormError message={error} />
          <Field
            id="password"
            label="New password"
            type="password"
            autoComplete="new-password"
            minLength={10}
            maxLength={128}
            hint="At least 10 characters."
            autoFocus
            required
          />
          <Field id="confirm" label="Repeat new password" type="password" autoComplete="new-password" required />
        </CardContent>
        <CardFooter className="mt-4 flex-col items-stretch gap-3">
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Change password"}
          </Button>
          <Button asChild variant="ghost">
            <Link href="/forgot-password">Request a new link</Link>
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
