import type { Metadata } from "next";

import { Positions } from "./positions";

export const metadata: Metadata = { title: "Open Positions" };

export default function Page() {
  return <Positions />;
}
