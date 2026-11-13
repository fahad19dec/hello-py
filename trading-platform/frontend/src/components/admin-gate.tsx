"use client";

import { ShieldAlert } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { useSession } from "@/lib/session";

/**
 * Explains why the admin area is unavailable. The API enforces the same rules;
 * this only saves staff from a confusing error.
 */
export function AdminGate({ children }: { children: ReactNode }) {
  const { me } = useSession();
  if (me.user.role === "user") {
    return (
      <Card className="mx-auto max-w-md">
        <CardHeader>
          <CardTitle>Page not found</CardTitle>
          <CardDescription>There is nothing at this address.</CardDescription>
        </CardHeader>
        <CardFooter>
          <Button asChild variant="outline">
            <Link href="/overview">Back to overview</Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }
  if (!me.second_factor_used) {
    return (
      <Card className="mx-auto max-w-md">
        <CardHeader>
          <ShieldAlert className="mb-1 size-6 text-warning" aria-hidden />
          <CardTitle>Two-factor sign-in required</CardTitle>
          <CardDescription>
            The admin area only opens for sessions that signed in with two-factor authentication.
            {me.user.mfa_enabled
              ? " Sign out and sign in again with your authenticator code."
              : " Turn it on in your security settings, then sign in again."}
          </CardDescription>
        </CardHeader>
        <CardFooter>
          <Button asChild>
            <Link href="/settings?tab=security">Open security settings</Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }
  return <>{children}</>;
}
