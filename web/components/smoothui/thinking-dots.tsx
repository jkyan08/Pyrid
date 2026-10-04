import { cn } from "@/lib/utils";

/**
 * ThinkingDots — the app's ONE loading indicator: three small dots pulsing
 * in sequence (CSS keyframes in globals.css). Under prefers-reduced-motion
 * the pulse stops and the dots read as a static ellipsis.
 *
 * Palette-colored Hunter Green by default; the dots use `bg-current`, so a
 * `text-*` class on `className` recolors them (e.g. `text-sage` on the dark
 * map canvas).
 */

const DOT_COUNT = 3;
const DOT_STAGGER_S = 0.18;

export interface ThinkingDotsProps {
  className?: string;
  /** Accessible label announced by screen readers. */
  label?: string;
  /** Dot diameter in pixels. */
  size?: number;
}

export default function ThinkingDots({
  className,
  label = "Loading",
  size = 5,
}: ThinkingDotsProps) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn("inline-flex shrink-0 items-center gap-1 text-hunter", className)}
    >
      {Array.from({ length: DOT_COUNT }, (_, index) => (
        <span
          key={index}
          aria-hidden="true"
          className="thinking-dot inline-block rounded-full bg-current motion-reduce:animate-none"
          style={{
            width: `${size}px`,
            height: `${size}px`,
            animationDelay: `${index * DOT_STAGGER_S}s`,
          }}
        />
      ))}
      <span className="sr-only">{label}</span>
    </span>
  );
}
