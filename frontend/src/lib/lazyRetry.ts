import { lazy, type ComponentType } from "react";

/**
 * Wraps a dynamic import with retry logic for transient network failures.
 * On final failure, forces a page reload (once) to bust any stale HTML cache.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyRetry<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
  retries = 2,
): ReturnType<typeof lazy<T>> {
  return lazy<T>(() => retry(factory, retries));
}

async function retry<T>(
  factory: () => Promise<T>,
  retries: number,
): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await factory();
    } catch (err) {
      if (attempt >= retries) {
        // Last resort: reload the page once to get fresh asset manifest.
        // Guard with sessionStorage to prevent infinite reload loops.
        const key = "chunk-reload";
        if (!sessionStorage.getItem(key)) {
          sessionStorage.setItem(key, "1");
          window.location.reload();
        }
        throw err;
      }
      // Exponential back-off: 200ms, 400ms
      await new Promise((r) => setTimeout(r, 200 * 2 ** attempt));
    }
  }
}
