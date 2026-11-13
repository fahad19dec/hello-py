import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Strategies" };

export default function Page() {
  return <PlannedSection href="/strategies" />;
}
