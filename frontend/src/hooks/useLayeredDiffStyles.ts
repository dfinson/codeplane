/**
 * Injects CSS styles for motivation view zones.
 * Called once from DiffViewer to set up the necessary Monaco CSS.
 */

import { useEffect } from "react";

export function useLayeredDiffStyles() {
  useEffect(() => {
    const id = "layered-diff-styles";
    if (document.getElementById(id)) return;
    const style = document.createElement("style");
    style.id = id;
    style.textContent = `
/* Motivation view zone */
.motivation-zone {
  padding: 3px 12px 0 8px;
  background: rgba(156, 220, 254, 0.03);
  border-top: 1px solid #2a2a3a;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  overflow: hidden;
  pointer-events: auto;
}
.motivation-zone .mot-label {
  display: none;
}
.motivation-zone .mot-text {
  color: #9cdcfe;
  font-style: italic;
  font-size: 14px;
  line-height: 1.4;
}

`;
    document.head.appendChild(style);
    return () => { style.remove(); };
  }, []);
}
