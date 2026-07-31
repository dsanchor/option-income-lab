"use client";

import { useEffect, useRef } from "react";
import { CountUp } from "countup.js";

/**
 * Animated number that counts up when it scrolls into view.
 * Falls back to the static value if IntersectionObserver / animation is
 * unavailable or reduced-motion is requested.
 */
export default function AnimatedNumber({
  value,
  prefix = "",
  suffix = "",
  decimals = 0,
  duration = 1.1,
  className,
}: {
  value: number;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  duration?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const started = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    if (reduced || !isFinite(value)) {
      el.textContent = `${prefix}${(isFinite(value) ? value : 0).toFixed(decimals)}${suffix}`;
      return;
    }

    const run = () => {
      if (started.current) return;
      started.current = true;
      const cu = new CountUp(el, value, {
        decimalPlaces: decimals,
        duration,
        prefix,
        suffix,
        useEasing: true,
        separator: ",",
      });
      if (!cu.error) cu.start();
      else el.textContent = `${prefix}${value.toFixed(decimals)}${suffix}`;
    };

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) if (e.isIntersecting) run();
      },
      { threshold: 0.3 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [value, prefix, suffix, decimals, duration]);

  return <span ref={ref} className={className}>{`${prefix}${(isFinite(value) ? value : 0).toFixed(decimals)}${suffix}`}</span>;
}
