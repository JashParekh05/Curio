import { forwardRef, type InputHTMLAttributes } from "react";

// Friendly Pop primitive — Input. Rounded, token-colored surface with a soft
// focus ring (≥44px height for touch). Replaces the brutalist bordered input.
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className = "", ...props }, ref) {
    return (
      <input
        ref={ref}
        className={
          "h-12 w-full rounded-control bg-surface text-on-surface placeholder:text-on-surface-muted " +
          "border border-outline px-4 text-base outline-none transition-[border-color,box-shadow] duration-base " +
          "focus:border-primary focus:shadow-[0_0_0_3px_rgba(108,75,244,0.22)] " +
          className
        }
        {...props}
      />
    );
  },
);
