"use client";

import { motion } from "motion/react";
import type { ReactNode } from "react";

/**
 * Lightweight scroll/entrance reveal wrapper using motion.
 * Use `index` to stagger a list of cards.
 */
export default function Reveal({
  children,
  index = 0,
  y = 12,
  className,
}: {
  children: ReactNode;
  index?: number;
  y?: number;
  className?: string;
}) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-40px" }}
      transition={{
        duration: 0.4,
        delay: Math.min(index * 0.05, 0.4),
        ease: [0.2, 0.7, 0.3, 1],
      }}
    >
      {children}
    </motion.div>
  );
}
