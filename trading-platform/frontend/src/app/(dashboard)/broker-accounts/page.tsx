import type { Metadata } from "next";

import { BrokerAccounts } from "./broker-accounts";

export const metadata: Metadata = { title: "Broker Accounts" };

export default function Page() {
  return <BrokerAccounts />;
}
