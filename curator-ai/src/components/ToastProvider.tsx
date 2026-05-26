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

const TONE_META: Record<
  ToastTone,
  { className: string; icon: string }
> = {
  success: {
    className: "border-emerald-500/35 bg-emerald-500/12 text-emerald-100 shadow-emerald-950/30",
    icon: "check_circle",
  },
  error: {
    className: "border-rose-500/35 bg-rose-500/12 text-rose-100 shadow-rose-950/30",
    icon: "error",
  },
  info: {
    className: "border-primary/40 bg-primary/12 text-yellow-100 shadow-primary/20",
    icon: "info",
  },
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const pushToast = useCallback((message: string, tone: ToastTone = "info") => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((prev) => [...prev, { id, message, tone }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3200);
  }, []);

  const value = useMemo(() => ({ pushToast }), [pushToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed left-1/2 top-4 z-[100] flex w-[min(440px,92vw)] -translate-x-1/2 flex-col gap-2">
        {toasts.map((t) => {
          const meta = TONE_META[t.tone];
          return (
            <div
              key={t.id}
              className={`lf-enter flex items-start gap-2.5 rounded-xl border px-4 py-3 text-sm shadow-xl backdrop-blur-md ${meta.className}`}
              role="status"
            >
              <span className="material-symbols-outlined mt-0.5 text-base">{meta.icon}</span>
              <span className="leading-snug">{t.message}</span>
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
