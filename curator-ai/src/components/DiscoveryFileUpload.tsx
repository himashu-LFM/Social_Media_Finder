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
  const [dragOver, setDragOver] = useState(false);

  const base = getPythonApiUrl();

  function openPicker() {
    inputRef.current?.click();
  }

  async function uploadFile(file: File) {
    if (!base) return;

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

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !base) return;
    await uploadFile(file);
  }

  return (
    <div
      className={`lf-enter lf-enter-delay-1 lf-card lf-card-hover relative overflow-hidden p-6 transition ${
        dragOver ? "border-primary/35 bg-primary/5" : ""
      }`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files?.[0];
        if (file) void uploadFile(file);
      }}
    >
      <div className="absolute -right-10 -top-10 h-32 w-32 rounded-full bg-primary/10 blur-3xl" />
      <div className="relative z-10">
        <div className="mb-3 flex items-center gap-2">
          <span className="material-symbols-outlined text-primary">upload_file</span>
          <h3 className="text-lg font-bold text-slate-100">Input file</h3>
        </div>
        <p className="mb-5 text-sm leading-relaxed text-slate-400">
          Upload <code className="rounded bg-slate-950/80 px-1.5 py-0.5 text-slate-500">.xlsx</code>{" "}
          or <code className="rounded bg-slate-950/80 px-1.5 py-0.5 text-slate-500">.csv</code> with
          a talent name column. The file is sent directly to the Python API.
        </p>

        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv"
          className="hidden"
          onChange={(e) => void onFileChange(e)}
        />

        {!base ? (
          <div className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
            <span className="material-symbols-outlined text-base">info</span>
            <p>
              Set <code className="text-amber-50/90">NEXT_PUBLIC_PYTHON_API_URL</code> in{" "}
              <code className="text-amber-50/90">.env.local</code> and start the FastAPI server to
              enable upload.
            </p>
          </div>
        ) : (
          <>
            <button
              type="button"
              disabled={loading}
              onClick={openPicker}
              className="lf-btn-secondary group flex w-full items-center justify-center gap-2 px-4 py-3.5 text-sm disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="material-symbols-outlined text-lg transition-transform group-hover:-translate-y-0.5">
                {loading ? "hourglass_top" : "cloud_upload"}
              </span>
              {loading ? "Uploading…" : "Choose Excel / CSV or drop here"}
            </button>
            {status && (
              <p className="mt-3 flex items-start gap-2 text-sm text-rose-300" role="alert">
                <span className="material-symbols-outlined text-base">error</span>
                {status}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
