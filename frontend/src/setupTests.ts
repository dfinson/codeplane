import "@testing-library/jest-dom";

// matchMedia is not implemented in jsdom; provide a stub that always reports
// a non-mobile (desktop) viewport so components using useIsMobile render
// their desktop variant by default in tests.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  configurable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
