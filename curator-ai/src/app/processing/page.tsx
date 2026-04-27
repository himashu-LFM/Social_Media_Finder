import type { Metadata } from "next";
import Link from "next/link";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppSidebar } from "@/components/AppSidebar";
import { ProcessingRunner } from "@/components/ProcessingRunner";

export const metadata: Metadata = {
  title: "Processing | ListenFirst",
  description: "Per-name discovery progress for social profile resolution.",
};

export default function ProcessingPage() {
  return (
    <div className="flex min-h-screen flex-col bg-background md:flex-row">
      <AppSidebar />

      <main className="flex min-h-screen flex-1 flex-col pb-24 md:ml-64 md:pb-0">
        <header className="sticky top-0 z-30 flex w-full items-center justify-between border-b border-white/5 bg-background/90 px-6 py-4 backdrop-blur-md">
          <h1 className="text-lg font-bold text-slate-100">Processing</h1>
          <Link
            href="/results"
            className="cursor-pointer rounded-lg bg-slate-800 px-4 py-2 text-sm font-semibold text-slate-200 ring-1 ring-white/10 transition hover:bg-slate-700 hover:brightness-110"
          >
            View Results
          </Link>
        </header>

        <div className="flex flex-1 flex-col">
          <ProcessingRunner />
        </div>
      </main>

      <AppMobileNav />
    </div>
  );
}
