import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "Live Orders" };

export default function Page() {
  return <PlannedSection href="/orders" />;
}
