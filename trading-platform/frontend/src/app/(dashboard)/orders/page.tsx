import type { Metadata } from "next";

import { Orders } from "./orders";

export const metadata: Metadata = { title: "Live Orders" };

export default function Page() {
  return <Orders />;
}
