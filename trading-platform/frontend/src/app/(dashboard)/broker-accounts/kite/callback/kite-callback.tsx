"use client";

import { CircleCheck, CircleX, Loader2 } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";

/** Zerodha sends the browser here after login, with a one-time request token. */
export function KiteCallback() {
  const params = useSearchParams();
  const [state, setState] = useState<{ status: "working" | "done" | "failed"; message?: string }>({ status: "working" });
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const requestToken = params.get("request_token");
    const loginState = params.get("state");
    // Remove the one-time token from the address bar and history.
    window.history.replaceState(null, "", "/broker-accounts/kite/callback");
    if (params.get("status") !== "success" || !requestToken || !loginState) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState({ status: "failed", message: "The Zerodha login was cancelled or did not finish." });
      return;
    }
    api("/broker-accounts/kite/session", { method: "POST", body: { state: loginState, request_token: requestToken } })
      .then(() => setState({ status: "done" }))
      .catch((err) => setState({ status: "failed", message: errorMessage(err) }));
  }, [params]);

  return (
    <Card className="mx-auto max-w-md">
      <CardHeader>
        {state.status === "working" ? <Loader2 className="mb-1 size-6 animate-spin text-muted-foreground" aria-hidden /> : null}
        {state.status === "done" ? <CircleCheck className="mb-1 size-6 text-positive" aria-hidden /> : null}
        {state.status === "failed" ? <CircleX className="mb-1 size-6 text-destructive" aria-hidden /> : null}
        <CardTitle>
          {state.status === "working" ? "Connecting to Zerodha…" : state.status === "done" ? "Connected to Zerodha" : "Zerodha login failed"}
        </CardTitle>
        <CardDescription>
          {state.status === "done"
            ? "The login is valid until 6:00 IST tomorrow; Zerodha requires a fresh login every trading day."
            : state.message}
        </CardDescription>
      </CardHeader>
      {state.status !== "working" ? (
        <CardFooter>
          <Button asChild>
            <Link href="/broker-accounts">Back to broker accounts</Link>
          </Button>
        </CardFooter>
      ) : null}
    </Card>
  );
}
