import type { Metadata } from "next";

import { AdminAudit } from "./admin-audit";

export const metadata: Metadata = { title: "Audit log" };

export default function AdminAuditPage() {
  return <AdminAudit />;
}
