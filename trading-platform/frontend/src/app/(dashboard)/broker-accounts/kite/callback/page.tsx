import type { Metadata } from "next";
import { Suspense } from "react";

import { KiteCallback } from "./kite-callback";

export const metadata: Metadata = { title: "Zerodha login" };

export default function Page() {
  return (
    <Suspense>
      <KiteCallback />
    </Suspense>
  );
}
