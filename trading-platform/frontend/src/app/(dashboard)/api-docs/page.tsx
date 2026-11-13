import type { Metadata } from "next";

import { ApiDocs } from "./api-docs";

export const metadata: Metadata = { title: "API Documentation" };

export default function Page() {
  return <ApiDocs />;
}
