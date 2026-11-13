import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Analytics" };

export default function Page() {
  return <PlannedSection href="/analytics" />;
}
