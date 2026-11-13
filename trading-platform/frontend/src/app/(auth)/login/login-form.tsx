"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Field, FormError } from "@/components/form-bits";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, api, errorMessage, setCsrfToken } from "@/lib/api";
import { safeNextPath } from "@/lib/format";
import type { LoginResult } from "@/lib/types";

export function LoginForm() {
  const router = useRouter();
  const next = safeNextPath(useSearchParams().get("next"));

  const [step, setStep] = useState<"password" | "second-factor">("password");
  const [email, setEmail] = useState("");
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [unverified, setUnverified] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    setNotice(null);
    setUnverified(false);
    try {
      const result = await api<LoginResult>("/auth/login", {
        method: "POST",
        body: { email: form.get("email"), password: form.get("password") },
      });
      setCsrfToken(result.csrf_token);
      if (result.mfa_required) {
        setStep("second-factor");
      } else {
        router.replace(next);
      }
    } catch (err) {
      if (err instanceof ApiError && err.code === "email_not_verified") setUnverified(true);
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitSecondFactor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = String(new FormData(event.currentTarget).get("code") ?? "").trim();
    setBusy(true);
    setError(null);
    try {
      const result = await api<LoginResult>("/auth/mfa/verify", {
        method: "POST",
        body: useRecoveryCode ? { recovery_code: value } : { code: value },
      });
      setCsrfToken(result.csrf_token);
      router.replace(next);
    } catch (err) {
      if (err instanceof ApiError && (err.code === "mfa_attempts_exceeded" || err.code === "not_authenticated")) {
        setStep("password");
        setError("Too many wrong codes. Sign in again.");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function resendConfirmation() {
    setBusy(true);
    try {
      await api("/auth/resend-verification", { method: "POST", body: { email } });
      setError(null);
      setUnverified(false);
      setNotice("We sent a new confirmation link. Check your inbox.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (step === "second-factor") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Two-factor authentication</CardTitle>
          <CardDescription>
            {useRecoveryCode
              ? "Enter one of your recovery codes. Each code works once."
              : "Enter the 6-digit code from your authenticator app."}
          </CardDescription>
        </CardHeader>
        <form onSubmit={submitSecondFactor}>
          <CardContent className="grid gap-4">
            <FormError message={error} />
            <Field
              key={useRecoveryCode ? "recovery" : "totp"}
              id="code"
              label={useRecoveryCode ? "Recovery code" : "Authentication code"}
              autoComplete="one-time-code"
              inputMode={useRecoveryCode ? "text" : "numeric"}
              placeholder={useRecoveryCode ? "abcd-efgh-jkmn" : "123456"}
              className="font-mono tracking-widest"
              autoFocus
              required
            />
          </CardContent>
          <CardFooter className="mt-4 flex-col items-stretch gap-3">
            <Button type="submit" disabled={busy}>
              {busy ? "Checking…" : "Verify"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setUseRecoveryCode(!useRecoveryCode);
                setError(null);
              }}
            >
              {useRecoveryCode ? "Use authenticator code instead" : "Use a recovery code instead"}
            </Button>
          </CardFooter>
        </form>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
        <CardDescription>Use the email and password for your account.</CardDescription>
      </CardHeader>
      <form onSubmit={submitPassword}>
        <CardContent className="grid gap-4">
          <FormError message={error} />
          {notice ? <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm">{notice}</p> : null}
          {unverified ? (
            <Button type="button" variant="outline" onClick={resendConfirmation} disabled={busy}>
              Send the confirmation link again
            </Button>
          ) : null}
          <Field
            id="email"
            label="Email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoFocus
            required
          />
          <Field id="password" label="Password" type="password" autoComplete="current-password" required />
        </CardContent>
        <CardFooter className="mt-4 flex-col items-stretch gap-3">
          <Button type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
          <div className="flex justify-between text-sm">
            <Link href="/forgot-password" className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
              Forgot password?
            </Link>
            <Link href="/register" className="text-primary underline-offset-4 hover:underline">
              Create account
            </Link>
          </div>
        </CardFooter>
      </form>
    </Card>
  );
}
