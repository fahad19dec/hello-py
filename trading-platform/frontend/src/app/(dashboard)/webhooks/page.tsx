import type { Metadata } from "next";
import { Suspense } from "react";

import { Webhooks } from "./webhooks";

export const metadata: Metadata = { title: "Webhook Connections" };

export default function Page() {
  return (
    <Suspense>
      <Webhooks />
    </Suspense>
  );
}
