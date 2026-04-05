# CodePlane Demo Video — Phase 1 Plan

**Format:** 3840×2160 @ 30fps, H.264  
**Duration:** ~51 seconds (~1530 frames)  
**Tone:** Fast, cinematic, high-energy product demo — not a tutorial or slideshow

---

## 1. Product Understanding

CodePlane gives you a **mental model over multiple concurrent agentic coding flows**. Controlling a single agent is already solved — the hard problem is maintaining situational awareness when you have 5, 10, 20 agents working in parallel across different repos and branches. CodePlane provides:

- **Fleet-level situational awareness** — a single Kanban view of every agent job across every repo, at a glance
- **Live observability** — real-time execution timeline, plan steps, transcript streaming so you know what each agent is actually doing right now
- **Approval routing** — dangerous operations surface to you without you having to watch every terminal
- **Diff review** — inspect agent-produced changes before merging
- **Cost attribution** — understand where your money goes across models, repos, and tools
- **Mobile as first-class citizen** — purpose-built mobile UI (not just responsive breakpoints) with tab-based job lists, thumb-zone actions, inline diff viewer, and full job management from your phone

The UI is a dark-mode Kanban dashboard (3 columns: In Progress / Awaiting Input / Failed) with drill-down into individual job detail views. On mobile, this transforms into a tab-based list with dedicated `MobileJobList`, mobile-optimized diff viewer (inline mode), and a floating action button for job creation.

---

## 2. UI Component Discovery

### Directly Usable (recreated with static props, matching exact Tailwind classes)

| Component | Source | Props Needed | Rendering Notes |
|---|---|---|---|
| **StateBadge** | `StateBadge.tsx` | `state: string` | Pure — 7 states with color/icon config. No store dependency. |
| **SdkBadge** | `SdkBadge.tsx` | `sdk: string` | Pure — "claude" (orange) or "copilot" (purple) with brand SVG icons. |
| **JobCard** | `JobCard.tsx` | `JobSummary` object | Recreate as static card — title, repo, branch, StateBadge, SdkBadge, timeline headline, elapsed time. Remove `useNavigate`/`useStore`. |
| **KanbanColumn** | `KanbanColumn.tsx` | `title, jobs[]` | Header with count badge + scrollable card list. Straightforward recreation. |
| **ExecutionTimeline** | `ExecutionTimeline.tsx` | `TimelineEntry[]` | Vertical rail with dots + timestamps + headlines. Fully recreatable. |
| **PlanPanel** | `PlanPanel.tsx` | `PlanStep[]` | Step list with status icons (done ✓, active spinner, pending ○). Recreatable. |
| **ApprovalBanner** | `ApprovalBanner.tsx` | `Approval` object | Orange/red bordered card with description + proposed action + Approve/Reject buttons. |
| **ResolutionBadge** | inside `JobCard.tsx` | `resolution: string` | "Merged" (green), "PR created" (blue), "Conflict" (orange), etc. |

### Recreated with Mock Data (can't import directly due to runtime deps)

All of these are fully recreatable — the visual output will match the real UI. The only reason we don't `import` them directly is specific runtime dependencies that don't exist in Remotion's headless renderer.

| Component | Blocking Dep | Recreation Strategy |
|---|---|---|
| **DiffViewer** | Monaco Editor (web workers, DOM measurement) | Styled `<pre>` blocks matching Monaco's dark theme colors — green/red diff lines, line numbers, file tabs. Visually identical. |
| **TranscriptPanel** | `@tanstack/react-virtual` (needs scroll container DOM measurement) | Render the visible slice directly — no virtualization needed since we control exactly which frames/lines are visible. Markdown rendered as styled spans. |
| **DashboardScreen** | SSE subscriptions, API polling, React Router | Compose from KanbanBoard + toolbar. All data from mock objects. Layout matches 1:1. |
| **JobDetailScreen** | SSE, API calls, live store subscriptions | Compose from ExecutionTimeline + PlanPanel + TranscriptPanel + ApprovalBanner + DiffView. Mock data for each panel. |
| **JobCreationScreen** | Form state, API mutation, toast notifications | Recreate the form layout with pre-filled fields. Animate cursor typing into inputs. |
| **MobileJobList** | Zustand store, React Router | Tab-based mobile view with filter input, 3 segmented tabs (In Progress / Awaiting Input / Failed), stacked job cards. Straightforward recreation. |
| **Mobile DiffViewer** | Monaco Editor | Inline diff mode (not side-by-side), file list stacked above code. Styled `<pre>` blocks. |
| **Mobile FAB** | React Router navigate | Floating "+" button in bottom-right corner. Pure visual. |

### Design System Tokens (from `frontend/src/index.css`)

```
--background:       220 20% 7%    → #0f1117
--foreground:       213 27% 90%   → #dce4ef
--card:             215 22% 11%   → #161b26
--primary:          217 91% 60%   → #4a8af5
--muted-foreground: 215 12% 57%  → #8490a3
--border:           215 12% 21%   → #2e3544
--destructive:      0 63% 40%    → #a62c2c
--radius:           0.5rem
```

Status colors (from StateBadge):
- queued: `yellow-900/30` + `yellow-400`
- running: `blue-900/30` + `blue-400`
- waiting_for_approval: `orange-900/30` + `orange-400`
- review: `cyan-900/30` + `cyan-400`
- completed: `green-900/30` + `green-400`
- failed: `red-900/30` + `red-400`

SDK colors:
- Claude Code: `#D97757` bg, `#F0B6A4` text
- GitHub Copilot: `#8534F3` bg, `#C898FD` text

---

## 3. Scene Plan

**Total: 50 seconds = 1500 frames @ 30fps**  
**Transitions: 10-frame (0.33s) crossfades between scenes**

| # | Scene | Frames | Time | Content |
|---|---|---|---|---|
| 1 | **Cold Open** | 90 | 0:00–0:03 | Full Kanban board animates in — 8 job cards cascade into 3 columns. State badges pulse. Instant visual density. |
| 2 | **Problem Statement** | 120 | 0:03–0:07 | Dark screen with animated text: agent icons (Claude + Copilot) multiply across screen → text "5 agents. 5 terminals. Zero mental model." fades in with glitch effect. |
| 3 | **Dashboard Reveal** | 180 | 0:07–0:13 | Camera zooms into the Kanban board. Cursor navigates to a "Running" job card and clicks. Card expands to fill screen (transition to job detail). |
| 4 | **Live Execution** | 210 | 0:13–0:20 | Split panel: Left = ExecutionTimeline with milestones appearing one by one. Right = PlanPanel with steps checking off. Streaming text at bottom simulates real-time transcript. |
| 5 | **Approval Gate** | 180 | 0:20–0:26 | ApprovalBanner slides in from top with orange glow pulse. Shows `git reset --hard` command. Cursor hovers "Approve" button → click → banner resolves, StateBadge transitions `Approval → Running`. |
| 6 | **Diff Review** | 180 | 0:26–0:32 | Simplified diff view with green/red lines typing in progressively. File tabs at top. Cursor clicks "Merge" → ResolutionBadge appears "Merged ✓". |
| 7 | **Analytics Montage** | 150 | 0:32–0:37 | Fast 3-panel montage: cost-by-model bar chart → repo breakdown donut → tool health heatmap. Numbers count up. All animated, no static frames. |
| 8 | **Mobile Showcase** | 150 | 0:37–0:42 | Phone frame animates in from right. Inside: MobileJobList with tabs, thumb swipes between "In Progress" and "Awaiting Input" tabs, approval banner appears, tap to approve. Then phone scales down to sit beside the desktop Kanban — same data, both views live. Caption: "Full control from your phone". |
| 9 | **Scale Shot** | 120 | 0:42–0:46 | Pull back to zoomed-out Kanban with 12+ jobs. Mixed Claude/Copilot badges. Multiple state transitions fire simultaneously (queued→running, running→review, review→completed). |
| 10 | **Architecture Diagram** | 120 | 0:46–0:50 | Animated system diagram (see §4). Nodes and edges draw in sequentially. Data flow pulses along connections. |
| 11 | **Closing** | 120 | 0:50–0:54 | Logo mark centers. `pip install codeplane` types out below. GitHub stars badge + "Open Source" text. Subtle particle background. |

**Total: 54 seconds = 1620 frames. With 10 transitions of 10 frames each = 100 overlap → effective 1620 - 100 = 1520 frames ≈ 50.7s**

### Pacing Rules
- No single static frame held longer than 0.8s
- Every scene has at least one animated element active at all times
- Transitions use `@remotion/transitions` fade (scenes 1-3) and slide (scenes 4-6) and wipe (scenes 7-10)
- Scene 7 (Analytics) uses internal cuts every 50 frames (1.67s) — fastest scene

---

## 4. System Diagram Plan

**Scene 9 — animated node-and-edge architecture diagram**

Layout (left-to-right flow):

```
┌──────────┐         ┌─────────────┐         ┌──────────────┐
│ Developer │ ──CLI──→│  CodePlane   │ ──SDK──→│ Claude Code  │
│    👤     │ ──API──→│  ┌────────┐ │         │   (orange)   │
└──────────┘         │  │ Engine │ │         └──────┬───────┘
                     │  └────────┘ │                │
                     │  Dashboard  │         ┌──────┴───────┐
                     │  Approvals  │         │    GitHub     │
                     │  Analytics  │ ──SDK──→│   Copilot     │
                     └─────────────┘         │   (purple)    │
                            │                └──────┬───────┘
                            │                       │
                            ↓                       ↓
                     ┌─────────────┐         ┌─────────────┐
                     │  Git Repos  │←────────│  Worktrees   │
                     └─────────────┘         └─────────────┘
```

**Animation sequence (150 frames):**
1. Frames 0-30: Developer node draws in (border traces clockwise)
2. Frames 20-50: Connection arrows animate left-to-right with traveling dots
3. Frames 40-70: CodePlane central node draws in (border + inner modules cascade)
4. Frames 60-90: Right-side arrows animate to agent nodes
5. Frames 80-110: Claude + Copilot nodes draw in with brand colors
6. Frames 100-130: Bottom connections animate (worktrees, repos)
7. Frames 120-150: Continuous data-flow pulse loops (glowing dots traversing edges)

**Visual style:**
- Nodes: rounded rectangles with `--card` background, `--border` stroke, 2px
- Active node: primary glow (`--primary` with 20% opacity shadow)
- Edges: 1.5px stroke, animated dash pattern
- Data flow: small circles (4px) traveling along edges with motion blur
- Labels: Inter 600 weight, `--foreground` color

---

## 5. Interaction Plan (Cursor Choreography)

A custom animated cursor component with subtle motion blur on movement.

### Cursor Specification
- **Appearance:** macOS-style white arrow cursor, 32×32px at 4K, with subtle drop shadow
- **Motion:** eased movement (cubic-bezier) between points — never teleport, never linear
- **Click effect:** cursor briefly scales down (0.9x) with a radial ripple emanating from click point
- **Hover effect:** target element gets a subtle border-color transition to `--primary`

### Choreography Timeline

| Scene | Time | Action | From → To |
|---|---|---|---|
| 3: Dashboard | 0:09 | Cursor enters from bottom-right, glides to "Running" job card | (3400,1800) → (1200,600) |
| 3: Dashboard | 0:11 | Hover highlight on card, then click | stationary → click ripple |
| 5: Approval | 0:23 | Cursor moves to "Approve" button | (1920,800) → (2400,1400) |
| 5: Approval | 0:24.5 | Click "Approve" — button depresses, banner animates out | click ripple |
| 6: Diff Review | 0:30 | Cursor moves to "Merge" button | (1600,1200) → (2800,1800) |
| 6: Diff Review | 0:31 | Click "Merge" — resolution badge animates in | click ripple |
| 8: Mobile | 0:38 | Finger-style touch indicator taps "Awaiting Input" tab | center of phone frame |
| 8: Mobile | 0:40 | Touch taps "Approve" on mobile approval banner | lower area of phone frame |

### Implementation Approach
- Single `<AnimatedCursor>` component wrapping `useCurrentFrame()` with keyframe interpolation
- Position keyframes defined as `{ frame: number, x: number, y: number, clicking?: boolean }`
- Use `interpolate()` with `Easing.bezier(0.4, 0, 0.2, 1)` for smooth movement
- Click ripple: expanding circle (opacity 0.4→0, scale 0→40px) over 10 frames
- Cursor hidden during scenes without interactions (scenes 1, 2, 4, 7, 8, 9, 10)

---

## 6. Mock Data Plan

### Job Data (8 jobs across the Kanban)

**In Progress (column 1):**
1. `{ title: "Add user authentication", repo: "acme/backend", branch: "feat/auth", state: "running", sdk: "claude", elapsed: "12m", headline: "Implementing JWT middleware", planProgress: "3/5" }`
2. `{ title: "Fix pagination bug", repo: "acme/api", branch: "fix/pagination", state: "running", sdk: "copilot", elapsed: "4m", headline: "Writing test cases", planProgress: "2/4" }`
3. `{ title: "Migrate to PostgreSQL", repo: "acme/backend", branch: "feat/postgres", state: "queued", sdk: "claude", elapsed: "1m" }`

**Awaiting Input (column 2):**
4. `{ title: "Refactor auth module", repo: "acme/backend", branch: "refactor/auth", state: "waiting_for_approval", sdk: "claude", elapsed: "8m", approvalDesc: "git reset --hard HEAD~3" }`
5. `{ title: "Update API docs", repo: "acme/docs", branch: "docs/api-v2", state: "review", sdk: "copilot", elapsed: "22m", resolution: "unresolved" }`

**Failed (column 3):**
6. `{ title: "Add dark mode", repo: "acme/frontend", branch: "feat/darkmode", state: "failed", sdk: "copilot", elapsed: "15m", failureReason: "Cannot resolve module '@/theme'" }`

**Scale Shot (additional jobs for scene 8):**
7-12. Six more jobs with varied states, repos (`acme/mobile`, `acme/infra`, `acme/ml-pipeline`), mixed SDKs, and realistic timelines.

### Execution Timeline (scene 4)

```
12:04:22  ✓ Cloned repository
12:04:25  ✓ Created worktree on feat/auth
12:04:31  ✓ Analyzed codebase structure
12:04:45  ✓ Generated implementation plan
12:05:02  ● Implementing JWT middleware    ← active
12:05:??  ○ Writing test suite             ← pending
```

### Plan Steps (scene 4)

```
✓ Analyze existing auth patterns
✓ Create JWT utility module
✓ Add middleware to route handlers
● Write integration tests            ← active (spinner)
○ Update API documentation
```

### Approval (scene 5)

```
⚠️ Explicit Approval Required
This operation cannot be auto-approved via "Trust Session".

"The agent wants to reset the branch to remove 3 conflicting commits."

Proposed action:
  git reset --hard HEAD~3

[Approve]  [Reject]
```

### Diff (scene 6)

Simplified diff showing 3 files changed:
```
src/middleware/auth.ts  (+42 -3)
src/routes/users.ts    (+18 -5)
tests/auth.test.ts     (+67 -0)
```

With visible code:
```diff
+ import { verifyToken } from '../middleware/auth';
+
+ export async function authenticate(req: Request) {
+   const token = req.headers.get('Authorization');
+   if (!token) throw new UnauthorizedError();
-   // TODO: implement auth
+   return verifyToken(token.replace('Bearer ', ''));
+ }
```

### Analytics (scene 7)

- Cost by model: Claude $12.40 (65%), Copilot $6.70 (35%) — animated bar chart
- Repo breakdown: backend 45%, api 30%, frontend 15%, docs 10% — donut chart
- Tool usage: file_edit (340), bash (210), search (180) — horizontal bars

---

## 7. Visual Design Plan

### Animated Background

Every scene gets a living background — no flat solid colors.

- **Primary background:** Deep dark navy (`hsl(220, 20%, 5%)`) with a subtle animated gradient mesh
- **Gradient mesh:** 3-4 large soft radial gradients that slowly drift (1-2px per frame):
  - Top-left: `hsl(217, 91%, 60%, 0.04)` (primary blue, very faint)
  - Bottom-right: `hsl(280, 60%, 50%, 0.03)` (purple tint)
  - Center: `hsl(180, 50%, 40%, 0.02)` (teal accent)
- **Grid overlay:** Faint dot grid (opacity 0.03) that adds depth without distraction
- **Scene-specific accents:**
  - Approval scene: orange radial pulse (`hsl(30, 90%, 50%, 0.06)`) behind the banner
  - Analytics scene: subtle green tint shift
  - Closing: centered radial glow behind logo

### Layout Philosophy

- **Never full-bleed screenshots** — all UI elements float in the center 70-80% of the frame with generous dark margins
- **Depth layers:** background mesh → subtle grid → floating UI panels → overlays → cursor → captions
- **Card elevation:** UI panels have `box-shadow: 0 0 60px rgba(0,0,0,0.5)` for floating-on-dark effect
- **Scale:** Components render at approximately 1.4x their normal size to fill 4K frame comfortably

### Typography

- **Headings:** Inter 700, 72px (at 4K), `--foreground` color
- **Body/UI text:** Inter 400-600, component-native sizes scaled 1.4x
- **Mono:** Roboto Mono 400 for code, commands, terminal text
- **Captions:** Inter 600, 56px, white with 2px text-shadow for readability

### Animation Principles

- **Enter animations:** Elements slide in from below (24px travel) with 15-frame ease-out
- **State transitions:** Color morphs over 12 frames (e.g., orange→blue when approval resolves)
- **Continuous motion:** Every scene has at least a slow drift, pulse, or progress indicator
- **Ken Burns:** Only used for the analytics montage (scene 7) — subtle 1.02x zoom over each sub-panel
- **Stagger:** Multi-element entrances use 3-4 frame stagger between items

---

## 8. Soundtrack Plan

### Music

No music track embedded. The video should work as a silent auto-play (social media, README embed) and as a narrated piece. Design for:

1. **Silent mode** (default render): captions carry the narrative
2. **Narrated mode** (future): VO can be layered on top

### Sound Design (optional layer, low priority)

If we add sound effects later:
- Soft UI click sounds on cursor interactions
- Subtle whoosh on scene transitions
- Low ambient hum during execution/monitoring scenes

**Recommendation:** Ship v1 silent. Sound is a polish pass.

---

## 9. Caption Script

Captions appear lower-center, Inter 600, 56px, max 5 words. Timed to scene beats.

| Scene | Time | Caption |
|---|---|---|
| 1: Cold Open | 0:00 | *Your AI coding fleet* |
| 1: Cold Open | 0:01.5 | *Managed from one place* |
| 2: Problem | 0:03.5 | *Five agents, five terminals* |
| 2: Problem | 0:05.5 | *Where's your mental model?* |
| 3: Dashboard | 0:07.5 | *Meet CodePlane* |
| 3: Dashboard | 0:10 | *Every job, every agent* |
| 4: Execution | 0:13.5 | *Watch work happen live* |
| 4: Execution | 0:16 | *Steps complete in real-time* |
| 4: Execution | 0:18.5 | *Full execution timeline* |
| 5: Approval | 0:20.5 | *Dangerous operation detected* |
| 5: Approval | 0:23 | *Nothing slips past you* |
| 5: Approval | 0:25 | *One click to approve* |
| 6: Diff Review | 0:26.5 | *Review every change* |
| 6: Diff Review | 0:29 | *Merge with confidence* |
| 7: Analytics | 0:32.5 | *Track costs per model* |
| 7: Analytics | 0:35 | *Know where money goes* |
| 8: Mobile | 0:37.5 | *Full control from your phone* |
| 8: Mobile | 0:40 | *Same data, any device* |
| 9: Scale | 0:42.5 | *Scale to dozens of agents* |
| 10: Architecture | 0:46.5 | *Plugs into your workflow* |
| 11: Closing | 0:50.5 | *Open source, free forever* |
| 11: Closing | 0:52.5 | *pip install codeplane* |

**22 captions across ~51 seconds = one every ~2.3s. Each ≤5 words. No caption duplicates on-screen UI text.**

---

## 10. Implementation Strategy

### Component Architecture

```
src/
  index.ts                    # registerRoot
  Root.tsx                     # Composition registration
  CodePlaneDemo.tsx            # Top-level TransitionSeries
  constants.ts                 # Timing, colors, layout
  fonts.ts                     # @remotion/google-fonts loader
  captions.ts                  # Timed caption entries
  components/
    AnimatedBackground.tsx     # Gradient mesh + grid overlay
    AnimatedCursor.tsx         # Cursor with click ripples
    CaptionOverlay.tsx         # Lower-center caption renderer
    StateBadge.tsx             # Recreated from frontend  
    SdkBadge.tsx               # Recreated from frontend
    JobCard.tsx                # Static version (no store)
    KanbanColumn.tsx           # Static version
    ExecutionTimeline.tsx      # Static version
    PlanPanel.tsx              # Static version
    ApprovalBanner.tsx         # Static version
    DiffView.tsx               # Simplified diff renderer
    AnalyticsChart.tsx         # Animated bar/donut charts
    SystemDiagram.tsx          # Node-edge architecture diagram
  scenes/
    S01_ColdOpen.tsx
    S02_Problem.tsx
    S03_DashboardReveal.tsx
    S04_LiveExecution.tsx
    S05_ApprovalGate.tsx
    S06_DiffReview.tsx
    S07_AnalyticsMontage.tsx
    S08_MobileShowcase.tsx
    S09_ScaleShot.tsx
    S10_Architecture.tsx
    S11_Closing.tsx
  data/
    mockJobs.ts                # All mock JobSummary objects
    mockTimeline.ts            # Timeline + plan step data
    mockAnalytics.ts           # Chart data
```

### Dependency Changes

**Add:**
- `lucide-react` — for icons matching the real UI (Loader2, Clock, ShieldQuestion, CheckCircle2, etc.)

**Keep:**
- `@remotion/transitions` — scene transitions
- `@remotion/google-fonts` — Inter + Roboto Mono
- `remotion` — core

**Remove if unused after rewrite:**
- `@remotion/gif` — no longer using GIF screenshots
- `@remotion/captions` — using simpler custom caption system
- `@remotion/media` — no audio

### Rendering Approach

Components are **faithfully recreated** in Remotion, not imported from the frontend. This avoids:
- Zustand store dependency
- React Router dependency
- Tailwind CSS build pipeline dependency
- Runtime async calls

Instead, each recreated component uses inline styles matching the exact HSL values and Tailwind class semantics from the real components. The visual output will be pixel-accurate to the real UI.

---

## Open Questions for Review

1. **Duration preference?** Plan targets 50s. Acceptable range is 45-55s. Shorter = punchier.
2. **Analytics charts — SVG or Canvas?** SVG is easier in Remotion. Canvas is smoother for complex animations. Recommend SVG for bar charts, SVG for donut.
3. **Cursor interaction density:** Currently 3 click interactions (scenes 3, 5, 6). Should we add more or is this sufficient?
4. **Architecture diagram complexity:** Current plan shows Developer → CodePlane → Agents → Repos. Should it include MCP server, worktree management, or keep it simple?
5. **Closing CTA:** Currently `pip install codeplane`. Should it also show `uvx codeplane` or a GitHub URL?
