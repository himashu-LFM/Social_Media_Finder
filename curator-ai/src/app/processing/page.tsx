import type { Metadata } from "next";
import { ProcessingRunner } from "@/components/ProcessingRunner";

export const metadata: Metadata = {
  title: "Processing | Social Scout",
  description: "Per-name discovery progress for social profile resolution.",
};

// A thin server shell so `metadata` stays a server export. The runner owns the
// whole page — including the header's Stop and status pill, which read the
// live run state — so it renders the AppShell itself.
export default function ProcessingPage() {
  return <ProcessingRunner />;
}
