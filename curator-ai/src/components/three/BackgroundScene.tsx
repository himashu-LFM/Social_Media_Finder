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
import { useEffect, useState } from "react";

// Client-only lazy import — keeps three.js out of SSR and the initial chunk.
const Scene3D = dynamic(() => import("./Scene3D"), {
  ssr: false,
  loading: () => null,
});

export function BackgroundScene() {
  // Scene3D is client-only (dynamic ssr:false), so no mount guard is needed.
  const [reducedMotion, setReducedMotion] = useState(false);
  const [visible, setVisible] = useState(true);

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

  return (
    <div className="lf-scene3d" aria-hidden>
      <Scene3D
        animate={!reducedMotion}
        frameloop={visible && !reducedMotion ? "always" : "never"}
      />
    </div>
  );
}
