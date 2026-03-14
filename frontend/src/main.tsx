import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";

// Mantine styles imported as JS — must be BEFORE Tailwind to avoid
// Tailwind's CSS processing corrupting Mantine's CSS-in-JS.
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";

import { theme } from "./theme";
import { App } from "./App";

// Tailwind + our custom styles (processed by @tailwindcss/vite plugin)
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Notifications position="top-right" />
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </MantineProvider>
  </StrictMode>,
);
