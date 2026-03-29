# Docs Asset Checklist

Internal planning file. Tracks every image and GIF needed across the docs,
README, and GitHub repo page — plus the setup procedure to create realistic
volume for the captures.

## Current State

All existing screenshots under `docs/images/screenshots/` are **1×1 px placeholders**.
Only 3 images are currently referenced by live docs:

| File | Referenced by |
|------|---------------|
| `hero-dashboard.png` | `index.md`, `README.md` |
| `job-running-transcript.png` | `index.md`, `quick-start.md` |
| `job-diff-viewer.png` | `quick-start.md` |

Everything else was deleted. New assets get created when a doc page is wired
to use them.

---

## Capture Setup

The dashboard needs to look like a real operator's workspace, not a toy with 2 jobs.
We use the two demo repos: **demo-issue-tracker-api** (Python/FastAPI) and
**demo-support-dashboard** (React/TS/Vite), both already registered in CodePlane.

### Target scene: 10–12 concurrent/recent jobs across both repos, mixed agents

**Create in this order** so the dashboard fills up naturally and states layer:

| # | Repo | Agent | Prompt | Target state | Notes |
|---|------|-------|--------|--------------|-------|
| 1 | demo-issue-tracker-api | copilot | "Add input validation to the create ticket endpoint and cover it with tests" | **completed** → archive | Provides diff content for diff viewer shot |
| 2 | demo-support-dashboard | claude | "Add a priority badge column to the ticket table with color-coded labels" | **completed** | Shows Claude in the mix |
| 3 | demo-issue-tracker-api | copilot | "Add pagination to the ticket list endpoint with limit and offset query params" | **review** | Stays in review for merge GIF |
| 4 | demo-support-dashboard | copilot | "Add a dark mode toggle that persists the preference in localStorage" | **completed** → archive | Background volume |
| 5 | demo-issue-tracker-api | claude | "Tighten error handling around ticket archival — return proper 404/409 codes" | **review** | Second review-state job visible on dashboard |
| 6 | demo-support-dashboard | copilot | "Persist the selected status filter in the URL query string" | **running** | Live job #1 for transcript streaming capture |
| 7 | demo-issue-tracker-api | copilot | "Add customer email search to the ticket list endpoint and add tests" | **running** | Live job #2 — shows concurrent execution |
| 8 | demo-support-dashboard | claude | "Add keyboard shortcut hints to the search input and status filter" | **running** (approval_required) | Triggers approval banner for capture |
| 9 | demo-issue-tracker-api | copilot | "Return 409 Conflict when archiving an already-archived ticket and add a test" | **queued** | Shows queue when at capacity |
| 10 | demo-support-dashboard | copilot | "Add a loading skeleton to the ticket list while the API request is in flight" | **queued** | Second queued job |

**State distribution on dashboard at capture time:**
- 2 running + 1 paused (approval) — active work
- 2 queued — shows capacity management
- 2 in review — pending operator decisions
- 1 completed (not yet archived) — recent finish (job 2)
- 2 archived (jobs 1, 4) — visible in history

This gives us a full, realistic dashboard with **10 jobs across 2 repos, 2 agents, 4+ states**.

### Settings for captures

```yaml
max_concurrent_jobs: 3         # so queuing is visible
permission_mode: auto          # default; override per-job for approval shots
```

### Capture sequence

1. Create jobs 1–2, let them complete, archive job 1
2. Create jobs 3–5, let 3 and 5 land in review, let 4 complete and archive
3. Set job 8 to `permission_mode: approval_required`
4. Create jobs 6, 7, 8 — they'll run concurrently (max_concurrent=3)
5. Create jobs 9, 10 while 6–8 are running — they'll queue
6. **Capture dashboard hero** now (all 10 jobs visible, mixed states)
7. Click into job 6 or 7 → **capture transcript streaming GIF**
8. Wait for job 8's approval prompt → **capture approval GIF/screenshot**
9. Click into completed job 1's diff → **capture diff viewer**
10. After jobs finish, **capture analytics** (will have real cost/model data from 10+ jobs)
11. Run `cpl info` in terminal → **capture QR code / tunnel URL output**
12. Switch to mobile viewport → **capture mobile shots**
13. Open settings → **capture settings** (2 repos registered, agent defaults)
14. Open command palette, search → **capture command palette GIF**
15. Click Complete on job 3 → **capture merge GIF**

---

## Capture Standard

1. Desktop: **1280×800** viewport, crop browser chrome, **2× retina** export.
2. Mobile: **iPhone 14 Pro** viewport via DevTools, 2× export.
3. GIFs: max **15 seconds**, **12 fps**, 800 px wide (use `gifski` or ScreenToGif for quality).
4. Keep prompts, repo names, branch names consistent — use exactly the prompts above.
5. Prefer full-screen product scenes over cropped fragments.
6. Terminal captures (e.g. `cpl info`): use terminal screenshot tool or DevTools, same 2× retina.

---

## Assets by Capture Step

All assets live under `docs/images/screenshots/`. Desktop in `desktop/`, mobile in `mobile/`.

### Step 6 — Dashboard hero (all 10 jobs visible, mixed states)

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/hero-dashboard.png` | Screenshot | index.md, README | Full dashboard — 10 jobs, mixed states, both repos in cards. This is the anchor visual. |

### Step 7 — Click into running job (job 6 or 7)

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/job-running-transcript.png` | Screenshot | index.md, quick-start.md | Transcript tab, agent reasoning visible, tool call group expanded, cost/token sidebar. |
| `desktop/transcript-streaming.gif` | GIF | guide.md below `### Transcript` | Same job. Entries appearing live, tool call group auto-expanding. 8–10 sec. |
| `desktop/plan-tab.png` | Screenshot | guide.md below `### Plan` | Switch to Plan tab — step list with mixed done/active/pending/skipped indicators. |
| `desktop/metrics-tab.png` | Screenshot | guide.md below `### Metrics` | Switch to Metrics tab — token/cost chart mid-job. |

### Step 8 — Job 8 approval prompt

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/approval-flow.gif` | GIF | guide.md below `### Approval Actions` table | Approval banner appears → click Approve → agent resumes. |
| `desktop/approval-banner.png` | Screenshot | guide.md below `### Permission Modes` table | Static fallback: file path, action type, approve/reject/trust buttons visible. |

### Step 9 — Completed job 1 diff

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/job-diff-viewer.png` | Screenshot | quick-start.md | Diff tab. File tree open, multi-file changes, syntax highlighting. Pick a file with both additions and deletions. |

### Step 10 — Analytics (after all jobs finish)

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/analytics-dashboard.png` | Screenshot | guide.md below `### Analytics` | Scorecard, model comparison (copilot vs claude), cost trend. Real data from 10+ jobs. |

### Step 11 — `cpl info` tunnel output

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/cpl-info-output.png` | Screenshot | guide.md in `## Remote & Mobile Access`, below `cpl info` command block | Terminal showing tunnel URL and QR code. The "how do I get it on my phone" visual. |

### Step 12 — Mobile viewport (same scene, DevTools responsive mode)

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `mobile/mobile-dashboard.png` | Screenshot | guide.md in `## Remote & Mobile Access`, after capability bullet list | 10+ jobs in stacked card layout. |
| `mobile/mobile-job-transcript.png` | Screenshot | guide.md in `## Remote & Mobile Access` | Job 6 or 7 transcript in compact mobile layout. |
| `mobile/mobile-approval.png` | Screenshot | guide.md in `## Remote & Mobile Access` | Job 8's approval prompt with visible tap targets. |
| `mobile/mobile-diff.png` | Screenshot | guide.md in `## Remote & Mobile Access` | Single-column diff view. |
| `mobile/mobile-merge.png` | Screenshot | guide.md in `## Remote & Mobile Access` | Merge controls stacked on mobile. |
| `mobile/mobile-voice-input.gif` | GIF | guide.md in `### Voice Input` or `## Remote & Mobile Access` | ~5 sec: tap mic → waveform animates → tap stop → text appears. |

### Step 13 — Settings

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/settings-page.png` | Screenshot | configuration.md below `## UI Settings` | Both demo repos registered, agent defaults, permission mode selector visible. |

### Step 14 — Command palette

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/command-palette.gif` | GIF | guide.md below `### Command Palette` | Open palette → type "ticket" → jobs filter → navigate to one. |

### Step 15 — Merge flow (job 3)

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/merge-resolve.gif` | GIF | guide.md below `## Merging & Resolution` table | Job 3 in review → click Complete → choose Smart Merge → success. |

### Between steps (capture when convenient)

| Asset | Type | Wire into | What to capture |
|-------|------|-----------|-----------------|
| `desktop/create-job-flow.gif` | GIF | guide.md below `### Job Parameters` table | Create job 9 or 10 — New Job → prompt → repo/agent/model → submit → queues. 10–12 sec. |
| `desktop/terminal-drawer.png` | Screenshot | guide.md below `### Terminal` | Terminal with 2 tabs (global + job-specific), real output. |

---

## Not Needed

- Empty/blank state screenshots — no one cares about an empty dashboard
- Separate logs and timeline tab captures — too niche for docs
- History page screenshot — the list is self-explanatory
- Architecture diagrams beyond existing ASCII art
- Per-agent CLI setup screenshots — handled by external docs (GitHub, Anthropic)
- QR code close-up — the `cpl info` terminal output is sufficient

---

## Completion Checklist

- [ ] Demo repos reset to clean main branches before capture session
- [ ] `docs/images/screenshots/mobile/` directory created
- [ ] All desktop assets captured (16 files: 10 screenshots + 6 GIFs)
- [ ] All mobile assets captured (6 files: 5 screenshots + 1 GIF)
- [ ] 1×1 placeholders replaced — docs pages render real images
- [ ] New assets wired into guide.md and configuration.md (add `<div>` image tags)
- [ ] Mobile shots added to Remote & Mobile Access section
- [ ] README hero image renders on GitHub
- [ ] All GIFs under 2 MB (compress with `gifsicle -O3` if needed)
- [ ] No remaining 1×1 placeholder PNGs in the repo
- [ ] Analytics screenshot shows real cost data from 10+ jobs across both agents
