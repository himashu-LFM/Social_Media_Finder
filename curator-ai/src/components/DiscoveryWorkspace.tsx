"use client";

import { useRouter } from "next/navigation";
import { authedFetch } from "@/lib/auth";
import { applySearchConfig } from "@/lib/search-mode";
import { useRef, useState } from "react";
import { useToast } from "@/components/ToastProvider";
import { getPythonApiUrl, saveProcessingNames, setPythonJobId } from "@/lib/processing-job";

export function DiscoveryWorkspace() {
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
    applySearchConfig(fd);   // no-op in Wikipedia mode

    try {
      const res = await authedFetch("/api/upload", {
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
    <section
      className={`sc-drop${dragOver ? " over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!dragOver) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        const file = e.dataTransfer.files?.[0];
        if (file) void uploadFile(file);
      }}
    >
      <span className="sc-drop-icon">
        <span className="material-symbols-outlined" style={{ fontSize: 24 }}>
          cloud_upload
        </span>
      </span>

      <h2>Drop a talent list to start a run</h2>
      <p>
        We read the talent rows, resolve official profiles across platforms, and export a
        confidence-scored workbook.
      </p>

      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv"
        className="hidden"
        onChange={(e) => void onFileChange(e)}
      />

      {!base ? (
        <p className="sc-note warn" style={{ marginTop: 16, textAlign: "left" }}>
          <span className="material-symbols-outlined">info</span>
          <span>
            Set <code className="sc-code">NEXT_PUBLIC_PYTHON_API_URL</code> in{" "}
            <code className="sc-code">.env.local</code> and start the API to enable
            uploads.
          </span>
        </p>
      ) : (
        <button
          type="button"
          disabled={loading}
          onClick={openPicker}
          className="sc-btn-primary"
          style={{ marginTop: 16 }}
        >
          <span
            className={`material-symbols-outlined${loading ? " animate-spin" : ""}`}
            style={{ fontSize: 18 }}
          >
            {loading ? "progress_activity" : "upload_file"}
          </span>
          {loading ? "Uploading…" : "Choose Excel / CSV"}
        </button>
      )}

      <div className="sc-chip-row" style={{ justifyContent: "center", marginTop: 14 }}>
        <span className="sc-chip">.xlsx</span>
        <span className="sc-chip">.xls</span>
        <span className="sc-chip">.csv</span>
        <span className="sc-chip">Max 25 MB</span>
        <span className="sc-chip">Empty rows skipped</span>
      </div>

      {status && (
        <p
          role="alert"
          className="sc-note warn"
          style={{ marginTop: 14, textAlign: "left" }}
        >
          <span className="material-symbols-outlined">error</span>
          <span>{status}</span>
        </p>
      )}
    </section>
  );
}
