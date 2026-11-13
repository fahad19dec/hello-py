import type { Metadata } from "next";

import { PlannedSection } from "@/components/planned-section";

export const metadata: Metadata = { title: "API Documentation" };

export default function Page() {
  return <PlannedSection href="/api-docs" />;
}
