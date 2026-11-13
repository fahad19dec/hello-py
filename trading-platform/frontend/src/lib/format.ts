export function formatDateTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone,
      timeZoneName: "short",
    }).format(new Date(iso));
  } catch {
    return new Date(iso).toISOString();
  }
}

export function formatRelative(iso: string | null | undefined, now: number): string {
  if (!iso) return "—";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

/** "Chrome on Windows" from a user-agent string; good enough to recognise a device. */
export function describeDevice(userAgent: string | null | undefined): string {
  if (!userAgent) return "Unknown device";
  const browser =
    /Edg\//.test(userAgent) ? "Edge"
    : /OPR\//.test(userAgent) ? "Opera"
    : /Firefox\//.test(userAgent) ? "Firefox"
    : /Chrome\//.test(userAgent) ? "Chrome"
    : /Safari\//.test(userAgent) ? "Safari"
    : null;
  const system =
    /Android/.test(userAgent) ? "Android"
    : /iPhone|iPad|iPod/.test(userAgent) ? "iOS"
    : /Windows/.test(userAgent) ? "Windows"
    : /Mac OS X/.test(userAgent) ? "macOS"
    : /Linux/.test(userAgent) ? "Linux"
    : null;
  if (browser && system) return `${browser} on ${system}`;
  return browser ?? system ?? userAgent.slice(0, 40);
}

const ACTION_LABELS: Record<string, string> = {
  "auth.registered": "Account created",
  "auth.email_verified": "Email confirmed",
  "auth.login.succeeded": "Signed in",
  "auth.login.failed": "Sign-in failed",
  "auth.logout": "Signed out",
  "auth.mfa.enabled": "Two-factor turned on",
  "auth.mfa.disabled": "Two-factor turned off",
  "auth.mfa.challenge_failed": "Wrong two-factor code",
  "auth.mfa.recovery_regenerated": "Recovery codes replaced",
  "auth.password.changed": "Password changed",
  "auth.password.reset_requested": "Password reset requested",
  "auth.password.reset_completed": "Password reset",
  "auth.session.revoked": "Session ended",
  "auth.sessions.revoked_others": "Other sessions ended",
  "user.profile.updated": "Profile updated",
  "admin.user.viewed": "Account viewed by support",
  "admin.user.suspended": "Account suspended",
  "admin.user.unsuspended": "Account reinstated",
  "admin.user.role_changed": "Role changed",
  "admin.user.sessions_revoked": "Signed out by support",
  "admin.bootstrap.created": "Admin account created",
};

export function describeAction(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/** Only same-site paths are followed after sign-in, never an outside address. */
export function safeNextPath(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/overview";
  return value;
}
