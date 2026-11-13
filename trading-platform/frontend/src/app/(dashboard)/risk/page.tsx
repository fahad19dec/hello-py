import type { Metadata } from "next";

import { Risk } from "./risk";

export const metadata: Metadata = { title: "Risk Management" };

export default function Page() {
  return <Risk />;
}
