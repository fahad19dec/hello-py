import type { Metadata } from "next";
import { Suspense } from "react";

import { Settings } from "./settings";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <Suspense>
      <Settings />
    </Suspense>
  );
}
