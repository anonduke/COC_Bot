# Village Automation — Analysis, Architecture & Roadmap

Status: **proposal, awaiting approval**. No code has been changed for this work.
Date: 2026-11-13. Machine tested: DESKTOP-GSD3MHF, LDPlayer 9.1.67, TH18 account.

---

## 0. Summary of the important findings

1. **The official Clash of Clans API can't drive this system.** `GET /players/{tag}` returns profile data: TH level, heroes, troops, spells, pets, equipment, achievements, league. It returns **no building levels, no stored resources, no builder status and no upgrade timers.** Each API key is also tied to one whitelisted IP. It is useful only for side checks, so it should not be on the critical path.
2. **The in-game Data Export is the right data source, and it works here.** I tested it live. Settings → More Settings → *Export Village data in JSON format* → **Copy**. LDPlayer syncs the Android clipboard to Windows, so Python reads the JSON straight from the Windows clipboard: 9.8 KB, no OCR. It contains:
   - every building type with per-level counts (e.g. Wall lvl 19 × 325, which confirms the walls are maxed);
   - **remaining upgrade time in seconds** for buildings, heroes, lab research and the pet house;
   - helper (Builder's Apprentice / Lab Assistant) cooldowns, plus a timestamp.

   The export contains exactly 7 builder jobs (2 buildings and 5 heroes), matching the in-game "0/7". It does **not** contain stored gold, elixir or dark elixir. Those still come from the storage OCR we already have, which is now reliable.
3. **Deep links open game screens directly.** For example, `adb shell am start -a android.intent.action.VIEW -d 'https://link.clashofclans.com/en/?action=OpenMoreSettings' -p com.supercell.clashofclans` lands on More Settings with no menu navigation. This makes navigation much more reliable.
4. **Builders are interchangeable in the game.** You cannot tell "Builder 3" to do something; any free builder takes the next job. Truly independent per-builder queues would not match how the game works. **I recommend one central prioritised plan.** The per-builder "lanes" are then computed from it for display and forecasting, and can optionally be pinned (for example, "one lane always on heroes"). Details in §3.
5. **The export counts buildings by type and level, not by individual building.** Plans must say "upgrade one Archer Tower 16→17", not "the Archer Tower at grid (12,30)". This also matches how the in-game Builder menu lists upgrades, so the bot can upgrade any building through that list without knowing where it sits on the map. That is the same approach `wall.py` already uses for walls.
6. **The emulator itself is the biggest reliability risk.** Today's events:
   - The game was frozen when I first looked; the home screen on display was about 30 minutes old.
   - Android showed "System UI isn't responding" **again immediately after a fresh reboot**.
   - `adb screencap` hangs on this LDPlayer setup.
   - The automatic reboot recovery path ran live for the first time and **did not recover**. It sends `runapp` on a fixed timer without checking that Android has finished booting or that Clash is in front.
   - LDPlayer now shows a floating ad (a separate Windows window, class `adframe`, owned by `dnplayer.exe`) **over the Build button and the builder counter**. Clicks there hit the ad. It can be hidden with Win32 `ShowWindow(hwnd, SW_HIDE)`; I did this by hand.

   A lifecycle manager that starts and stops the emulator per session addresses most of this. It should be built *early*, not at Phase 5.
7. **Event-driven scheduling makes 1–5 hour polling unnecessary.** While the emulator is off, the village can only change in two ways: timers counting down (fully predictable from the export) and enemy raids (which only cost resources). Re-reading the village every few hours would mean booting the emulator just to learn what we already know. Instead, **read the village at the start and end of every session.** Add a configurable "health check" session (e.g. every 12 h) only to catch manual play on another device.

---

## 1. Existing project analysis

### 1.1 What's there

| Area | Where | State |
|---|---|---|
| Attack loop (find match → loot check → deploy → end → return home) | `Ranked_Copy.py` `attack()`, `startAttacking`, `isGoodOpponent`, `deployTroops`, `endBattle`, `finishBattleAndGoHome` | Working. 50/50 attacks in the soak test, ~280 attacks in the wall-max run. |
| Opponent loot OCR | `Ranked_Copy.py` `_loot_digit_mask`, `_classify_loot_digits` + `misc/loot_digits.npz` | Reliable. Per-digit classifier with Tesseract fallback. |
| Storage OCR (own gold/elixir) | `wall.py` `read_storage`, `_storage_mask` | Reliable for gold and elixir. **No dark elixir reader.** |
| Wall upgrades | `wall.py` `upgrade_wall` → builder panel → scan list → Upgrade → Confirm, with popup handling (need-more, builders-busy) | Working; walls are now maxed. |
| Recovery | `recover_to_home`, `restart_game`, `_screen_frozen`, `_close_android_anr`, `_clear_launch_popups` | Home-screen recovery works. **Emulator reboot recovery does not (see §0.6).** |
| Window control | `activate_game_window` / `force_foreground` (duplicated in both files) | Works, but needs an unlocked desktop with LDPlayer in front. |
| Per-machine profiles | `wall.py` `MACHINE_PROFILES` | Coordinates differ per PC. |
| GUI | Tkinter panel in `Ranked_Copy.py` `__main__` | Toggles, settings popup, Start/Stop. 4 h max runtime. |
| Watchdog | `_bot_monitor`, `_update_heartbeat` | Present. |
| Builder Base, donations, best-base finder | various | Out of scope; leave untouched. |

### 1.2 Reusable as-is
- `attack()`, which returns success or failure, plus `recover_to_home()`. These become the farming "unit of work".
- `wall.read_storage` and the digit classifier, for resource checks.
- The builder-panel list scanning and upgrade-dialog handling in `wall.py`. This is the template for a generic "upgrade building X" executor.
- The popup templates in `misc/`.

### 1.3 Limitations and conflicts to design around
- **Module-level globals and side effects.** `CONFIG`, `attack_count` and the window coordinates are globals. The GUI only runs under `__main__`, so `Ranked_Copy` can be imported as a library; the test runners already do this. I will wrap it rather than rewrite it.
- **pyautogui needs the real desktop.** Mouse and screenshots only work if Windows is unlocked, not asleep, and LDPlayer is uncovered. "Runs in the background while nobody watches" therefore means: auto-login, no lock screen, no sleep, and no one using the PC during sessions. Phase 4 will check whether `adb shell input tap` and a background capture method can remove this restriction (see R3).
- **Fixed sleeps instead of state checks** in several places, e.g. `restart_game`'s `sleep(90)`. New code should wait for observable conditions.
- **No persistent state.** Everything resets on restart. Only logs survive.
- **Legacy files** (`Ranked.py`, `Attack*.py`, `coc_bot1.py`, `c.py`, `Ranked_Copy copy.py` …) add clutter. I propose leaving them alone now and archiving them later with your OK.
- **Uncommitted fixes.** The fixes from the last sessions are not committed. The new system should start from a committed baseline (your call).

---

## 2. Data sources

| Need | Source | Notes |
|---|---|---|
| Building/hero/lab/pet levels, active timers, helper cooldowns | **In-game Data Export (clipboard)** | Exact to the second; read at every session start and end. |
| Gold / elixir | Storage OCR (existing) | Read on the home screen. |
| Dark elixir | Storage OCR (**new**: one more region + digit samples) | Needed for hero upgrades. |
| Gems | OCR (**new**) | Used as a safety guard (R7), not for spending. |
| Free builders "x/y" | Builder counter OCR (existing `read_builder_count`) | Cross-checks the export. |
| Upgrade costs, durations, max level per TH | **Static game data** (Phase 2 research) | Options: (a) ClashKing's static data JSON (used by Clash Max); (b) the game's own CSVs extracted from the installed APK. Must map export `data` IDs (e.g. 1000001 = Town Hall, 1000010 = Wall, 1000015 = Builder's Hut) to names, costs and times. |
| Profile cross-check | Official API (optional) | Hero/troop levels only. Possible later "sanity check" feature, not required. |

### Export format observed (top-level keys)
`tag, timestamp, helpers, guardians, buildings, traps, decos, obstacles, units, siege_machines, heroes, spells, pets, equipment, house_parts, skins, sceneries, buildings2, traps2, decos2, obstacles2, units2, heroes2, skins2, sceneries2, boosts`

Building entries look like `{"data": id, "lvl": n, "cnt": k}`, or `{"data": id, "lvl": n, "timer": secs}` while upgrading, plus special forms (`gear_up`, `types`/`modules` for the Crafting Station). Keys ending in `2` are the Builder Base.

### Capturing the export reliably
1. Clear the Windows clipboard, or write a known sentinel to it.
2. Open More Settings with the deep link, scroll to Data Export, tap Copy. Scroll by dragging labels only: **Delete Account is on the same screen**, so taps must be limited to known-safe coordinates on that screen.
3. Read the clipboard, parse the JSON, and check that `tag` matches the configured account and `timestamp` is within the last 2 minutes.
4. Close the dialog and save the raw JSON and the parsed result to the database.

---

## 3. Builder management: recommended model

### 3.1 Why not literal per-builder queues
In the game, any free builder takes any job. With 6 hand-made queues, one queue would run dry while another backs up. The bot would then either idle a builder or have to "borrow" from another queue, which means a central planner anyway. Clash Ninja (Oct 2026 rework) moved the same way: it merged its separate planners into **one unified timeline** whose rows are builder lanes, with drag-and-drop between lanes.

### 3.2 Recommended model: plan + simulated lanes
- **Upgrade Plan**: one ordered list of *plan items*, e.g. `Archer Tower → next level`, `Grand Warden → 75`, `Town Hall 18 → 19`. Each item has a priority, a target level, optional constraints and an "enabled" flag.
  - Constraints include: "not during CWL", "only if Gold ≥ X after upgrade", "lane: heroes", "earliest start".
- **Lanes (builders)**: a forward simulation of the plan from the latest export (Clash Max's approach). Every builder lane has a `busy_until`. At each "builder frees" event, it picks the highest-priority *eligible* item. Lanes are what you see per builder in the dashboard: current job, finish time, next 3 jobs.
- **Pinning**: optionally reserve a lane for a category (e.g. "Lane 1 = heroes only"). This gives you the per-builder control you described without its downside.
- **Manual override**: "next upgrade = X" puts X at the head of the plan for the next free builder.
- **Eligibility** is computed, not hand-maintained:
  - TH level allows the target level;
  - the building is not already at max or upgrading;
  - the cost fits in storage capacity;
  - the resource type is affordable now, or farmable within the configured farming budget.
  - Ineligible items are skipped and marked with a reason, never silently dropped.
- **Completed items** leave the plan automatically when the next export shows the new level.
- **Save and restore**: plans are stored in the database. "Plan templates" can be exported to and imported from JSON files.
- **Presets** (optional, later): "Heroes first", "Defence first", "Max before TH". These are just orderings of the same plan.

### 3.3 Resource-aware ordering (a suggestion you didn't mention)
Loot in storage can be stolen while you're offline, and farming takes real time. So the planner should:
- farm **just before** a builder frees, so you don't sit on resources between sessions;
- prefer the cheapest-to-afford next item when two items have equal priority;
- show **expected farming time** per item, using our measured loot per attack from the logs.

---

## 4. Proposed architecture

Seven components rather than thirteen. Fewer moving parts means fewer ways to break a long-running system.

```
                 ┌──────────────────────────────────────────┐
                 │  Dashboard (local web UI, separate proc)  │
                 │  reads state, writes commands/settings    │
                 └───────────────┬──────────────────────────┘
                                 │ SQLite (WAL)
┌────────────────────────────────▼─────────────────────────────────┐
│ Orchestrator (one long-running process, Windows Task Scheduler)  │
│  • Scheduler: next-event loop over persisted events              │
│  • Session runner: state machine per session                     │
│     BOOT → SYNC_IN → UPGRADE ↔ FARM → SYNC_OUT → SHUTDOWN         │
│  • Planner: plan + lane simulation (pure, no I/O)                │
│  • Action journal: every action intent→done/verified/failed      │
└──────┬───────────────┬───────────────────┬───────────────────────┘
       │               │                   │
┌──────▼─────┐  ┌──────▼────────┐   ┌──────▼──────────────────────┐
│ Emulator   │  │ Game driver   │   │ Village state               │
│ manager    │  │ (vision+input)│   │ export parser, static data, │
│ ldconsole, │  │ home check,   │   │ resource OCR, builder calc  │
│ adb, ANR,  │  │ export, nav,  │   └─────────────────────────────┘
│ ads, boot  │  │ upgrade exec, │
└────────────┘  │ farming =     │
                │ Ranked_Copy.  │
                │ attack() (as- │
                │ is, wrapped)  │
                └───────────────┘
```

| Component | Responsibility | Built from |
|---|---|---|
| **Emulator manager** | Is LDPlayer running? Start it; wait for `sys.boot_completed`; hide the `adframe` ad window; close ANRs; launch Clash (adb intent, not a fixed sleep); confirm Clash is the resumed activity (`dumpsys`); shut down; hard reboot. | `ldconsole`, `adb`, Win32. Replaces `restart_game`. |
| **Game driver** | Screen recognition (home, popups, ads, Welcome back), deep-link navigation, data export, generic "upgrade X via builder list", resource OCR, gem guard. Farming calls the existing `attack()`/`recover_to_home()`. | `wall.py` and `Ranked_Copy.py` logic, wrapped in an adapter. No rewrite. |
| **Village state** | Parse the export, map IDs to names via static data, compute builder jobs and free times, store snapshots, diff old vs new (used for verification). | New, pure Python, fully unit-testable. |
| **Planner** | Plan items, eligibility, lane simulation, farming targets per upcoming item. | New, pure, unit-testable. |
| **Scheduler / session runner** | Persisted events (`builder_free`, `health_check`, `retry_after_failure`); groups events within a window into one session; runs the session state machine; resumes after a crash. | New. |
| **Store** | SQLite tables: snapshots, plan items, settings, events, sessions, action journal, notifications. | New. |
| **Dashboard + notifier** | Local web UI; alerts for critical failures. | New, separate process. |

### 4.1 Key decisions and why

- **Database: SQLite** (Python stdlib, WAL mode). One writer (the orchestrator) plus a reader (the dashboard). There are only tens of events a day. Nothing bigger is justified; it's a single file to back up.
- **Scheduler: a small custom next-event loop**, not APScheduler or Celery. The loop works like this:
  - Events live in SQLite.
  - The loop sleeps until the earliest due event.
  - On wake it **recomputes from stored state**, so a missed or late wake-up is harmless.
  - A restart only re-reads the table.
  - The process is started at logon by **Windows Task Scheduler**, with restart-on-failure.
- **Session grouping**: when an event is due, gather every builder that frees within *N* minutes (default 45; configurable) into the same session. Also merge sessions that would start within *M* minutes of each other. This also applies "sleep / active hours" windows (Clash Ninja and Clash Max both offer these). The scheduler can optionally add random jitter to session start times.
- **Configuration**: three layers.
  1. Defaults in code.
  2. **Permanent** settings in the SQLite `settings` table, edited from the dashboard.
  3. **Temporary** overrides held in memory for the current run only, shown as "temporary" in the UI.

  Per-machine coordinates stay in `wall.py` profiles.
- **Dashboard: local web UI** (FastAPI or Flask, server-rendered pages + SortableJS for drag and drop), on `localhost` and optionally your LAN. It is a separate process from the bot, so a UI crash can't kill the bot, and you can check it from your phone. The existing Tkinter panel stays for manual attack/wall mode.
- **Notifications**: Telegram bot or ntfy.sh push. That means sending data (status text only) to an external service, so I won't set this up without your go-ahead.

### 4.2 Reliability rules

1. **Journal before acting, verify after.** Every action that changes the game is first written to the journal as `INTENT` (e.g. "upgrade Archer Tower 16→17, session 42"). It is then executed, and **verified by a fresh export**. The export must show a new `timer` on that type, or one more at the next level.
   - Only then is it marked `VERIFIED`.
   - On restart, any `INTENT` without a `VERIFIED` result is resolved by **re-exporting first, never by repeating the action**.
   - This is the "never blindly repeat" requirement, and the export makes it cheap.
2. **Gem guard.** Read gems at session start and after every upgrade. Any drop aborts the session and sends an alert.
   - The bot never taps a gem-priced button; the "builders busy" popup is closed with its X.
   - Gems went 5,130 → 5,092 between the end of my last run and today, while the bot was off. That was presumably you, but it shows why a guard that would catch it automatically is worth having.
3. **Every wait has a timeout and an observable exit condition.** Examples: "Clash is resumed activity", "home Battle button visible", "export timestamp fresh".
4. **Bounded retries with escalation.** Retry the step, then restart Clash, then reboot the emulator, then abort the session, notify you and back off (e.g. 30 min, then 2 h).
5. **State reconciliation.** If the export disagrees with the database (you played manually, a timer finished early because of a helper or Builder Potion, a different builder count), **the export wins**. Plans are recomputed and the difference is logged.
6. **Screenshots on every failure**, plus a rolling log per session, are kept for 14 days.

---

## 5. Roadmap (re-ordered)

I've re-ordered your phases in two ways:
- **Emulator lifecycle moves up.** Every later phase depends on it, and it's the least reliable part today.
- **A dedicated "upgrade executor" phase is added.** Upgrading arbitrary buildings is the hardest new game-interaction problem and was missing from the list.

Each phase ends with written test results and an acceptance sign-off from you.

### Phase 0: Baseline (small)
- **Objectives:** a committed, known-good baseline; LDPlayer health.
- **Tasks:**
  - You decide whether to commit the existing fixes.
  - Tune the LDPlayer instance (CPU/RAM allocation, update LDPlayer, check ad and notification settings) to reduce System UI ANRs.
  - Record a screenshot corpus of every popup seen so far.
- **Acceptance:** 2 h idle on the home screen without an ANR, or ANR frequency measured and logged.

### Phase 1: Analysis (this document)
- **Acceptance:** you approve the architecture and phase order.

### Phase 2: Village state and static data
- **Objectives:** capture the export, parse it, map IDs, OCR resources (adding DE and gems), store snapshots in SQLite.
- **Tasks:**
  - Choose and verify the static data source against the live export: every ID in your export must resolve to a name and a level table.
  - Write the export capture flow with safe-tap rules.
  - Implement a snapshot diff.
- **Tests:**
  - Unit tests on saved exports (fixtures), including the special entry types (`gear_up`, `types`/`modules`, `helper_cooldown`).
  - 20 live captures in a row with 100 % success.
  - DE and gem OCR checked against 30 screenshots.
- **Acceptance:**
  - Every ID maps to a name.
  - Capture is 20/20.
  - The resource OCR error rate is 0 on the test corpus.

### Phase 3: Builder tracker
- **Objectives:** current jobs, free times and builder count from the snapshot; next-free forecast.
- **Tests:**
  - Unit tests with a fake clock.
  - The computed "free builders now" must match the in-game counter on 10 live checks.
  - Predicted completions are checked against the real ones over 24 h. Tolerance is ±2 min, or flagged with the reason (helper, potion, manual).
- **Acceptance:** forecasts within tolerance; any mismatch is explained in the log.

### Phase 4: Emulator lifecycle manager (moved up)
- **Objectives:** cold start → village ready → shutdown, unattended.
- **Tasks:**
  - Boot detection via adb.
  - Win32 hiding of the `adframe` ad and the launcher top-up ad.
  - ANR closing.
  - Intent launch plus resumed-activity check.
  - Welcome/ad popup clearing.
  - Graceful shutdown.
  - Replace `restart_game` with this.
  - **Spike:** test `adb input tap` and background window capture as a replacement for real mouse/desktop control (R3).
- **Tests:** 50 cold start/stop cycles, plus fault injection:
  - kill `dnplayer.exe` during boot;
  - kill Clash during load;
  - wifi off (`adb shell svc wifi disable`) → "connection lost" screen;
  - an ANR while booting.
- **Acceptance:** ≥ 49/50 cycles reach the village unattended; every failure ends in a clean abort plus a notification, never a hang.

### Phase 5: Upgrade executor
- **Objectives:** "upgrade one `<type>` to the next level" via the Builder menu list, generalising `wall.py`.
- **Includes:**
  - OCR of the list rows (name, level, cost);
  - scrolling;
  - the upgrade dialog;
  - need-more and builders-busy popups;
  - **dry-run mode** (opens the dialog and reads the cost, does not Confirm);
  - verification through the export diff.
- **Tests:**
  - Dry runs on 15 different building types and all heroes.
  - 5 real upgrades, each verified by export.
  - The "not enough resources" and "no free builder" paths.
- **Acceptance:** 100 % correct target selection in dry runs; no unverified action left in the journal.

### Phase 6: Planner and plan management
- **Objectives:** plan items, eligibility, lane simulation, overrides, templates.
- **Tests:**
  - Unit tests for many scenarios: simultaneous frees; ineligible items; storage capacity; TH caps; pinned lanes; manual override.
  - A golden-file test: a given export + plan must produce a known schedule.
- **Acceptance:** simulated schedules match hand-calculated expectations; there is no state where a free builder has an eligible item but stays idle.

### Phase 7: Farming integration
- **Objectives:** the farming controller sets targets from the next N plan items (cost + safety margin, capped by storage) and runs `attack()` until they are met or a budget is reached.
  - Budgets: max attacks, max minutes.
  - The storage-full check is reused.
- **Tests:**
  - Simulated targets.
  - Live: farm to a 3 M gold target and stop within one attack of reaching it.
  - Budget exhaustion → the session ends cleanly and is retried later.
- **Acceptance:** targets met without over-farming beyond one attack; no change to `attack()` behaviour.

### Phase 8: Scheduler and orchestrator
- **Objectives:**
  - the full loop: IDLE → event → session → IDLE;
  - session grouping, active hours, jitter;
  - crash-resume via the journal;
  - Task Scheduler setup.
- **Tests:**
  - Fake-clock simulation of a week.
  - Kill the orchestrator mid-upgrade and mid-farm, then confirm a correct resume with no duplicate action.
  - Reboot Windows during IDLE.
- **Acceptance:** 72 h unattended live with zero duplicate actions, zero gem spend, and every builder idle-gap explained.

### Phase 9: Dashboard and notifications
- **Pages:**
  - Overview (TH, resources, builders, timers);
  - Plan editor with drag-and-drop and lanes timeline;
  - Farming settings;
  - Automation settings (permanent vs temporary);
  - Session history with screenshots;
  - Manual controls (pause, run session now, skip item).
- **Acceptance:** every setting round-trips and survives restart; manual pause takes effect within one step.

### Phase 10: Long-run hardening
- A 7-day soak test, a failure-statistics review, and tuning of timeouts and grouping windows.

---

## 6. Risks and limitations

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | **Account ban.** Automation breaks Supercell's Terms of Service. 24/7 unattended, regular patterns raise detection risk. | Account loss | Your decision. Use active hours, jitter, realistic attack pacing and caps on daily attacks. |
| R2 | **Emulator instability** (System UI ANR, frozen frames, screencap hang) | Stuck sessions | Short sessions with a fresh boot; frozen-frame and ANR detection; escalation; Phase 0 tuning. |
| R3 | **Needs the desktop unlocked and LDPlayer uncovered** | No true background run; PC unusable during sessions | Phase 4 spike: `adb input` for taps + window capture without focus. Otherwise: dedicated PC, auto-login, no sleep or lock. |
| R4 | **Game updates** change the UI, the templates, the export format or the deep links | Silent breakage | Template-confidence monitoring; schema check on the export; a "degraded → pause + notify" mode. |
| R5 | **Static data lags** new game levels | Wrong costs or eligibility | Fall back to costs read from the upgrade dialog (Phase 5 reads them anyway); the export remains the truth for levels and timers. |
| R6 | **Raids while offline** steal stored loot | Slower progress | Farm right before upgrades (§3.3); keep resources low between sessions. |
| R7 | **Gem spend by misclick** | Real money | Gem guard (§4.2.2); safe-tap allowlist on dangerous screens (Delete Account is on the export screen). |
| R8 | **Clipboard conflicts**: you copy something at the same moment | Bad or stale export | Sentinel + tag + timestamp freshness check; retry. |
| R9 | **Builder count varies** (Goblin Builder events, B.O.B, helpers) | Forecast drift | The export plus the in-game counter are the truth; recompute every session. |
| R10 | **Walls are maxed** → surplus gold/elixir has no sink | Over-farming wasted | Farming targets driven by the plan, not "until full". |

---

## 7. Testing strategy

- **Pure logic (no emulator):** pytest for the export parser, static data mapping, builder tracker, planner, scheduler and journal recovery. Use a **fake clock** and **saved real exports** as fixtures. These run in seconds and gate every change.
- **Vision tests:** a growing corpus of real screenshots (home, popups, ads, ANR, loading, connection lost, results). Every recogniser must classify the corpus correctly. Each new failure screenshot from live runs gets added.
- **Live dry runs:** the full flow with Confirm disabled, to check navigation without spending anything.
- **Fault injection:** kill processes, turn wifi off, inject an ANR, change resources between sessions by hand, fake clipboard content, and a Windows reboot. Each scenario has an expected outcome written down before it runs.
- **Soak tests:** 72 h (Phase 8) and 7 days (Phase 10), measuring:
  - success rate per step;
  - builder idle time;
  - farming efficiency;
  - number of manual interventions (target: zero).
- **Regression guard for existing features:** the existing 50-attack soak runner is re-run after any change that touches shared code. Attack and wall behaviour must stay unchanged.

---

## 8. Sources
- Clash Ninja: [Upgrade Tracker overview](https://clash.ninja/upgrade-tracker/overview), [planner rework (Oct 2026)](https://www.clash.ninja/blog/clash-ninja-planner-rework-oct-2026), [one-tap village updating](https://www.clash.ninja/blog/one-tap-village-updating-is-here), [loot planner](https://www.clash.ninja/blog/loot-planner-enters-early-access)
- Clash Max scheduler: [home](https://clashscheduler.pages.dev/), [about / algorithm](https://clashscheduler.pages.dev/about)
- Official API models via wrappers: [coc.py Player model](https://cocpy.readthedocs.io/en/latest/models/players.html), [coc-rs API source](https://docs.rs/crate/coc-rs/latest/source/src/api.rs), [API polling notes](https://guildorder.com/games/coc/wiki/the-official-api-and-polling)
- Live verification on this machine: Data Export → clipboard, deep link to More Settings, `adframe` ad window, ANR after reboot, `adb screencap` hang.
