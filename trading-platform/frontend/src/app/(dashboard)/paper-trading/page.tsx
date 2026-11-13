import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Paper Trading" };

export default function Page() {
  return <PlannedSection href="/paper-trading" />;
}
