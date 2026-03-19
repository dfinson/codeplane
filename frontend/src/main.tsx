import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Toaster } from "sonner";
import { App } from "./App";
import { useStore } from "./store";
import "./index.css";

// Expose the store for e2e test assertions.
(window as unknown as Record<string, unknown>)["__cpl__"] = { store: useStore };

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <TooltipPrimitive.Provider delayDuration={300}>
        <App />
        <Toaster position="top-right" theme="dark" richColors />
      </TooltipPrimitive.Provider>
    </BrowserRouter>
  </StrictMode>,
);
