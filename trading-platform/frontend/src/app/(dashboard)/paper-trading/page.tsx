import type { Metadata } from "next";

import { PaperTrading } from "./paper-trading";

export const metadata: Metadata = { title: "Paper Trading" };

export default function Page() {
  return <PaperTrading />;
}
