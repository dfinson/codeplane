import { useState, useRef, useEffect } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Check, ChevronDown, Search } from "lucide-react";
import { cn } from "../../lib/utils";
import { Label } from "./label";

export interface ComboboxItem {
  value: string;
  label: string;
  disabled?: boolean;
  description?: string;
}

interface ComboboxProps {
  items: ComboboxItem[];
  value: string | null;
  onChange: (value: string | null) => void;
  placeholder?: string;
  label?: string;
  clearable?: boolean;
  className?: string;
}

export function Combobox({
  items,
  value,
  onChange,
  placeholder = "Select…",
  label,
  clearable,
  className,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const selected = items.find((i) => i.value === value);
  const filtered = search
    ? items.filter(
        (i) =>
          i.label.toLowerCase().includes(search.toLowerCase()) ||
          i.value.toLowerCase().includes(search.toLowerCase()),
      )
    : items;

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0);
    else setSearch("");
  }, [open]);

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {label && <Label>{label}</Label>}
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger asChild>
          <button
            type="button"
            className="flex h-9 w-full items-center justify-between rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <span className={cn("truncate", !selected && "text-muted-foreground")}>
              {selected ? selected.label : placeholder}
            </span>
            <ChevronDown size={14} className="opacity-50 shrink-0 ml-2" />
          </button>
        </Popover.Trigger>
        <Popover.Content
          className="z-50 w-[var(--radix-popover-trigger-width)] rounded-md border border-border bg-popover shadow-md p-0"
          align="start"
          sideOffset={4}
        >
          <div className="flex items-center border-b border-border px-3 gap-2">
            <Search size={13} className="text-muted-foreground shrink-0" />
            <input
              ref={inputRef}
              className="flex h-9 w-full bg-transparent py-2 text-sm text-foreground placeholder:text-muted-foreground outline-none"
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="max-h-52 overflow-y-auto p-1">
            {filtered.length === 0 ? (
              <div className="py-4 text-center text-sm text-muted-foreground">No results</div>
            ) : (
              filtered.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  disabled={item.disabled}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm cursor-default",
                    item.disabled
                      ? "opacity-40 cursor-not-allowed"
                      : "text-foreground hover:bg-accent",
                  )}
                  onClick={() => {
                    if (item.disabled) return;
                    onChange(item.value);
                    setOpen(false);
                  }}
                >
                  <Check
                    size={14}
                    className={cn("shrink-0", value === item.value ? "opacity-100" : "opacity-0")}
                  />
                  <span className="flex flex-col items-start min-w-0">
                    <span className="truncate">{item.label}</span>
                    {item.description && (
                      <span className="text-xs text-muted-foreground truncate">{item.description}</span>
                    )}
                  </span>
                </button>
              ))
            )}
          </div>
          {clearable && value && (
            <div className="border-t border-border p-1">
              <button
                type="button"
                className="flex w-full items-center justify-center rounded-sm px-2 py-1 text-xs text-muted-foreground hover:bg-accent"
                onClick={() => {
                  onChange(null);
                  setOpen(false);
                }}
              >
                Clear
              </button>
            </div>
          )}
        </Popover.Content>
      </Popover.Root>
    </div>
  );
}
