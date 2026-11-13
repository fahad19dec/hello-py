import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Support" };

export default function Page() {
  return <PlannedSection href="/support" />;
}
