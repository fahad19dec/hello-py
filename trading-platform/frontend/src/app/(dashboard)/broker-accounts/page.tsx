import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Broker Accounts" };

export default function Page() {
  return <PlannedSection href="/broker-accounts" />;
}
