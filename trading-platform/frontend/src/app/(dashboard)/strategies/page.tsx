import type { Metadata } from "next";

import { Strategies } from "./strategies";

export const metadata: Metadata = { title: "Strategies" };

export default function Page() {
  return <Strategies />;
}
