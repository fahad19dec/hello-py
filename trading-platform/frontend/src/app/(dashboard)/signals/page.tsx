import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Signal Logs" };

export default function Page() {
  return <PlannedSection href="/signals" />;
}
