import type { Metadata } from "next";
import { Suspense } from "react";
import { AnalysisClient } from "./AnalysisClient";

export const metadata: Metadata = {
  title: "Analysis | ListenFirst",
  description: "Confidence distribution analysis for verified social links.",
};

// Server shell for `metadata` only. The Suspense boundary is required:
// AnalysisClient calls useSearchParams() to read the ?job= of the run being viewed.
export default function AnalysisPage() {
  return (
    <Suspense fallback={null}>
      <AnalysisClient />
    </Suspense>
  );
}
