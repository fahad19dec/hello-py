import type { Metadata } from "next";

import { Trades } from "./trades";

export const metadata: Metadata = { title: "Trade History" };

export default function Page() {
  return <Trades />;
}
