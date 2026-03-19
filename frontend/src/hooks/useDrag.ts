import { useCallback, useRef } from "react";

interface UseDragOptions {
  axis: "x" | "y";
  onDrag: (delta: number) => void;
  onDragEnd?: () => void;
}

export function useDrag({ axis, onDrag, onDragEnd }: UseDragOptions) {
  const dragging = useRef(false);
  // Keep latest callbacks in refs to avoid stale closures in event handlers
  const onDragRef = useRef(onDrag);
  onDragRef.current = onDrag;
  const onDragEndRef = useRef(onDragEnd);
  onDragEndRef.current = onDragEnd;

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      const startPos = axis === "y" ? e.clientY : e.clientX;

      const onMove = (ev: MouseEvent) => {
        if (!dragging.current) return;
        const currentPos = axis === "y" ? ev.clientY : ev.clientX;
        onDragRef.current(startPos - currentPos);
      };

      const onUp = () => {
        dragging.current = false;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        onDragEndRef.current?.();
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [axis],
  );

  const onTouchStart = useCallback(
    (e: React.TouchEvent) => {
      dragging.current = true;
      const touch = e.touches[0];
      if (!touch) return;
      const startPos = axis === "y" ? touch.clientY : touch.clientX;

      const onMove = (ev: TouchEvent) => {
        if (!dragging.current) return;
        const t = ev.touches[0];
        if (!t) return;
        const currentPos = axis === "y" ? t.clientY : t.clientX;
        onDragRef.current(startPos - currentPos);
      };

      const onEnd = () => {
        dragging.current = false;
        document.removeEventListener("touchmove", onMove);
        document.removeEventListener("touchend", onEnd);
        onDragEndRef.current?.();
      };

      document.addEventListener("touchmove", onMove, { passive: false });
      document.addEventListener("touchend", onEnd);
    },
    [axis],
  );

  return { onMouseDown, onTouchStart };
}
