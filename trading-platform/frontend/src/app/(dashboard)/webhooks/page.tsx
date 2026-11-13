import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Webhook Connections" };

export default function Page() {
  return <PlannedSection href="/webhooks" />;
}
