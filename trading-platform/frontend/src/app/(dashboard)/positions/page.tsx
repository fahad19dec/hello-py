import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Open Positions" };

export default function Page() {
  return <PlannedSection href="/positions" />;
}
