import { forwardRef, useEffect, useRef } from "react";
import { cn } from "../../lib/utils";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: string;
  /** When true, the textarea grows to fit its content so users never need to scroll within it. */
  autoResize?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, error, autoResize, onChange, ...props }, ref) => {
    const internalRef = useRef<HTMLTextAreaElement>(null);

    const setRef = (el: HTMLTextAreaElement | null) => {
      (internalRef as React.MutableRefObject<HTMLTextAreaElement | null>).current = el;
      if (typeof ref === "function") ref(el);
      else if (ref) ref.current = el;
    };

    // Adjust height whenever the value changes (covers programmatic updates too)
    useEffect(() => {
      const el = internalRef.current;
      if (!autoResize || !el) return;
      el.style.height = "auto";
      el.style.height = el.scrollHeight + "px";
    }, [autoResize, props.value]);

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      if (autoResize) {
        e.currentTarget.style.height = "auto";
        e.currentTarget.style.height = e.currentTarget.scrollHeight + "px";
      }
      onChange?.(e);
    };

    return (
      <>
        <textarea
          ref={setRef}
          aria-invalid={error ? true : undefined}
          className={cn(
            "flex min-h-[80px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 resize-none",
            autoResize && "overflow-hidden",
            error && "border-red-500 focus-visible:ring-red-500",
            className,
          )}
          onChange={handleChange}
          {...props}
        />
        {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
      </>
    );
  },
);
Textarea.displayName = "Textarea";
