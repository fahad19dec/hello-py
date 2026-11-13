"use client";

import { Copy, Download, ShieldCheck, TriangleAlert } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Field, FormError } from "@/components/form-bits";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { APP_NAME } from "@/lib/config";
import { useSession } from "@/lib/session";
import type { MfaStatus } from "@/lib/types";

function ChangePasswordCard() {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const next = String(form.get("new_password") ?? "");
    if (next !== String(form.get("confirm") ?? "")) {
      setError("The two new passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { count } = await api<{ count: number }>("/auth/password/change", {
        method: "POST",
        body: { current_password: form.get("current_password"), new_password: next },
      });
      formElement.reset();
      toast.success(
        count > 0 ? `Password changed. ${count} other device${count === 1 ? " was" : "s were"} signed out.` : "Password changed.",
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>Changing it signs out every other device.</CardDescription>
      </CardHeader>
      <form onSubmit={submit}>
        <CardContent className="grid gap-4">
          <FormError message={error} />
          <Field id="current_password" label="Current password" type="password" autoComplete="current-password" required />
          <Field
            id="new_password"
            label="New password"
            type="password"
            autoComplete="new-password"
            minLength={10}
            maxLength={128}
            hint="At least 10 characters."
            required
          />
          <Field id="confirm" label="Repeat new password" type="password" autoComplete="new-password" required />
        </CardContent>
        <CardFooter className="mt-4">
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Change password"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

function RecoveryCodes({ codes }: { codes: string[] }) {
  const text = codes.join("\n");

  function download() {
    const url = URL.createObjectURL(new Blob([`${APP_NAME} recovery codes\n\n${text}\n`], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="grid gap-3">
      <p className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        <span>
          Save these now. They are shown once. Each code signs you in a single time if you lose your authenticator.
        </span>
      </p>
      <ul className="grid grid-cols-2 gap-2 rounded-md border border-border bg-muted/40 p-3 font-mono text-sm">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => navigator.clipboard.writeText(text).then(() => toast.success("Copied"))}
        >
          <Copy aria-hidden /> Copy
        </Button>
        <Button type="button" variant="outline" onClick={download}>
          <Download aria-hidden /> Download
        </Button>
      </div>
    </div>
  );
}

type Flow =
  | { name: "idle" }
  | { name: "setup-password" }
  | { name: "setup-scan"; secret: string; uri: string }
  | { name: "show-codes"; codes: string[]; title: string }
  | { name: "disable" }
  | { name: "regenerate" };

function TwoFactorCard() {
  const { me, refresh } = useSession();
  const [status, setStatus] = useState<MfaStatus | null>(null);
  const [flow, setFlow] = useState<Flow>({ name: "idle" });
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadStatus = useCallback(() => {
    api<MfaStatus>("/auth/mfa").then(setStatus).catch(() => setStatus(null));
  }, []);
  useEffect(loadStatus, [loadStatus]);

  function close() {
    setFlow({ name: "idle" });
    setError(null);
    setUseRecoveryCode(false);
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function field(event: FormEvent<HTMLFormElement>, name: string) {
    return String(new FormData(event.currentTarget).get(name) ?? "").trim();
  }

  async function startSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const password = field(event, "password");
    await run(async () => {
      const setup = await api<{ secret: string; otpauth_uri: string }>("/auth/mfa/setup", {
        method: "POST",
        body: { password },
      });
      setFlow({ name: "setup-scan", secret: setup.secret, uri: setup.otpauth_uri });
    });
  }

  async function confirmSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = field(event, "code");
    await run(async () => {
      const { recovery_codes } = await api<{ recovery_codes: string[] }>("/auth/mfa/enable", {
        method: "POST",
        body: { code },
      });
      await refresh();
      loadStatus();
      setFlow({ name: "show-codes", codes: recovery_codes, title: "Two-factor authentication is on" });
    });
  }

  async function disable(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const password = field(event, "password");
    const code = field(event, "code");
    await run(async () => {
      await api("/auth/mfa/disable", {
        method: "POST",
        body: useRecoveryCode ? { password, recovery_code: code } : { password, code },
      });
      await refresh();
      loadStatus();
      close();
      toast.success("Two-factor authentication turned off");
    });
  }

  async function regenerate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const password = field(event, "password");
    const code = field(event, "code");
    await run(async () => {
      const { recovery_codes } = await api<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", {
        method: "POST",
        body: { password, code },
      });
      loadStatus();
      setFlow({ name: "show-codes", codes: recovery_codes, title: "New recovery codes" });
    });
  }

  const enabled = me.user.mfa_enabled;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Two-factor authentication
          <Badge variant={enabled ? "secondary" : "outline"} className={enabled ? "text-positive" : "text-muted-foreground"}>
            {enabled ? "On" : "Off"}
          </Badge>
        </CardTitle>
        <CardDescription>
          {enabled
            ? "Signing in needs a code from your authenticator app as well as your password."
            : "Add a second step to sign-in using an authenticator app such as Google Authenticator, Authy or 1Password."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {enabled ? (
          <p className="flex items-center gap-2 text-muted-foreground">
            <ShieldCheck className="size-4 text-positive" aria-hidden />
            {status ? `${status.recovery_codes_remaining} of 10 recovery codes unused.` : "Loading…"}
          </p>
        ) : (
          <p className="text-muted-foreground">
            Strongly recommended before you connect a broker account. Admin features require it.
          </p>
        )}
      </CardContent>
      <CardFooter className="mt-4 flex-wrap gap-2">
        {enabled ? (
          <>
            <Button variant="outline" onClick={() => setFlow({ name: "regenerate" })}>
              Replace recovery codes
            </Button>
            <Button variant="destructive" onClick={() => setFlow({ name: "disable" })}>
              Turn off
            </Button>
          </>
        ) : (
          <Button onClick={() => setFlow({ name: "setup-password" })}>Set up two-factor</Button>
        )}
      </CardFooter>

      <Dialog open={flow.name !== "idle"} onOpenChange={(open) => (open ? null : close())}>
        <DialogContent>
          {flow.name === "setup-password" ? (
            <form onSubmit={startSetup} className="grid gap-4">
              <DialogHeader>
                <DialogTitle>Confirm your password</DialogTitle>
                <DialogDescription>For your security, enter your password to start the setup.</DialogDescription>
              </DialogHeader>
              <FormError message={error} />
              <Field id="password" label="Password" type="password" autoComplete="current-password" autoFocus required />
              <DialogFooter>
                <Button type="submit" disabled={busy}>
                  Continue
                </Button>
              </DialogFooter>
            </form>
          ) : null}

          {flow.name === "setup-scan" ? (
            <form onSubmit={confirmSetup} className="grid gap-4">
              <DialogHeader>
                <DialogTitle>Scan this code</DialogTitle>
                <DialogDescription>
                  Scan it with your authenticator app, then enter the 6-digit code the app shows.
                </DialogDescription>
              </DialogHeader>
              <div className="flex flex-col items-center gap-3">
                <div className="rounded-lg bg-white p-3">
                  <QRCodeSVG value={flow.uri} size={176} aria-label="Authenticator setup QR code" />
                </div>
                <p className="text-center text-xs text-muted-foreground">
                  Cannot scan? Enter this key by hand:
                  <br />
                  <span className="break-all font-mono text-foreground">{flow.secret}</span>
                </p>
              </div>
              <FormError message={error} />
              <Field
                id="code"
                label="Code from the app"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="123456"
                className="font-mono tracking-widest"
                autoFocus
                required
              />
              <DialogFooter>
                <Button type="submit" disabled={busy}>
                  Turn on two-factor
                </Button>
              </DialogFooter>
            </form>
          ) : null}

          {flow.name === "show-codes" ? (
            <div className="grid gap-4">
              <DialogHeader>
                <DialogTitle>{flow.title}</DialogTitle>
                <DialogDescription>Keep your recovery codes somewhere safe, away from your phone.</DialogDescription>
              </DialogHeader>
              <RecoveryCodes codes={flow.codes} />
              <DialogFooter>
                <Button onClick={close}>I have saved them</Button>
              </DialogFooter>
            </div>
          ) : null}

          {flow.name === "disable" || flow.name === "regenerate" ? (
            <form onSubmit={flow.name === "disable" ? disable : regenerate} className="grid gap-4">
              <DialogHeader>
                <DialogTitle>
                  {flow.name === "disable" ? "Turn off two-factor authentication" : "Replace recovery codes"}
                </DialogTitle>
                <DialogDescription>
                  {flow.name === "disable"
                    ? "Your account will be protected by your password alone."
                    : "Your current recovery codes stop working as soon as new ones are created."}
                </DialogDescription>
              </DialogHeader>
              <FormError message={error} />
              <Field id="password" label="Password" type="password" autoComplete="current-password" autoFocus required />
              <Field
                key={useRecoveryCode ? "recovery" : "totp"}
                id="code"
                label={useRecoveryCode ? "Recovery code" : "Code from your authenticator app"}
                inputMode={useRecoveryCode ? "text" : "numeric"}
                autoComplete="one-time-code"
                className="font-mono tracking-widest"
                required
              />
              <DialogFooter className="sm:justify-between">
                {flow.name === "disable" ? (
                  <Button type="button" variant="ghost" onClick={() => setUseRecoveryCode(!useRecoveryCode)}>
                    {useRecoveryCode ? "Use authenticator code" : "Use a recovery code"}
                  </Button>
                ) : (
                  <span />
                )}
                <Button type="submit" variant={flow.name === "disable" ? "destructive" : "default"} disabled={busy}>
                  {flow.name === "disable" ? "Turn off" : "Create new codes"}
                </Button>
              </DialogFooter>
            </form>
          ) : null}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export function SecurityTab() {
  return (
    <div className="grid max-w-xl gap-5">
      <TwoFactorCard />
      <ChangePasswordCard />
    </div>
  );
}
