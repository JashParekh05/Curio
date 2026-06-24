import { type HTMLAttributes } from "react";

// Friendly Pop primitive — Card. A rounded surface with soft elevation + a
// hairline outline instead of a hard brutalist border. Padding is left to the
// caller so it composes cleanly.
export function Card({ className = "", ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`bg-surface text-on-surface rounded-card shadow-elev-1 border border-outline ${className}`}
      {...props}
    />
  );
}
