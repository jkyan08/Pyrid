"use client";

import {
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  Drawer as DrawerPrimitive,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";
import { cn } from "@/lib/utils";
import { motion, useReducedMotion } from "motion/react";

/* ------------------------------------------------------------------ */
/*  Animation constants                                                */
/* ------------------------------------------------------------------ */

const STAGGER_BASE_DELAY = 0.12;
const STAGGER_CHILD_DELAY = 0.05;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export type DrawerSide = "top" | "right" | "bottom" | "left";

/** vaul-style side → base-ui swipeDirection (the drawer's resting edge). */
const SIDE_TO_SWIPE_DIRECTION = {
  top: "up",
  right: "right",
  bottom: "down",
  left: "left",
} as const;

export interface DrawerProps {
  /** Drawer content */
  children?: React.ReactNode;
  /** Additional CSS class names */
  className?: string;
  /** Description displayed below the title */
  description?: string;
  /** Footer content */
  footer?: React.ReactNode;
  /** Callback when the open state changes */
  onOpenChange?: (open: boolean) => void;
  /** Whether the drawer is open */
  open?: boolean;
  /** The side from which the drawer opens */
  side?: DrawerSide;
  /** Title displayed in the drawer header */
  title?: string;
  /** Trigger element that opens the drawer */
  trigger?: React.ReactNode;
}

/* ------------------------------------------------------------------ */
/*  Stagger child — animates content items in sequence after open      */
/* ------------------------------------------------------------------ */

const StaggerChild = ({
  children,
  className,
  index,
  shouldReduceMotion,
}: {
  children: React.ReactNode;
  className?: string;
  index: number;
  shouldReduceMotion: boolean | null;
}) => (
  <motion.div
    className={className}
    animate={
      shouldReduceMotion
        ? { opacity: 1 }
        : { opacity: 1, transform: "translateY(0px)" }
    }
    exit={
      shouldReduceMotion
        ? { opacity: 0, transition: { duration: 0 } }
        : { opacity: 0, transition: { duration: 0.1 } }
    }
    initial={
      shouldReduceMotion
        ? { opacity: 1 }
        : { opacity: 0, transform: "translateY(6px)" }
    }
    transition={
      shouldReduceMotion
        ? { duration: 0 }
        : {
            bounce: 0,
            delay: STAGGER_BASE_DELAY + index * STAGGER_CHILD_DELAY,
            duration: 0.25,
            type: "spring" as const,
          }
    }
  >
    {children}
  </motion.div>
);

/* ------------------------------------------------------------------ */
/*  Animated handle bar with glow pulse                                */
/* ------------------------------------------------------------------ */

const AnimatedHandle = ({
  shouldReduceMotion,
}: {
  shouldReduceMotion: boolean | null;
}) => (
  <motion.div
    animate={
      shouldReduceMotion
        ? {}
        : {
            opacity: [0.5, 1, 0.5],
          }
    }
    className="mx-auto mt-4 h-2 w-[100px] shrink-0 rounded-full bg-muted"
    transition={
      shouldReduceMotion
        ? { duration: 0 }
        : {
            duration: 2,
            ease: [0.645, 0.045, 0.355, 1],
            repeat: Number.POSITIVE_INFINITY,
          }
    }
  />
);

/* ------------------------------------------------------------------ */
/*  Drawer                                                             */
/* ------------------------------------------------------------------ */

export default function Drawer({
  open,
  onOpenChange,
  side = "bottom",
  title,
  description,
  className,
  children,
  trigger,
  footer,
}: DrawerProps) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <DrawerPrimitive
      // Adapted for the base-ui drawer primitive (shadcn base-nova):
      // vaul's `direction` becomes `swipeDirection` ("top"/"bottom" → "up"/"down"),
      // and the primitive owns the slide in/out (its own CSS transitions), so
      // the vaul-era exit-holding dance is gone — motion only staggers the
      // content in after the popup mounts.
      swipeDirection={SIDE_TO_SWIPE_DIRECTION[side]}
      onOpenChange={(next) => onOpenChange?.(next)}
      open={open}
    >
      {trigger ? <DrawerTrigger>{trigger}</DrawerTrigger> : null}

      <DrawerContent
        className={cn(
          // Hide the default handle for bottom drawers — we render our own animated one
          side === "bottom" &&
            "[&>[class*='h-2'][class*='rounded-full']]:hidden",
          className
        )}
      >
        {/* Animated handle for bottom drawers */}
        {side === "bottom" && (
          <AnimatedHandle shouldReduceMotion={shouldReduceMotion} />
        )}

        <div className="flex min-h-0 flex-1 flex-col">
          {/* Staggered header */}
          {title || description ? (
            <StaggerChild index={0} shouldReduceMotion={shouldReduceMotion}>
              <DrawerHeader>
                {title ? <DrawerTitle>{title}</DrawerTitle> : null}
                {description ? (
                  <DrawerDescription>{description}</DrawerDescription>
                ) : null}
              </DrawerHeader>
            </StaggerChild>
          ) : null}

          {/* Staggered body (scrollable for long evidence content) */}
          {children ? (
            <StaggerChild
              className="min-h-0 flex-1 overflow-y-auto"
              index={title || description ? 1 : 0}
              shouldReduceMotion={shouldReduceMotion}
            >
              <div className="px-4 pb-6">{children}</div>
            </StaggerChild>
          ) : null}

          {/* Staggered footer */}
          {footer ? (
            <StaggerChild
              index={(title || description ? 1 : 0) + (children ? 1 : 0)}
              shouldReduceMotion={shouldReduceMotion}
            >
              <DrawerFooter>{footer}</DrawerFooter>
            </StaggerChild>
          ) : null}
        </div>
      </DrawerContent>
    </DrawerPrimitive>
  );
}

/* ------------------------------------------------------------------ */
/*  Re-exports                                                         */
/* ------------------------------------------------------------------ */

export {
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
};
