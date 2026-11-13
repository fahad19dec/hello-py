import type { Metadata } from "next";

import { Signals } from "./signals";

export const metadata: Metadata = { title: "Signal Logs" };

export default function Page() {
  return <Signals />;
}
