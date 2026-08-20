import type { Metadata } from "next";
import { AnalysisClient } from "./AnalysisClient";

export const metadata: Metadata = {
  title: "Analysis | ListenFirst",
  description: "Confidence distribution analysis for verified social links.",
};

// Server shell for `metadata` only. No Suspense needed here — unlike Results
// and Serper, this view does not read search params.
export default function AnalysisPage() {
  return <AnalysisClient />;
}
