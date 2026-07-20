"use client";

/**
 * FadeIn — reusable fade/slide-in-on-scroll wrapper (Framer Motion).
 * Honours prefers-reduced-motion (renders instantly, no transform) and only
 * animates once when scrolled into view.
 */

import { motion, useReducedMotion } from "framer-motion";
import type { ReactNode } from "react";

type FadeInProps = {
  children: ReactNode;
  /** Stagger helper — seconds to delay the animation. */
  delay?: number;
  /** Pixels to travel on the Y axis before settling. */
  y?: number;
  className?: string;
};

export function FadeIn({ children, delay = 0, y = 16, className }: FadeInProps) {
  const reduce = useReducedMotion();

  return (
    <motion.div
      className={className}
      initial={reduce ? { opacity: 0 } : { opacity: 0, y }}
      whileInView={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.5, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}
