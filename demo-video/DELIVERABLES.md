# CodePlane Demo Video — Complete Deliverables

## 1. Grounding Summary

**What the product does:**
CodePlane is a control plane for running and supervising coding agents headless on your workstation. It provides live monitoring, approval gates, workspace isolation via git worktrees, cost analytics, and remote access from any browser or device.

**Main user workflow:**
Register repo → Create job (prompt + agent + model + permission mode) → Watch live transcript/plan/metrics → Handle approval gates → Review diffs → Merge, create PR, or discard.

**Key capabilities demonstrated in video:**
1. **Dashboard & Job Management** — Kanban board with real-time state tracking
2. **Job Creation** — Prompt, agent/model selection, permission modes
3. **Live Monitoring** — Real-time transcript, tool calls, plan progress, approval gates
4. **Diff Review** — Side-by-side syntax-highlighted diffs, workspace browser, merge/PR/discard
5. **Cost Analytics** — Token usage, model comparison, cost per job

**Visuals from repo (all real):**
- `docs/images/logo.png` — Product logo
- `docs/images/screenshots/desktop/hero-dashboard.png` — Dashboard screenshot
- `docs/images/screenshots/desktop/create-job-flow.gif` — Job creation animation
- `docs/images/screenshots/desktop/transcript-streaming.gif` — Live monitoring animation
- `docs/images/screenshots/desktop/job-diff-viewer.gif` — Diff viewer animation
- `docs/images/screenshots/desktop/analytics-dashboard.png` — Analytics screenshot
- `docs/images/screenshots/desktop/analytics-scorecard.png` — Scorecard screenshot

**Real commands referenced:**
- `pip install codeplane` — Installation
- `cpl up` — Start server (from backend/cli.py)
- `cpl doctor` — Health check

---

## 2. Scene-by-Scene Video Plan

| # | Scene | Duration | Frames | Visual Source | Transition |
|---|-------|----------|--------|---------------|------------|
| 1 | Hook | 10s | 300 | logo.png + text | — |
| 2 | Problem | 12s | 360 | Animated text list | fade (15f) |
| 3 | Dashboard | 13s | 390 | hero-dashboard.png | fade (15f) |
| 4 | Create Job | 13s | 390 | create-job-flow.gif + steps | fade (15f) |
| 5 | Live Monitoring | 14s | 420 | transcript-streaming.gif + badges | fade (15f) |
| 6 | Diff Review | 12s | 360 | job-diff-viewer.gif + resolution cards | fade (15f) |
| 7 | Analytics | 9s | 270 | analytics-dashboard.png + badges | fade (15f) |
| 8 | Closing | 10.5s | 315 | logo.png + value props + install cmd | fade (15f) |

**Total raw frames:** 2805
**Transition overlap:** 7 × 15 = 105 frames
**Effective duration:** 2805 − 105 = **2700 frames = 90 seconds ✓**

---

## 3. Asset Usage Map

| Public Path | Source in Repo | Used In Scene |
|---|---|---|
| `public/assets/logo.png` | `docs/images/logo.png` | 1 (Hook), 8 (Closing) |
| `public/assets/mark.png` | `docs/images/mark.png` | Available (backup) |
| `public/assets/hero-dashboard.png` | `docs/images/screenshots/desktop/hero-dashboard.png` | 3 (Dashboard) |
| `public/assets/create-job-flow.gif` | `docs/images/screenshots/desktop/create-job-flow.gif` | 4 (Create Job) |
| `public/assets/transcript-streaming.gif` | `docs/images/screenshots/desktop/transcript-streaming.gif` | 5 (Live Monitoring) |
| `public/assets/job-diff-viewer.gif` | `docs/images/screenshots/desktop/job-diff-viewer.gif` | 6 (Diff Review) |
| `public/assets/analytics-dashboard.png` | `docs/images/screenshots/desktop/analytics-dashboard.png` | 7 (Analytics) |
| `public/assets/analytics-scorecard.png` | `docs/images/screenshots/desktop/analytics-scorecard.png` | Available (backup) |

---

## 4. Suno Soundtrack Prompt

```json
{
  "duration_seconds": 90,
  "prompt": "90-second instrumental ambient electronic track for a technical product demo. Soft synthesizer pads with gentle pulse. Starts minimal and quiet with a single pad texture. Builds subtly with light arpeggiated synth notes around 20 seconds. Maintains a calm, focused midtempo groove through the middle section with understated rhythmic elements. Brief gentle lift at 50 seconds, then settles back. Clean fade-out in the final 8 seconds. No vocals, no drums, no bass drops. Minimal, modern, refined.",
  "style_tags": ["instrumental", "ambient", "minimal", "tech", "modern", "electronic"],
  "pacing_notes": {
    "intro": "0-10s: Single soft pad, barely there. Sets a clean, open feeling for the logo reveal.",
    "middle": "10-75s: Gentle build with light arpeggios and subtle rhythmic texture. Never overpowering. Should feel like focused concentration. Brief lift around 50s marks the monitoring/approval gate section.",
    "ending": "75-90s: Simplify back to pad. Clean fade-out over last 8 seconds, ending on a resolved chord."
  }
}
```

---

## 5. Subtitle Script by Scene

### Scene 1: Hook (0–10s)
1. "A control plane for coding agents." (0.5–3.0s)
2. "Run, supervise, and review — from any device." (3.5–6.0s)
3. "Built for headless agent execution." (6.5–9.5s)

### Scene 2: Problem (10–22s)
4. "Agents run in the dark." (10.5–13.5s)
5. "No visibility into reasoning or tool calls." (14.0–16.5s)
6. "No approval gates for risky operations." (17.0–19.0s)
7. "No way to intervene mid-run." (19.5–21.5s)

### Scene 3: Dashboard (22–35s)
8. "See every job at a glance." (22.5–25.0s)
9. "Kanban board tracks queued, running, and review states." (25.5–28.5s)
10. "Multiple agents run in parallel." (29.0–31.5s)
11. "Each job runs in its own git worktree." (32.0–34.5s)

### Scene 4: Create Job (35–48s)
12. "Write a prompt. Pick your agent and model." (35.5–38.5s)
13. "Choose a permission mode." (39.0–41.5s)
14. "Full auto, review & approve, or observe only." (42.0–44.5s)
15. "One click to launch." (45.0–47.5s)

### Scene 5: Live Monitoring (48–62s)
16. "Watch the agent think in real time." (48.5–51.0s)
17. "Reasoning, tool calls, and plan progress stream live." (51.5–54.0s)
18. "Approval gates pause on risky actions." (54.5–57.0s)
19. "Approve, reject, or trust the session." (57.5–59.5s)
20. "Send operator messages to steer the agent." (60.0–61.5s)

### Scene 6: Diff Review (62–74s)
21. "Review every change before it lands." (62.5–65.0s)
22. "Syntax-highlighted side-by-side diffs." (65.5–68.0s)
23. "Browse the full workspace, not just changed files." (68.5–70.5s)
24. "Merge, create a PR, or discard." (71.0–73.5s)

### Scene 7: Analytics (74–83s)
25. "Track token usage and costs across the fleet." (74.5–77.0s)
26. "Compare models by cost and performance." (77.5–80.0s)
27. "No more invisible token burn." (80.5–82.5s)

### Scene 8: Closing (83–90s)
28. "Headless. Remote-first. Open." (83.5–86.0s)
29. "CodePlane — control your coding agents." (86.5–89.5s)

**Total: 29 subtitle cards across 90 seconds.**

---

## 6. Render Instructions

### Prerequisites
```bash
cd demo-video
npm install
```

### Preview in Studio
```bash
npm run studio
# Opens at http://localhost:3000
```

### Render Final Video
```bash
# Default render (H.264, 3840x2160, 30fps)
npm run render

# Output: out/codeplane-demo.mp4
```

### Render Settings
- **Resolution:** 3840×2160 (4K UHD)
- **Codec:** H.264
- **FPS:** 30
- **Duration:** 2700 frames (90.00 seconds)
- **Composition ID:** `CodePlaneDemo`

### Adding Soundtrack
After generating music from Suno using the prompt above:
1. Place the MP3 in `public/assets/soundtrack.mp3`
2. Add this import to `src/DemoVideo.tsx`:
```tsx
import { Audio } from "@remotion/media";
import { staticFile, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
```
3. Add inside the `<AbsoluteFill>` in DemoVideo, after `<SubtitleOverlay />`:
```tsx
<Audio
  src={staticFile("assets/soundtrack.mp3")}
  volume={(f) => {
    const fps = 30;
    // Fade in over first 2 seconds, fade out over last 3 seconds
    return Math.min(
      interpolate(f, [0, 2 * fps], [0, 0.3], { extrapolateRight: "clamp" }),
      interpolate(f, [87 * fps, 90 * fps], [0.3, 0], { extrapolateLeft: "clamp" })
    );
  }}
/>
```

### Full CLI Render Command (advanced)
```bash
npx remotion render src/index.ts CodePlaneDemo out/codeplane-demo.mp4 \
  --codec h264 \
  --image-format jpeg \
  --jpeg-quality 95 \
  --concurrency 50% \
  --log verbose
```
