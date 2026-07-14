# Design

Visual system for the xAuby WebUI (`xauby/webui/static/`). Register: product
(see PRODUCT.md). Direction: Dieter Rams / Swiss-industrial minimalism,
matched to the project's marketing site (`Website/public/research-platform.html`)
as it actually renders — a single confident orange accent on a warm
near-black shell, an 8px corner radius on cards/panels, hairline borders
instead of shadows, and borderless hairline-row tiles (no boxed stat cells)
inside detail panels. Less color, less ornament, more restraint than a
conventional fintech dashboard — not the previous orange-and-blue duotone.

## Theme

Dark only (`color-scheme: dark`). The operator glances at the dashboard from
a phone, often at night; dark is functional (OLED, ambient light), not
aesthetic posturing.

## Color

Plain hex/rgba tokens in `:root` (no OKLCH). **One accent hue, everything
else is a neutral or a state color:**

| Token | Value | Role |
|-------|-------|------|
| `--bg-0` | `#161614` | Page background |
| `--bg-1` | `#101010` | Desktop phone-frame fill |
| `--surface` | `#1e1e1b` | Dark framed panels — the dominant card treatment |
| `--surface-2` | `#232320` | Raised surface |
| `--tile` | `#161614` | Cutout stat tiles, list rows |
| `--ink-1` / `--ink-2` / `--ink-3` | `#ece9e2` @ 100% / 72% / 55% | Primary / secondary / caption text on dark surfaces |
| `--orange` (`--gold`) | `#ff431a` | The one accent: brand, LIVE status, nav active state, mini-card 1, edge accent on "urgent" panels |
| `--orange-mid` | `#ff6a3c` | Hover state, EMA fast, price marker |
| `--orange-tertiary` | `#ff7a4d` | Rare tertiary emphasis (underline-style links) |
| `--neutral` | `#8a877f` | Everywhere the old system used a second (blue) accent: EMA26, down-candles, the "quiet" half of a pairing, `--info` |
| `--warn` | `#c79a2a` | Degraded / caution / SIM-mode — the one color that reliably means "pay attention" |
| `--cream` | `#ece9e2` | Fill for the two light "paper" surfaces (balance card, signal hero) plus the Activity log panels |

`--pos` and `--neg` both resolve to `--orange` at the root — the dashboard
does not use green/red for profit/loss anywhere, including the Activity log.
The log's cream-background panels were the one earlier exception (true green
`#116b31` / red `#e03a10` chip text); that exception is gone — trade PnL,
event chips, and side badges all read as orange (positive / fill-type) or a
dark-neutral gray (negative / muted) on the cream fill, matching PRODUCT.md's
principle "profit/loss doesn't get its own hue" without a carve-out.

There is no blue anywhere in this palette. Where the previous duotone used
`--blue`/`--blue-deep` as the "other half" of a pairing (Position mini-card,
`detail-accent-blue` panels, EMA26, info chips), the neutral gray `--neutral`
takes over that role — a de-emphasis color, not a second accent.

The canvas charts can't read CSS variables, so `app.js` mirrors the tokens as
literals (documented in a comment above `PORTFOLIO_COLORS`). The portfolio
donut has no categorical hue palette to draw from in a one-accent system, so
extra slices step through accent/cream/neutral at two alphas rather than
introducing new hues.

The bundled fallback icon (`xau-logo.svg`, served when `cli_ui.webui_avatar`
is unset) is the one place a literal gold-coin gradient still exists —
generic branding for an unconfigured or forked instance, unrelated to the
dashboard chrome. The live instance overrides it with the operator's own
photo (`cli_ui.webui_avatar: /profile-itsara.jpg`).

## Typography

**One family for everything** — Space Grotesk, loaded from Google Fonts via
`<link>` + `preconnect` in `index.html`/`login.html` (weights 400/500/600/700;
the server's CSP allows `fonts.googleapis.com` / `fonts.gstatic.com` for
this, no other external origins). Body copy, labels, headings, and figures
all use the same family — there is no separate system-font stack for body
text. All figures use `font-variant-numeric: tabular-nums` /
`font-feature-settings: "tnum" 1`. Scale runs from a 52px equity hero (600
weight) down to 9–10px micro labels; `h1::first-letter` is always colored
`--orange`.

## Layout

Phone-first shell: `min(430px, 100%)` wide, `100svh`, safe-area padding,
**five views** — Home / Signal / Ops / Regime / Activity — toggled by
`body[data-view]` and a 5-button bottom nav. Each view scrolls natively with
hidden scrollbars. Desktop ≥980px centers the phone shell in a bezel frame,
but each view gets its own desktop grid (Overview splits into a balance
column + chart column; Operator/Regime become 3-column; Signal/Activity
become 2-column) rather than just scaling the phone layout up.

**Radii are a flat `8px`** (`--radius-sm` / `--radius-md` / `--radius-lg` all
resolve to the same value) on every card, panel, chip, badge, and input,
plus literal circles (status LED-adjacent elements, the user's avatar photo,
the Google-logo mark) and the bottom-nav pill shell (`--radius-pill`, 999px)
for genuinely round elements. Stat tiles (`.detail-item` / `.protection-item`
/ `.metric-item` / `.reason-item`) are no longer boxed — they're borderless
rows separated by a single `border-bottom` hairline (`--line` on dark
surfaces, a darker rgba on the cream hero cards), so a detail panel reads as
a flat list rather than a grid of cards; state (`ok`/`warn`/`info`/`muted`)
is carried by the value text color, not a background fill. The hero cards
(balance, signal hero, the two Activity panels, Position Detail, Market
Regime) render as light cream "paper" with near-black text (`#161614`), a
deliberate inversion against the dark shell — used sparingly, not on every
panel. Position Detail and Market Regime lead with one large featured
number (Unrealized PnL; Regime name) followed by two 4-tile groups rather
than a dense flat grid of every available field.

**No shadows, no gradients.** Depth and separation come entirely from 1px
hairline borders and flat background-fill contrast, matching the marketing
site's authored template (which has zero `box-shadow` and zero
`linear-gradient` outside of the bundled Google-logo mark). Where the
previous system used a glow/lift on hover, the current one shifts
`border-color` to `--orange-mid` only.

## Components

- **Header identity**: an avatar photo + operator name (`Itsara Kaewruang`,
  hardcoded fallback in `index.html`) next to a config-driven bot name and
  tagline. `/api/meta` serves `cli_ui.bot_name` (default `"xAuby :
  Alternative Store of Value Trading System"`, split by `app.js` into a
  two-line subtitle) and `cli_ui.webui_avatar`; unset config falls back to
  the bundled gold-coin mark. This is a personalized product, not a
  white-labeled template.
- **Status pill** (`.status-pill`): an 8px-rounded outline, not a filled
  capsule — orange border/text for LIVE, amber border/text for everything
  else (SIM, STALE, no-state, offline), with a small pulsing dot
  (`.pill-dot`, `currentColor`) inside the pill next to the label.
- **Health strip**: 2×2 micro-chips (engine / state age / ws age / api),
  durations compacted (`24d`, not raw seconds).
- **Bottom nav**: a fixed rounded bar (16px radius) holding a horizontally
  swipeable text-only track — no icons. A fixed orange indicator pill sits
  at the bar's horizontal center; the five view buttons scroll underneath it
  via `transform: translateX`, snapping to the nearest view on touch
  release (drag threshold: nearest of five 80px-wide slots) or on a plain
  click. The active label inverts to dark text where it sits under the
  indicator; inactive labels stay a muted cream.
- **Balance / Signal-hero / Activity panels**: light cream flat surface,
  dark text, no shadow — the app's "trust" surfaces, used for the figures
  that matter most (equity, the current signal, the event/trade logs) rather
  than every detail panel.
- **Accent panels** (`detail-accent-orange` / `detail-accent-blue` — Execution
  Health & Macro Guard vs. Risk & Signal & Strategy Bias): both are now dark
  framed panels identical to a plain panel. The "orange" variant gets a 2px
  orange top-edge accent bar and an orange-outlined badge for the
  more time-critical content; the "blue" variant (renamed in spirit, not in
  CSS class name, to keep the diff a value-swap) gets no edge bar and a
  neutral-gray-outlined badge. Accented-vs-neutral replaces
  orange-vs-blue as the way two panels in a group visually differ — there is
  no colored full-bleed fill left anywhere in these panels.
- **Chips** (`.chip-*` / `.ev-chip`): 8px-rounded rectangles, mostly
  outline-only. Inside the Activity log (light cream background) event
  chips are uniformly solid orange with dark text regardless of category —
  the category distinction lives in the small rail dot next to each row
  (orange for fill/open-type events, amber for risk events, muted gray for
  signal/regime/price events) rather than in chip color. Trade side badges
  (LONG/SHORT) share one neutral chip style, distinguished only by label
  text. Everywhere else chips use the orange accent, the neutral gray, or
  amber warn — no green, no red.
- **Sign-in page** (`login.html`, served at `/login` when a password is set):
  a frameless single-column flow on the same `#161614` canvas as the
  dashboard. The hero is a dark gradient block (`#1e1e1b` → `#161614`, no
  liquid animation, no light streaks) with cream text and an orange
  `h1::first-letter`, matching the dark-shell treatment used elsewhere; the
  primary button is the same flat cream-fill / orange-hover pattern as the
  marketing site's buttons. Space Grotesk is the sole font here too.
  Login-specific overrides live in `login.css` so dashboard layout rules
  cannot leak into the pre-auth screen. Pre-auth shows only generic
  branding and never exposes runtime state, the operator photo, or the
  operator name.

## Motion

State-conveying only: 220ms view fade-rise on tab switch, 120–180ms nav
transitions, `:active` scale on tab/nav buttons, a brief `value-flash` on
updated figures, an opacity pulse (not a glow) on the LIVE status pill.
Everything gated by `prefers-reduced-motion: reduce`.

## Safari/iOS specifics

`viewport-fit=cover` + `env(safe-area-inset-*)`, `theme-color` = `--bg-0`
(`#161614`),
standalone web-app metas (`apple-mobile-web-app-title: xAuby`),
`-webkit-tap-highlight-color: transparent`, `-webkit-text-size-adjust:
100%`, `overscroll-behavior-y: none` on body, `overscroll-behavior:
contain` on scrollers.
