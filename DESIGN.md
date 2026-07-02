# Design

Visual system for the xAuby WebUI (`xauby/webui/static/`). Register: product
(see PRODUCT.md). Direction: "private gold desk" — warm near-black surfaces,
one gold accent, semantic color reserved for state.

## Theme

Dark only (`color-scheme: dark`). The operator glances at the dashboard from
a phone, often at night; dark is functional (OLED, ambient light), not
aesthetic posturing.

## Color (OKLCH)

| Token | Value | Role |
|-------|-------|------|
| `--bg-0` | `oklch(14% 0.008 80)` | Page background |
| `--bg-1` | `oklch(17% 0.01 80)` | Desktop phone-frame fill |
| `--surface` | `oklch(21% 0.012 80)` | Cards / panels |
| `--surface-2` | `oklch(24.5% 0.014 80)` | Raised surface |
| `--tile` | `oklch(100% 0 0 / 0.045)` | Quiet stat tiles, list rows |
| `--ink-1` | `oklch(96% 0.008 90)` | Primary text |
| `--ink-2` | `oklch(81% 0.015 85)` | Secondary text / prose |
| `--ink-3` | `oklch(67% 0.015 85)` | Labels, captions (≥4.5:1 on surface) |
| `--gold` | `oklch(82% 0.12 88)` | Brand accent: identity + selection only |
| `--pos` | `oklch(78% 0.14 158)` | Profit / OK / long |
| `--neg` | `oklch(72% 0.16 22)` | Loss / error / short |
| `--warn` | `oklch(80% 0.13 60)` | Degraded / caution (orange, distinct from gold) |
| `--info` | `oklch(78% 0.07 245)` | Informational (EMA26, regime info) |

Each state color has a `-tint` variant at ~13% alpha for chip/tile fills.
Rules: gold never signals state; green/red/amber never decorate. Canvas chart
hexes mirror the tokens (`#e3b558` gold EMA12, `#8fb7e8` info EMA26,
`#4cc38f`/`#e56571` candles).

## Typography

Single family: iOS system stack (`-apple-system, BlinkMacSystemFont,
"SF Pro Text", system-ui, …`). No webfonts. All figures use
`font-variant-numeric: tabular-nums`. Scale is fixed rem/px, tight ratio:
36px equity hero (700, -0.015em) → 18px page title → 14px panel headings →
12–13px body → 10–11px labels. Units render smaller and muted next to big
numbers (`.unit`).

## Layout

Phone-first shell: `min(430px, 100%)` wide, `100dvh`, safe-area padding,
three views (Home / Signal / Activity) toggled by `body[data-view]`. Each
view scrolls natively (`-webkit-overflow-scrolling: touch`, hidden
scrollbars, bottom padding clearing the nav). Desktop ≥860px centers the
phone layout in a bezel frame — never a widened layout. Radii: 10/14/20px.
Stat tiles are uniform `--tile` fills; value color carries meaning.

## Components

- **Status pill** (`.status-pill`): LIVE = pos tint; SIM/STALE/offline = warn.
- **Health strip**: 2×2 micro-chips (engine / state age / ws age / api),
  durations compacted (`24d`, not raw seconds).
- **Bottom nav**: fixed pill bar, 3 tabs, ≥48px targets, active tab =
  gold text on `--gold-tint`; blur backdrop.
- **Balance card**: hero equity number, faint gold radial tint top-left —
  the only decorative gold on the page.
- **Chips** (`.chip-*` / `.ev-chip`): tinted bg + matching text, semantic.

## Motion

State-conveying only: 220ms view fade-rise on tab switch, 120–180ms nav
transitions, `:active` scale on tab buttons. Everything gated by
`prefers-reduced-motion: reduce`.

## Safari/iOS specifics

`viewport-fit=cover` + `env(safe-area-inset-*)`, `theme-color #191712`,
standalone web-app metas, `-webkit-tap-highlight-color: transparent`,
`-webkit-text-size-adjust: 100%`, `overscroll-behavior-y: none` on body,
`overscroll-behavior: contain` on scrollers.
