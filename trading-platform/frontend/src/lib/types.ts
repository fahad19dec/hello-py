export type Role = "user" | "support_admin" | "super_admin";

export type User = {
  id: string;
  email: string;
  display_name: string;
  timezone: string;
  role: Role;
  status: "active" | "suspended";
  mfa_enabled: boolean;
  email_verified: boolean;
  created_at: string;
  last_login_at: string | null;
};

export type Me = {
  user: User;
  csrf_token: string;
  session_id: string;
  session_expires_at: string;
  second_factor_used: boolean;
};

export type LoginResult = {
  mfa_required: boolean;
  csrf_token: string;
  user: User | null;
};

export type SessionInfo = {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
};

export type Activity = {
  id: number;
  created_at: string;
  action: string;
  actor: "you" | "staff" | "system";
  ip: string | null;
  user_agent: string | null;
  detail: Record<string, unknown>;
};

export type MfaStatus = { enabled: boolean; recovery_codes_remaining: number };

export type SystemStatus = {
  app_name: string;
  version: string;
  environment: string;
  live_trading_enabled: boolean;
  paper_trading_enabled: boolean;
};

export type AdminUser = {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  status: "active" | "suspended";
  suspended_reason: string | null;
  mfa_enabled: boolean;
  email_verified: boolean;
  created_at: string;
  last_login_at: string | null;
};

export type AdminUserDetail = {
  user: AdminUser;
  sessions: { id: string; created_at: string; last_seen_at: string; ip: string | null; user_agent: string | null }[];
};

export type AuditEntry = {
  id: number;
  created_at: string;
  user_id: string | null;
  actor_user_id: string | null;
  actor_role: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  ip: string | null;
  detail: Record<string, unknown>;
};

export const ROLE_LABELS: Record<Role, string> = {
  user: "User",
  support_admin: "Support admin",
  super_admin: "Super admin",
};
