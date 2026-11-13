"use client";

import { CircleCheck, CircleX, Loader2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Field, FormError } from "@/components/form-bits";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";

type State = { status: "checking" } | { status: "confirmed" } | { status: "failed"; message: string };

export function VerifyEmail() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<State>(
    token ? { status: "checking" } : { status: "failed", message: "This link is incomplete." },
  );
  const [resent, setResent] = useState(false);
  const [resendError, setResendError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true; // the link works once, so never send it twice
    // Take the single-use token out of the address bar and browser history.
    window.history.replaceState(null, "", "/verify-email");
    api("/auth/verify-email", { method: "POST", body: { token } })
      .then(() => setState({ status: "confirmed" }))
      .catch((err) => setState({ status: "failed", message: errorMessage(err) }));
  }, [token]);

  async function resend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setResendError(null);
    try {
      await api("/auth/resend-verification", {
        method: "POST",
        body: { email: new FormData(event.currentTarget).get("email") },
      });
      setResent(true);
    } catch (err) {
      setResendError(errorMessage(err));
    }
  }

  if (state.status === "checking") {
    return (
      <Card>
        <CardHeader>
          <Loader2 className="mb-1 size-6 animate-spin text-muted-foreground" aria-hidden />
          <CardTitle>Confirming your email…</CardTitle>
        </CardHeader>
      </Card>
    );
  }

  if (state.status === "confirmed") {
    return (
      <Card>
        <CardHeader>
          <CircleCheck className="mb-1 size-6 text-positive" aria-hidden />
          <CardTitle>Email confirmed</CardTitle>
          <CardDescription>Your account is ready. Sign in to continue.</CardDescription>
        </CardHeader>
        <CardFooter>
          <Button asChild className="w-full">
            <Link href="/login">Sign in</Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CircleX className="mb-1 size-6 text-destructive" aria-hidden />
        <CardTitle>This link did not work</CardTitle>
        <CardDescription>
          {state.message} Links work once and expire after 24 hours. If you already confirmed your email, just sign in.
        </CardDescription>
      </CardHeader>
      <form onSubmit={resend}>
        <CardContent className="grid gap-4">
          <FormError message={resendError} />
          {resent ? (
            <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm">
              If that address has an unconfirmed account, a new link is on its way.
            </p>
          ) : (
            <Field id="email" label="Email" type="email" autoComplete="username" required />
          )}
        </CardContent>
        <CardFooter className="mt-4 flex-col items-stretch gap-3">
          {resent ? null : (
            <Button type="submit" variant="outline">
              Send a new link
            </Button>
          )}
          <Button asChild variant="ghost">
            <Link href="/login">Back to sign in</Link>
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
