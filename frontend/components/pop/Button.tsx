import { type ButtonHTMLAttributes, forwardRef } from "react";

// Friendly Pop primitive — Button. Consumes the Phase-0 design tokens
// (tokens.css + tailwind theme): rounded-pill, token colors, soft elevation,
// fast purposeful press, accessible focus ring, ≥44px target, reduced-motion
// safe. Replaces brutalist buttons as screens migrate (Phase 2).

type Variant = "primary" | "secondary" | "soft" | "ghost";
type Size = "sm" | "md" | "lg";

const base =
  "inline-flex items-center justify-center gap-2 font-semibold rounded-pill select-none " +
  "transition-[transform,box-shadow,background-color,filter] duration-base ease-pop " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-canvas " +
  "disabled:opacity-50 disabled:pointer-events-none active:translate-y-px " +
  "motion-reduce:transition-none motion-reduce:active:translate-y-0";

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-primary text-on-primary shadow-elev-1 hover:shadow-elev-2 hover:brightness-[1.03] active:bg-primary-press",
  secondary:
    "bg-secondary text-on-secondary shadow-elev-1 hover:shadow-elev-2 hover:brightness-[1.03]",
  soft: "bg-surface-alt text-on-surface hover:brightness-95",
  ghost: "bg-transparent text-on-surface hover:bg-surface-alt",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-10 px-4 text-sm",
  md: "h-12 px-5 text-base",
  lg: "h-14 px-6 text-lg",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", size = "md", className = "", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      className={`${base} ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
      {...props}
    />
  );
});
