"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { ApiError, api, setCsrfToken } from "@/lib/api";
import type { Me } from "@/lib/types";

type SessionValue = {
  me: Me;
  /** Re-read the signed-in user after a change (profile, two-factor). */
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionValue | null>(null);

type State = { status: "loading" } | { status: "ready"; me: Me } | { status: "error"; message: string };

/**
 * Loads the signed-in user for everything inside the dashboard.
 *
 * The API decides who is signed in; this only reacts to its answer. Nothing
 * inside renders until the session is confirmed, so a signed-out visitor never
 * sees dashboard content.
 */
export function SessionProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [state, setState] = useState<State>({ status: "loading" });

  const load = useCallback(async () => {
    try {
      const me = await api<Me>("/auth/me");
      setCsrfToken(me.csrf_token);
      setState({ status: "ready", me });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setCsrfToken(null);
        router.replace(`/login?next=${encodeURIComponent(pathname)}`);
        return;
      }
      setState({
        status: "error",
        message: error instanceof ApiError ? error.message : "Could not load your session.",
      });
    }
  }, [router, pathname]);

  useEffect(() => {
    // Runs once per mount; later refreshes are explicit.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {
      // Already signed out on the server is fine; leave either way.
    }
    setCsrfToken(null);
    router.replace("/login");
  }, [router]);

  if (state.status === "loading") return <>{fallback}</>;
  if (state.status === "error") {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm text-muted-foreground">{state.message}</p>
        <button className="text-sm text-primary underline-offset-4 hover:underline" onClick={() => void load()}>
          Try again
        </button>
      </div>
    );
  }
  return <SessionContext value={{ me: state.me, refresh: load, signOut }}>{children}</SessionContext>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside the dashboard");
  return value;
}
