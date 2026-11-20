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

### Phase 0 findings (2026-11-14)
- **Host is CPU-bound.** The PC has an i3-6006U (2 cores / 4 threads) and Intel HD 520 graphics. With the game idle, host CPU sat at 100%: AnyDesk ~65% and the emulator (`Ld9BoxHeadless`) ~65%, roughly half each. AnyDesk encodes the screen while a remote session is open. The LAN dashboard (Phase 9) removes the need to keep AnyDesk connected.
- **Emulator tuning.** I ran `ldconsole globalsetting --fps 30 --audio 0`. It turned out that sets only the global default (`leidians.config` `framesPerSecond`); the instance keeps its own `basicSettings.fps: 60` in `leidian0.config`. I tried 30 on the instance and went back to 60 when chasing the display problem below; fps was not the cause. Resolution and window size are unchanged, because the bot's coordinates depend on them.
- **Likely root cause of the freezes: Windows turns the display off** (idle run 2 confirms or refutes). The power plan switches the display off after 5 minutes without input.
  - With no active display, the Intel GPU stops presenting the emulator's OpenGL frames. LDPlayer's own window chrome still repaints, so it looks alive, but the Android picture stops.
  - Most likely, Android then waits on frames that never complete: System UI can't draw → "System UI isn't responding", and after longer idle the whole graphics stack hangs. (The mechanism is inferred; the symptoms and the fix below are observed.)
  - The 2 h idle test showed exactly this: fine for 80 min, then at 10:39 a System UI ANR, `SurfaceFlinger: Timed out waiting for hw vsync`, `WATCHDOG KILLING SYSTEM PROCESS … ThreadedRenderer`, and `surfaceflinger` stuck in uninterruptible sleep. Only an emulator restart recovers from that.
  - Reboots with the display still off came back to LDPlayer's boot screen frozen at "94%", even though adb showed Android fully booted with Clash in front. Forcing the display on (`SetThreadExecutionState(ES_DISPLAY_REQUIRED)` plus one key press) made the real picture appear immediately.
  - This would also explain why the attack loop has been stable: its clicks count as input and keep the display awake. Idle time between sessions does not.
  - **Fix:** the bot process holds `ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED` for as long as it runs (no system setting change needed). Alternatively, set the display timeout to "never" in Windows power options.
- **System UI hang on boot** (every boot today). Detection without screen reading: `dumpsys window | grep mCurrentFocus` contains `Application Not Responding: com.android.systemui`. My first fix was `adb shell "su 0 kill -9 <pid of com.android.systemui>"`. **Later in Phase 2 this turned out to keep the problem going**: press Wait instead (see Phase 2 progress).
- **Frozen-picture detection.** adb alone can't see this failure: Clash can be focused and running while the picture is frozen. Compare two window captures a few seconds apart; the village always animates, so zero changed pixels means frozen.
- **Boot timing:** `ldconsole launch` → `sys.boot_completed=1` in about 59 s. Launching via `adb shell monkey -p com.supercell.clashofclans -c android.intent.category.LAUNCHER 1` gives `GameApp` focus within 5 s.
- **LDPlayer ad windows** are top-level Win32 windows owned by `dnplayer.exe`. Seen so far: classes `adframe` (side panel) and `ADFullScreenFrame` (full-screen promo). Both hide with `ShowWindow(hwnd, SW_HIDE)`. The in-game "Top-Up" ad is drawn inside the emulator and is closed with the existing `misc/ld_ad_close.png`.
- **The game's own data tables are readable** (this decides the Phase 2 data source).
  - They sit in `split_install_time_asset_pack.apk` → `assets/logic/*.csv`.
  - Each file has a `Sig:` header (64 bytes) followed by LZMA with a 4-byte size field, which the Python stdlib `lzma` decodes.
  - `buildings.csv` has `GlobalID`, which matches the export's `data` IDs exactly: every ID in the live export resolved. It also has per-level `BuildTimeD/H/M/S`, `BuildResource`, `BuildCost` and `TownHallLevel`.
  - `heroes.csv`, `townhall_levels.csv` (max count/level per TH) and `deeplinks.csv` are also available.
  - This data updates itself with every game patch: re-extract when `versionName` changes (now 18.647.5).
- **Deep links available** (from `deeplinks.csv`): `Home`, `OpenAttackScreen`, `OpenHeroHall`, `OpenLaboratory`, `OpenPetHouse`, `OpenBlacksmith`, `OpenMoreSettings`, `SelectGameObject(name)` and more. `SelectGameObject&name=Archer Tower` had no visible effect in a first test; this is for Phase 5 to investigate.
- **"Anyone there? You have been disconnected due to inactivity"** appears after idling on the home screen. It dims the screen, so every OCR read returns 0. Sessions must clear it before reading anything; the existing watchdog handles it during attacks.
- **The village changed while the bot was off** (between 2026-11-13 23:20 and 2026-11-14 ~09:00):
  - gold 23.69M → 21.46M;
  - elixir 24.11M → 10.95M;
  - dark elixir 370K → 324K;
  - gems 5,092 → 4,875.

  These changes are from manual play on another device or raids. The design must expect this: the export always wins (§4.2.5).

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
| Upgrade costs, durations, max level per TH | **The game's own CSVs, extracted from the installed APK** (verified, see Phase 0 findings) | `GlobalID` matches the export's `data` IDs exactly (1000001 = Town Hall, 1000010 = Wall, 1000015 = Builder's Hut). Re-extract automatically when the game version changes. No third-party data source needed. |
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
| **Emulator manager** | Keep the Windows display awake; is LDPlayer running? Start it; wait for `sys.boot_completed`; hide the `adframe` ad window; close ANRs; detect a frozen picture; launch Clash (adb intent, not a fixed sleep); confirm Clash is the resumed activity (`dumpsys`); shut down; hard reboot. | `ldconsole`, `adb`, Win32. Replaces `restart_game`. |
| **Game driver** | Screen recognition (home, popups, ads, Welcome back), deep-link navigation, data export, generic "upgrade X via builder list", resource OCR, gem guard. Farming calls the existing `attack()`/`recover_to_home()`. | `wall.py` and `Ranked_Copy.py` logic, wrapped in an adapter. No rewrite. |
| **Village state** | Parse the export, map IDs to names via static data, compute builder jobs and free times, store snapshots, diff old vs new (used for verification). | New, pure Python, fully unit-testable. |
| **Planner** | Plan items, eligibility, lane simulation, farming targets per upcoming item. | New, pure, unit-testable. |
| **Scheduler / session runner** | Persisted events (`builder_free`, `health_check`, `retry_after_failure`); groups events within a window into one session; runs the session state machine; resumes after a crash. | New. |
| **Store** | SQLite tables: snapshots, plan items, settings, events, sessions, action journal, notifications. | New. |
| **Dashboard + notifications** | LAN web UI with actions; notifications area for errors and alerts. | New, separate process. |

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
- **Dashboard: local web app on your LAN** (decided 2026-11-14). FastAPI or Flask, server-rendered pages + SortableJS for drag and drop. Reachable from another PC or a phone on the same network. It is a separate process from the bot, so a UI crash can't kill the bot. It **takes actions** too (pause, resume, run a session now, skip an item, edit the plan); those go through the store as commands that the orchestrator picks up at its next safe step, never by driving the game directly. No login screen is planned for a home LAN; a simple shared token can be added if you want one. The existing Tkinter panel stays for manual attack/wall mode.
- **Notifications: a notifications area in the web page** (decided 2026-11-14). Errors, aborted sessions, gem-guard trips and recoveries are stored in SQLite with severity and a screenshot link, shown newest-first with unread counts, and can be acknowledged. No external notification service.

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
- **Result (run 1, display allowed to turn off):** failed at 80 min with a full graphics hang; cause found (see Phase 0 findings). Run 2 holds the display on.
- **Result (run 2, display held on, 2026-11-14 11:35–13:35):** no graphics hang in 120 min; the game kept rendering throughout. The System UI dialog appeared after 3 min and stayed until cleared; the game kept running behind it.
- **Signed off 2026-11-14** on these terms: the full hang is prevented by keeping the display awake; the System UI dialog is handled by detection + automatic handling, not cured (handling changed to "press Wait" in Phase 2).

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

#### Phase 2 results so far (2026-11-14)
- Every ID maps to a name: **yes**, except decorations (cosmetic, no table).
- Live capture: **20/20** (41–52 s each), after switching the System UI handling to "Wait". The first run of 20 (still restarting System UI) was 19/20.
- Resource OCR: **20/20** home screenshots read correctly (gold, elixir, dark elixir, gems), and dimmed screens read as unknown. *Open:* all 20 had the same values; the corpus needs varied numbers, which farming sessions will provide.

#### Phase 2 progress (2026-11-14)
Code: `village/` package, tests in `tests/` (`python -m unittest discover -s tests -t .`). Commands: `python -m village static | capture [--repeat N] | show`.
- **Static data** (`village/gamedata.py`): decoded from the installed APK into `data/static/<game version>/` (not committed). Export IDs are the `GlobalID` column where a table has one (buildings, characters, spells, traps: *not* in row order), otherwise class × 1,000,000 + named-row index. English names come from `localization/texts.csv` + `texts_patch.csv`. **Every ID in the live export resolves** except decorations (no table in this asset pack; cosmetic).
- **Export parser** (`village/export.py`): entries, job queues (builder, lab, pet house, Builder Base), helper cooldowns, Crafting Station modules. The fixture export gives 7 builder jobs = the in-game 0/7.
- **Diff** (`village/diff.py`): started / finished / leveled (instant, e.g. walls) / unexplained.
- **Store** (`village/store.py`): SQLite WAL, `snapshots` table with OCR resources, schema versioning.
- **Capture** (`village/capture.py`): runs entirely through adb, so the LDPlayer window may be covered and the mouse is never used.
  - `adb exec-out screencap` works once the display is kept awake. Raw format takes ~1 s; PNG takes ~10 s on this emulator.
  - The game ignores fast swipes; slow 800 ms drags scroll ~400 px each, and each `input` call costs ~3 s. The list needs 8 drags; they run in one adb call, then the row position is checked.
  - Taps only Copy and Close, each placed relative to a template match that must be at its expected position.
  - Your clipboard is saved first and restored after.
  - One capture takes about 50 s.
- **Resources** (`village/resources.py`): gold, elixir, **dark elixir and gems** read from the adb screenshot with `wall.py`'s digit matcher. A screen dimmed by a popup reads as "unknown", never as a number.
- **System UI dialog: press Wait, never restart System UI.**
  - The logged reason is a 10 s timeout on the per-minute clock broadcast.
  - The ANR traces show a *freshly started* System UI stuck in its start-up, waiting on `system_server`. There, LDPlayer's own code (`LdUtils.get_config_value`, called from `PackageManagerService.checkUidPermission`) holds a lock and parses a config string on every permission check.
  - So each restart (our adb kill, or "Close app") produces a new slow start-up, which misses the next clock tick and stalls again. During the first 20-capture run, which killed System UI at every start, it stalled **every minute**.
  - Pressing **Wait** instead let the same System UI process finish starting: no new stall in the following 4 minutes.
  - `village.adb.dismiss_systemui_anr` presses Wait, guarded by a template of the dialog. The capture calls it whenever the expected screen is not found.
  - I briefly set `hide_error_dialogs=1` (it makes Android kill instead of asking) and reverted it the same day, because it would automate the restart loop.

### Phase 3: Builder tracker
- **Objectives:** current jobs, free times and builder count from the snapshot; next-free forecast.
- **Tests:**
  - Unit tests with a fake clock.
  - The computed "free builders now" must match the in-game counter on 10 live checks.
  - Predicted completions are checked against the real ones over 24 h. Tolerance is ±2 min, or flagged with the reason (helper, potion, manual).
- **Acceptance:** forecasts within tolerance; any mismatch is explained in the log.

#### Phase 3 progress (2026-11-14)
- `village/builders.py`: forecast (free now, next free, per-job end), Builder's Apprentice boost model, `verify_series` (drift / early / late per job). `python -m village builders | verify`.
- **Total builders come from the in-game counter.** The 7th builder is an "extra" builder: a job on it carries `extra: true`, but while it is idle nothing in the export shows it, so huts + B.O.B alone undercount (6).
- `extra: true` on a lab job = the extra lab slot.
- Builder's Apprentice level L: `BoostMultiplier` L for `BoostTimeSeconds` 3600, i.e. (1 + L)× speed for an hour. The rest between boosts is not in the tables; 23 h is assumed until live data confirms it.
- The counter "x/y" is read from the same home screenshot as the resources; every capture compares it with the computed free count.
- `village/home.py` gets the game to its home screen before each capture: System UI dialog → Wait; "Anyone there?" → RELOAD GAME; Welcome back / LDPlayer top-up ad closed; restart Clash as a last resort (unknown screens saved to `data/screens/unknown/`).
- A file lock (`data/capture.lock`) makes sure only one process drives the game.
- 24 h run started 2026-11-14 16:33 (hourly captures). The first attempt at 16:18 failed: "Anyone there?" then black frames during the restart. Both are now handled.

### Phase 4: Emulator lifecycle manager (moved up)
- **Objectives:** cold start → village ready → shutdown, unattended.
- **Tasks:**
  - Hold the Windows display awake for the whole time the bot runs.
  - Frozen-picture detection (two captures, zero change → restart the emulator).
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

#### Phase 5 progress: dry run (2026-11-14, started early at your request)
`village/upgrader.py`, `python -m village dryrun --queue | --name NAME --from LEVEL`, and a "Dry run next step" button on the dashboard's queue page. Results (rows opened, dialog titles, screenshots) appear under Alerts.
- **How it works:**
  1. Home screen.
  2. Tap the builder counter to open the **Buildings & Heroes** panel.
  3. Read the row titles with Tesseract; in-progress rows (green bars) are skipped.
  4. Tap a matching row: the game opens **"Upgrade <name> to Level N?"** directly.
  5. Read the title, save a screenshot, close with X.
  6. Wrong level: keep looking. The panel has one row per level group, e.g. your Wizard Towers at 13 and 8 are separate rows.
- **Dry run never confirms.** There is no function in the module that taps Confirm; a test checks this.
- **Live results:**
  - Wizard Tower 8 → 9 was found in all four runs. Each time it first opened the level-13 tower's dialog ("to Level 14"), rejected it, then found "to Level 9". The price was 520,000 gold (1,300,000 before an event discount), 9h 30m instead of 1d.
  - Dragon Duke 23 → 24 was found. Its price was shown in **red** (184,000 dark elixir vs 163,132 owned); the dry run now reports that as "not enough resources".
  - Gold, elixir, dark elixir, gems and the builder counter were unchanged after every run.
- **What the dialog offers:**
  - Two Confirm buttons: resources, and a magic item (Hammer of Building / Hammer of Heroes).
  - Prices can be discounted by events, so the dialog, not the game tables, is the truth for costs.
  - Reading the price digits (white or lime on green) is not solved yet; screenshots are kept instead.
- **Panel scrolling is the hard part.**
  - adb drags that start on a row act as a **tap** on that row: they open its dialog. Live, a Crafting Station dialog opened with an option pre-selected and Confirm showing. Nothing was confirmed; it was closed with X.
  - Mouse-wheel events via `sendevent` (touchscreen and mouse devices) did not scroll.
  - Drags now start in the gap between two rows, on a settled frame, re-checked just before. About one drag per run still lands as a tap; a dialog check after every drag closes it.
- **Before the confirming version (5b):**
  - re-verify the dialog title on the exact frame Confirm is tapped;
  - refuse red prices;
  - detect the "need more" and "builders busy" offers (wall.py's lessons);
  - verify through the export diff afterwards.

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

#### Early piece, at your request (2026-11-14): farming from the dashboard
- **Where:** the dashboard's **Attack** page (`village/farm.py`). It runs the existing `Ranked_Copy.attack()` unchanged.
- **Goals:**
  - until storages are full (gold, elixir, both or either; read from the bar fill);
  - N attacks;
  - until gold/elixir/dark elixir reach set amounts.
- **Always-on limits:** max attacks, max minutes, and 6 failed attacks in a row. Options: ranked, event troops, walls every N attacks.
- **Stop:** honoured between attacks, never mid-battle.
- **Visibility:** progress per attack (resources, bar fill, gains) and a run history; the result also goes to Alerts.
- **Game lock:** a run holds the capture lock, so captures and dry runs wait.
- **Dead runs:** a run with no progress for 20 min is marked failed.
- **Limitation:** `Ranked_Copy` uses the real mouse on the LDPlayer window, so the window must be visible and the PC unused while farming.

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
- **Served on the LAN** so it works from another PC or a phone; actions go through the store as commands (§4.1).
- **Notifications area** on every page: errors, aborted sessions, gem-guard trips, recoveries; acknowledge to clear.
- **Pages:**
  - Overview (TH, resources, builders, timers);
  - Plan editor with drag-and-drop and lanes timeline;
  - Farming settings;
  - Automation settings (permanent vs temporary);
  - Session history with screenshots;
  - Manual controls (pause, run session now, skip item).
- **Acceptance:** every setting round-trips and survives restart; manual pause takes effect within one step.

#### Built early, at your request (2026-11-14)
`python -m village web` → `http://<PC>:8765/`. Standard library only (no packages to install).
- **Dashboard:** resource tiles with 72 h sparklines and the change vs about a day earlier; builders free / total with live countdowns and progress bars; a 72 h builder timeline; lab, pet house and helpers; village facts; free builders over time; latest alerts.
- **Builder queue:** catalog of everything upgradable (levels, max for TH/Hero Hall, next cost and time) and an ordered queue (drag-and-drop or arrows, target level, enable/disable).
  - **Projected schedule** across the builders. This is a preview of the Phase 6 planner: queue order is priority, one upgrade per building or hero at a time, no resource limits yet.
- **Settings:** all permanent settings with validation; each shows whether it is in effect now or which phase uses it.
- **Alerts:** failed captures, counter mismatches and dashboard errors, with mark-as-read.
- **Capture now** button (respects the capture lock), emulator status pill, dark and light themes, phone layout.
- POSTs need the `X-Village: 1` header, so other websites can't trigger actions. There is no login (home LAN only).
- Remaining for Phase 9: session history with screenshots, pause/skip controls wired to the orchestrator (Phase 8).

### Phase 10: Long-run hardening
- A 7-day soak test, a failure-statistics review, and tuning of timeouts and grouping windows.

---

### Status after the 2026-11-16 build (Phases 4–9)
Everything below runs as one service: `python -m village serve` (dashboard + orchestrator, each restarted if it exits). It is started at logon by `python -m village install-autostart`, a script in the user's Startup folder that needs no admin rights.

**Phase 4, emulator manager** (`village/emulator.py`): start / stop LDPlayer, System UI dialog → Wait, hide ad windows, launch Clash, home screen, frozen-picture check (one restart).
- Live: 3/3 cold starts from fully off reached the home screen in 172–197 s.
- Found while testing: relaunching too soon after a quit left LDPlayer without its network helper (`VBoxNetNAT`), so adb could not connect. `stop()` now waits until LDPlayer has really exited, and `start()` restarts once if adb never connects.
- A cold game load takes over a minute, so the first home-screen wait after a launch is 3 min.

**Phase 5b, real upgrades** (`upgrader.upgrade`). Confirm is tapped only when all of these hold on the same frame:
- the dialog X is in place;
- the title says exactly `<name>` and `Level <from+1>`;
- the price is not red;
- the resource Confirm button is in place;
- the builder counter shows a free builder.

Never the magic-item Confirm, never a gem button. Anything unexpected after Confirm is dismissed with Android BACK. Success is decided by a fresh export: the diff must show the upgrade started.
- Game 19.32.4: Tesseract read "Level 10?" as "107". Levels are now read from the title's yellow glyphs with the game-font digit matcher, "?" dropped first.
- Drags in the panel can still act as taps on a row: from the gap between rows up to 1 in 2, ending off the panel 1 in 10. Such a dialog is closed at once and can never be confirmed (the title check fails).
- Row identity: the old 12×4 fingerprint merged "Wizard Tower 13→14" with "9→10". It now compares the full time/cost crop: same row 0–0.7, different ≥ 5.5, threshold 3.
- Live dry run on 19.32.4: Wizard Tower 9→10 found after correctly rejecting the 13→14 row.
- **Not yet done live:** no free builder all evening (next 00:13), and live mode is the user's switch.

**Phase 6, planner** (`village/planner.py`):
- Queue order is priority. Per item, the lowest idle copy below target; busy items are skipped, never blocking.
- Affordability is taken from the tables as an upper bound; the dialog decides.
- A storage-capacity check uses the bar fill: an upgrade dearer than the storage is never farmed for.

**Phase 7, farming in sessions:**
- If the next step isn't affordable (live mode, farming on), the session farms with `target` mode: cost + margin, capped below capacity. It then re-captures and lets the dialog decide.
- Dashboard farming: background starts failed because Windows refused to bring LDPlayer to the front ("foreground lock"). Fixed with a synthetic ALT press (`village/winfocus.py`). Live: 1/1 attack done from the dashboard.

**Phase 8, orchestrator** (`village/orchestrator.py`), tick every 20 s:
- due = builder free with work (back-off 30 min / 2 h after sessions without progress), else the next builder freeing, grouped within the window; health check; active hours; jitter;
- sessions run as child processes with a 3 h timeout;
- dashboard commands; heartbeat in `kv`;
- stale sessions are marked interrupted;
- unverified INTENT actions are resolved from the next export, never repeated;
- gem guard: any gem drop pauses automation.

**Phase 9, dashboard:** an Automation page, also usable from a phone:
- automation on/pause, live/dry run (with a confirmation prompt), farming on/off;
- Run a session now, next session countdown and reason;
- session history with every action, its verification and screenshots;
- a status pill on every page.

**Game update 19.32.4 (2026-11-16):**
- More Settings became a tabbed menu: new templates, the list needs ~14 drags;
- the export gained an `emotes` ID list;
- static data is re-extracted automatically per version (1,261 objects);
- Town Hall 19 is now available.

**Tests:** 149 unit/integration tests (planner, session with a fake game, orchestrator with a fake clock, web API, confirm guards on real dialog crops).
- Live end-to-end: dashboard "Run a session now" → orchestrator → session #2 → capture → "no free builder", correct.

**Still open:**
- the first live upgrade (needs a free builder + live mode);
- Phase 3's 24 h forecast check and the Phase 10 7-day soak: these now run as the service operates;
- the Windows Firewall rule for access from other devices (needs admin, see README in chat).

### Multiple accounts (2026-11-20)

The game links several accounts to one Supercell ID. Switching in the game:
profile → Settings tab → the Supercell ID bar shows the current tag → blue
switch button → side panel with one card per account ("Supercell ID name",
village name, "Town Hall: N"). Tapping a card restarts the game on that account
within ~6 s, no confirmation. Below the cards: "OTHER IDS / Log in to Supercell
ID", never tapped. Verified live with 3 accounts (Anonduke #2RQPVQJ2V TH19,
RAHMANIV #9JCUV0CCV TH18, RAHMANV #2JV002VG2 TH18).

- `village/accounts.py`: read the current tag (OCR of the bar), read the cards
  (OCR, anchored on the "Town Hall: N" line), switch by exact Supercell ID name,
  "Find accounts" (learns each card's tag by switching once), switch job.
- Store v6: `accounts` table; queue items, sessions, actions, farm runs and
  notifications carry the tag; per-account kv (`player:`, `last_session:`,
  `no_progress:`, `fail_streak:`, `test:<tag>:`). The active account is the
  `player_tag` setting; everything reads the active account by default, a
  session pins its account at start. Pre-v6 rows were given to the account in use.
- Safety: every capture checks the export tag. A session, "Capture now", a dry
  run or a farming run that finds the game on another account switches back
  first; a switch only becomes active after the new account's export was read.
- Dashboard: account menu in the top bar (switch, rename, Find accounts), a
  progress overlay while the game switches (visible in every open browser), all
  data reloaded afterwards. Switching is refused during a session or farming run.
- Automation works on the active account only.

---

## 6. Risks and limitations

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R1 | **Account ban.** Automation breaks Supercell's Terms of Service. 24/7 unattended, regular patterns raise detection risk. | Account loss | Your decision. Use active hours, jitter, realistic attack pacing and caps on daily attacks. |
| R2 | **Emulator instability** (System UI ANR, frozen frames, screencap hang) | Stuck sessions | Keep the display awake (root cause found in Phase 0); short sessions with a fresh boot; frozen-frame and ANR detection; escalation. |
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
