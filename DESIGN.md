# Design

Visual system for the xAuby WebUI (`xauby/webui/static/`). Register: product
(see PRODUCT.md). Direction: a warm orange-and-blue duotone on a near-black
shell, with light "paper" cards for hero content (balance, signal, detail
panels) — bolder and more graphic than a conventional muted fintech dashboard.

## Theme

Dark only (`color-scheme: dark`). The operator glances at the dashboard from
a phone, often at night; dark is functional (OLED, ambient light), not
aesthetic posturing.

## Color

Plain hex/rgba tokens in `:root` (no OKLCH). Two accent hues carry the whole
system instead of one:

| Token | Value | Role |
|-------|-------|------|
| `--bg-0` | `#07080d` | Page background |
| `--bg-1` | `#161618` | Desktop phone-frame fill |
| `--surface` | `#202027` | Dark cards / panels |
| `--surface-2` | `#2a2b36` | Raised surface |
| `--tile` | `rgba(255,255,255,.07)` | Quiet stat tiles, list rows |
| `--ink-1` / `--ink-2` / `--ink-3` | `#f8f4ee` @ 100% / 80% / 54% | Primary / secondary / caption text on dark surfaces |
| `--orange` (`--gold`) | `#ff431a` | Primary accent: brand, LIVE status, nav active state, first duotone card |
| `--orange-mid` | `#ff7040` | Gradient partner for `--orange` |
| `--blue` / `--blue-deep` | `#97aef0` / `#526fe6` | Secondary accent: the "other side" of every duotone pairing (position card, info chips, regime/operator panels) |
| `--warn` | `#ffb15c` | Degraded / caution / anything not LIVE |
| `--info` | alias of `--blue` | EMA26, regime info |
| `--cream` | `#f8f4ee` | Fill for light "paper" cards (balance, signal hero, detail-hero) |

`--gold` is a direct alias of `--orange` — there is no separate gold hue in
the live palette. `--pos` and `--neg` both resolve to `--orange` (`#ff431a`)
at the root: the dashboard does not use green/red for profit/loss on the
dark shell, it leans on the orange/blue duotone and card placement instead.
Real green/red only show up inside the Activity log (`#116b31` / `#9b2418`),
against the light activity-panel background, for trade PnL chips. `--warn`
is the one color that reliably means "pay attention."

The canvas charts can't read CSS variables, so `app.js` mirrors the tokens
as literals (documented in a comment above `PORTFOLIO_COLORS`). Two extra
hues — `#70e0c2` and `#c6b7ff` — exist only as the extended categorical
palette for 5th/6th portfolio assets in the donut/legend; they are data-viz
colors, never UI state. Dark captions on the light cards sit at
`rgba(22,22,24,.62)` (cream) / `.72` (orange/blue gradients) — the minimum
alphas that clear 4.5:1; don't lower them for elegance.

The bundled fallback icon (`xau-logo.svg`, served when `cli_ui.webui_avatar`
is unset) is the one place a literal gold-coin gradient
(`#fff0a8 → #f6c84f → #c88922 → #7a4a14`) exists — generic branding for an
unconfigured or forked instance. The live instance overrides it with the
operator's own photo (`cli_ui.webui_avatar: /profile-itsara.jpg`).

## Typography

Two families. Body copy, labels, and the health strip use the iOS system
stack (`-apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, …`).
Display numbers and headings (`h1`, the equity figure, the signal action,
featured detail values) use **Space Grotesk**, loaded from Google Fonts via
`<link>` + `preconnect` in `index.html` — a webfont, not a system-only
stack (the server's CSP explicitly allows `fonts.googleapis.com` /
`fonts.gstatic.com` for this; no other external origins). All figures use `font-variant-numeric: tabular-nums` /
`font-feature-settings: "tnum" 1`. Scale runs from a 52px equity hero (600
weight) down to 9–10px micro labels; `h1::first-letter` is always colored
`--orange`.

## Layout

Phone-first shell: `min(430px, 100%)` wide, `100svh`, safe-area padding,
**five views** — Home / Signal / Ops / Regime / Activity — toggled by
`body[data-view]` and a 5-button bottom nav. Each view scrolls natively with
hidden scrollbars. Desktop ≥980px centers the phone shell in a rounded
bezel frame, but each view gets its own desktop grid (Overview splits into
a 380px balance column + chart column; Operator/Regime become 3-column;
Signal/Activity become 2-column) rather than just scaling the phone layout
up. Radii are 16/24/30px plus a full pill — larger and rounder than a
typical dense fintech UI. Stat tiles are uniform `--tile` fills on dark
panels; the hero cards (balance, signal, detail-hero, first Activity panel)
instead render as light cream "paper" with near-black text (`#161618`), a
deliberate inversion against the dark shell.

## Components

- **Header identity**: an avatar photo + operator name (`Itsara Kaewruang`,
  hardcoded fallback in `index.html`) next to a config-driven bot name and
  tagline. `/api/meta` serves `cli_ui.bot_name` (default `"xAuby :
  Alternative Store of Value Trading System"`, split by `app.js` into a
  two-line subtitle) and `cli_ui.webui_avatar`; unset config falls back to
  the bundled gold-coin mark. This is a personalized product, not a
  white-labeled template.
- **Status pill** (`.status-pill`): only two states exist. LIVE = the
  brand orange gradient with a slow pulse; everything else (SIM, STALE,
  no-state, offline) = amber warn. The accent color doing double duty as
  "brand" and "all is well" is intentional here, not an oversight.
- **Health strip**: 2×2 micro-chips (engine / state age / ws age / api),
  durations compacted (`24d`, not raw seconds).
- **Bottom nav**: fixed pill bar, 5 tabs, blur backdrop; the active tab is
  filled solid with the orange gradient (dark text on it), not a
  gold-text-on-tint treatment.
- **Balance / Signal / Detail-hero cards**: light cream gradient surface,
  dark text, soft shadow — the app's one "trust" surface, echoed by
  matching hero treatments on the Signal and Operator/Regime detail views.
- **Duotone accent cards**: the second mini-card (Position) and the
  `detail-accent-*` panels alternate a solid orange gradient card and a
  solid blue gradient card, both with dark text — the recurring two-color
  motif that carries identity through Operator and Regime detail.
- **Chips** (`.chip-*` / `.ev-chip`): tinted bg + matching text. Inside the
  Activity log these use true green/red; everywhere else they use the
  orange/blue/warn accents above.
- **Sign-in page** (`login.html`, served at `/login` when a password is set):
  a frameless single-column flow on the dark xAuby canvas. The orange
  liquid-glow hero with diagonal light streaks sits above the password form
  and blue pill action, preserving the reference layout without a white page
  or simulated phone bezel. Hero headline text uses Noto Sans Regular; the
  hero summary uses Noto Sans Light. Login-specific overrides live in
  `login.css` so dashboard layout rules cannot leak into the pre-auth screen.
  Pre-auth shows only generic branding and never exposes runtime state, the
  operator photo, or the operator name.

## Motion

State-conveying only: 220ms view fade-rise on tab switch, 120–180ms nav
transitions, `:active` scale on tab/nav buttons, a brief `value-flash` on
updated figures. Everything gated by `prefers-reduced-motion: reduce`.

## Safari/iOS specifics

`viewport-fit=cover` + `env(safe-area-inset-*)`, `theme-color` = `--bg-0`
(`#07080d`),
standalone web-app metas (`apple-mobile-web-app-title: xAuby`),
`-webkit-tap-highlight-color: transparent`, `-webkit-text-size-adjust:
100%`, `overscroll-behavior-y: none` on body, `overscroll-behavior:
contain` on scrollers.
