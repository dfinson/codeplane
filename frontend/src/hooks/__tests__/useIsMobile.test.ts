import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useIsMobile } from "../useIsMobile";

// ---------------------------------------------------------------------------
// matchMedia mock
// ---------------------------------------------------------------------------

type MediaListener = (e: { matches: boolean }) => void;
let listeners: MediaListener[] = [];
let currentMatches = false;

beforeEach(() => {
  listeners = [];
  currentMatches = false;

  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: vi.fn((query: string) => {
      // Parse the max-width value from the query
      const match = query.match(/max-width:\s*(\d+)px/);
      const breakpointPx = match ? parseInt(match[1], 10) : 767;
      currentMatches = window.innerWidth <= breakpointPx;
      return {
        matches: currentMatches,
        media: query,
        addEventListener: (_event: string, cb: MediaListener) => {
          listeners.push(cb);
        },
        removeEventListener: (_event: string, cb: MediaListener) => {
          listeners = listeners.filter((l) => l !== cb);
        },
      };
    }),
  });
});

function setWindowWidth(width: number) {
  Object.defineProperty(window, "innerWidth", {
    writable: true,
    configurable: true,
    value: width,
  });
}

function fireMediaChange(matches: boolean) {
  for (const listener of listeners) {
    listener({ matches });
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useIsMobile", () => {
  it("returns false on wide viewport", () => {
    setWindowWidth(1024);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("returns true on narrow viewport", () => {
    setWindowWidth(500);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("responds to media query change", () => {
    setWindowWidth(1024);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);

    act(() => {
      fireMediaChange(true);
    });
    expect(result.current).toBe(true);

    act(() => {
      fireMediaChange(false);
    });
    expect(result.current).toBe(false);
  });

  it("supports custom breakpoint", () => {
    setWindowWidth(500);
    const { result } = renderHook(() => useIsMobile(400));
    // 500 > 399 → not mobile
    expect(result.current).toBe(false);
  });

  it("cleans up listener on unmount", () => {
    setWindowWidth(1024);
    const { unmount } = renderHook(() => useIsMobile());
    expect(listeners).toHaveLength(1);
    unmount();
    expect(listeners).toHaveLength(0);
  });
});
