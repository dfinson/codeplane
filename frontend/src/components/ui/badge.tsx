import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        destructive: "border-transparent bg-destructive text-destructive-foreground",
        outline: "border-border text-foreground",
        light: "border-transparent bg-primary/15 text-primary",
        green: "border-transparent bg-green-500/15 text-green-400",
        red: "border-transparent bg-red-500/15 text-red-400",
        yellow: "border-transparent bg-yellow-500/15 text-yellow-400",
        blue: "border-transparent bg-blue-500/15 text-blue-400",
        gray: "border-transparent bg-secondary text-muted-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

/** Status dot badge — used for SSE connection indicator */
export function DotBadge({
  color,
  children,
  className,
  ...rest
}: {
  color: "green" | "yellow" | "red";
  children: React.ReactNode;
  className?: string;
} & React.HTMLAttributes<HTMLSpanElement>) {
  const dot: Record<string, string> = {
    green: "bg-green-500",
    yellow: "bg-yellow-500",
    red: "bg-red-500",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-xs font-medium text-muted-foreground",
        className,
      )}
      {...rest}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", dot[color])} />
      {children}
    </span>
  );
}
