"use client";

/**
 * BackgroundScene — mounts the Three.js scene as a fixed, click-through layer
 * behind all content. Three.js is:
 *   • lazy-loaded (next/dynamic, ssr:false) so it never runs on the server and
 *     is code-split out of the initial bundle,
 *   • paused when the tab is hidden (frameloop "never") to save CPU/GPU,
 *   • fully disabled (static) when the user prefers reduced motion.
 */

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * Routes where the user is reading or operating on dense data. The animated
 * background competes with React for main-thread and GPU time on exactly the
 * pages that render a thousand-plus table cells, so it is skipped there. It
 * stays on the pages where it is the point.
 */
const DATA_DENSE_ROUTES = ["/results", "/serper", "/analysis", "/processing"];

// Client-only lazy import — keeps three.js out of SSR and the initial chunk.
const Scene3D = dynamic(() => import("./Scene3D"), {
  ssr: false,
  loading: () => null,
});

export function BackgroundScene() {
  // Scene3D is client-only (dynamic ssr:false), so no mount guard is needed.
  const [reducedMotion, setReducedMotion] = useState(false);
  const [visible, setVisible] = useState(true);
  const pathname = usePathname();
  const dataDense = DATA_DENSE_ROUTES.some((r) => pathname?.startsWith(r));

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const syncMotion = () => setReducedMotion(mq.matches);
    const syncVisibility = () => setVisible(!document.hidden);

    syncMotion();
    syncVisibility();
    mq.addEventListener?.("change", syncMotion);
    document.addEventListener("visibilitychange", syncVisibility);

    return () => {
      mq.removeEventListener?.("change", syncMotion);
      document.removeEventListener("visibilitychange", syncVisibility);
    };
  }, []);

  // Unmount entirely on data-dense routes — this also frees the WebGL context
  // rather than just idling it, so scrolling a large table gets the whole GPU.
  if (dataDense) return null;

  return (
    <div className="lf-scene3d" aria-hidden>
      <Scene3D
        animate={!reducedMotion}
        frameloop={visible && !reducedMotion ? "always" : "never"}
      />
    </div>
  );
}
