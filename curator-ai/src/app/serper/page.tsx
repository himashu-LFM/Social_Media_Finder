import type { Metadata } from "next";
import { Suspense } from "react";
import { SerperClient } from "./SerperClient";

export const metadata: Metadata = {
  title: "Serper Result | ListenFirst",
  description: "Serper + LLM first-pass output, before the Apify backup.",
};

// Server shell for `metadata` only — see ResultsClient for why the data loading
// moved to the browser. Suspense is required by useSearchParams().
export default function SerperResultPage() {
  return (
    <Suspense fallback={null}>
      <SerperClient />
    </Suspense>
  );
}
