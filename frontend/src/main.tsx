import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { Notifications } from "@mantine/notifications";

// Mantine layered styles — must use .layer.css variant so CSS layers
// don't conflict with Tailwind's preflight/base rules.
import "@mantine/core/styles.layer.css";
import "@mantine/notifications/styles.layer.css";

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
