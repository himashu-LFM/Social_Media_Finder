"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { useToast } from "@/components/ToastProvider";
import {
  getPythonApiUrl,
  saveProcessingNames,
  setPythonJobId,
} from "@/lib/processing-job";

export function DiscoveryFileUpload() {
  const router = useRouter();
  const { pushToast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const base = getPythonApiUrl();

  function openPicker() {
    inputRef.current?.click();
  }

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !base) return;

    setLoading(true);
    setStatus(null);

    const fd = new FormData();
    fd.append("file", file);

    try {
      const res = await fetch(`${base}/api/upload`, {
        method: "POST",
        body: fd,
      });
      const payload = (await res.json().catch(() => ({}))) as {
        detail?: string | unknown;
        job_id?: string;
        names?: string[];
      };

      if (!res.ok) {
        let msg =
          typeof payload.detail === "string"
            ? payload.detail
            : `Upload failed (${res.status})`;
        if (res.status === 404) {
          msg =
            "Python API returned 404 — the running uvicorn process is outdated. In C:\\Testing stop the server (Ctrl+C), then start: uvicorn api_server:app --host 127.0.0.1 --port 8787 --reload";
        }
        if (res.status === 405) {
          msg =
            "405 Method Not Allowed — restart uvicorn from C:\\Testing with the latest api_server.py (use --reload).";
        }
        setStatus(msg);
        pushToast("Upload failed.", "error");
        setLoading(false);
        return;
      }

      if (!payload.job_id || !payload.names?.length) {
        setStatus("Invalid response from server.");
        pushToast("Invalid upload response.", "error");
        setLoading(false);
        return;
      }

      saveProcessingNames(payload.names);
      setPythonJobId(payload.job_id);
      setLoading(false);
      pushToast("File uploaded.", "success");
      router.push("/processing");
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setStatus(
        `Cannot reach ${base}: ${detail}. Run uvicorn from C:\\Testing (port 8787), set NEXT_PUBLIC_PYTHON_API_URL in .env.local, restart next dev.`,
      );
      pushToast("Cannot reach Python API.", "error");
      setLoading(false);
    }
  }

  return (
    <div className="rounded-2xl border border-dashed border-slate-600/50 bg-surface-high/30 p-6">
      <h3 className="mb-2 text-lg font-bold text-slate-100">Input file</h3>
      <p className="mb-4 text-sm text-slate-400">
        Upload <code className="text-slate-500">.xlsx</code> or{" "}
        <code className="text-slate-500">.csv</code> with a talent name column (same shape as{" "}
        <code className="text-slate-500">Demo_Social.xlsx</code>). The file is sent to the Python API —
        you do not need to copy it into this project folder.
      </p>

      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv"
        className="hidden"
        onChange={(e) => void onFileChange(e)}
      />

      {!base ? (
        <p className="rounded-xl bg-slate-900/80 px-4 py-3 text-sm text-amber-200/90 ring-1 ring-amber-500/20">
          Set <code className="text-amber-100/90">NEXT_PUBLIC_PYTHON_API_URL</code> in{" "}
          <code className="text-amber-100/90">.env.local</code> and start the FastAPI server to enable
          upload.
        </p>
      ) : (
        <>
          <button
            type="button"
            disabled={loading}
            onClick={openPicker}
            className="w-full rounded-xl border border-white/10 bg-surface px-4 py-3 text-sm font-semibold text-slate-200 transition hover:border-primary hover:bg-primary hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Uploading…" : "Choose Excel / CSV"}
          </button>
          {status && (
            <p className="mt-3 text-sm text-rose-300" role="alert">
              {status}
            </p>
          )}
        </>
      )}
    </div>
  );
}
