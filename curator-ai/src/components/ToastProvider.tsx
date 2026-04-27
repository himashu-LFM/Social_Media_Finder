"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

type ToastTone = "success" | "error" | "info";

type ToastItem = {
  id: number;
  message: string;
  tone: ToastTone;
};

type ToastContextValue = {
  pushToast: (message: string, tone?: ToastTone) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const pushToast = useCallback((message: string, tone: ToastTone = "info") => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((prev) => [...prev, { id, message, tone }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 2600);
  }, []);

  const value = useMemo(() => ({ pushToast }), [pushToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed left-1/2 top-4 z-[100] flex w-[min(420px,92vw)] -translate-x-1/2 flex-col gap-2">
        {toasts.map((t) => {
          const toneClass =
            t.tone === "success"
              ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-100"
              : t.tone === "error"
                ? "border-rose-500/35 bg-rose-500/15 text-rose-100"
                : "border-indigo-500/35 bg-indigo-500/15 text-indigo-100";
          return (
            <div
              key={t.id}
              className={`rounded-lg border px-3 py-2 text-sm shadow-lg backdrop-blur-sm ${toneClass}`}
              role="status"
            >
              {t.message}
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within ToastProvider.");
  }
  return ctx;
}

