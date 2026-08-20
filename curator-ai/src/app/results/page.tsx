import type { Metadata } from "next";
import { Suspense } from "react";
import { ResultsClient } from "./ResultsClient";

export const metadata: Metadata = {
  title: "Results | ListenFirst",
  description: "Verified social profile output aligned with the python export schema.",
};

// A thin server shell so `metadata` can stay a server export. All data loading
// happens in the client component, which is where the auth token lives — and
// which lets the whole app be exported as static files.
//
// The Suspense boundary is required: ResultsClient calls useSearchParams().
export default function ResultsPage() {
  return (
    <Suspense fallback={null}>
      <ResultsClient />
    </Suspense>
  );
}
