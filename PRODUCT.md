# Product

## Register

product

## Users

One person: the bot's owner/operator, Itsara Kaewruang. He runs the xAuby
perpetual-swap trading bot (gold/XAU on OKX) on a VPS and checks on it from
his iPhone (Safari, often via Tailscale) or a desktop browser. Context is a
quick glance — waiting in line, in bed, between tasks — not a work session.
The job: "is the bot alive, what's my equity, what position am I in, did
anything happen?" answered in under ten seconds. The WebUI header shows his
name and photo directly (not a generic "operator" label) — this is a
single-owner product, not a multi-tenant one.

## Product Identity

Product name **xAuby**, tagline **"Alternative Store of Value Trading
System"** (`xauby/meta.py`). The combined title `xAuby : Alternative Store
of Value Trading System` is the default shown in the CLI banner, TUI
header, Telegram messages, and the WebUI header subtitle;
`bot_config.yaml -> cli_ui.bot_name` can override it per deployment, and
`cli_ui.webui_avatar` swaps the header photo (defaults to a bundled
gold-coin mark when unset). "Alternative Store of Value" describes the
strategy itself — gold/XAU as a hedge asset — it is not marketing filler.

## Product Purpose

The WebUI is a strictly read-only monitoring dashboard over the live engine
state (`/api/state`, `/api/health`, `/api/recent-events`, `/api/trades`,
`/api/candles`, `/api/meta`). It never places, cancels, or modifies trades.
Success = the owner trusts a five-second glance on Home: equity, position,
signal, regime, and system health are legible instantly on a phone, and
anomalies (degraded feed, stale state, offline engine) are impossible to
miss. Four further views (Signal, Ops, Regime, Activity) exist for whenever
he wants to go deeper than the glance — still without a single control.

## Brand Personality

Personal, confident, a little bold — this is one man's gold desk, branded
with his own name and photo, not a mass-market exchange app or a
white-labeled template. The execution matches the project's own marketing
site: Dieter Rams / Swiss-industrial restraint rather than generic "premium
fintech minimalism" — a single confident orange accent against a warm
near-black shell, an 8px corner radius on cards and panels, hairline
borders instead of shadows, borderless hairline-row tiles inside detail
panels rather than boxed stat cells, light cream "paper" surfaces used
sparingly for the numbers that matter most (balance, signal, position PnL,
regime), and one display webfont (Space Grotesk) used for everything, not
just the big figures. Trust comes from restraint in what's *shown* (no
clutter, no controls) and restraint in *how* it's shown (one hue, no
ornament) rather than from a muted palette — the one color allowed on the
shell is allowed to be confident precisely because it isn't competing with
a second one.

## Anti-references

- The generic neon-crypto dashboard: purple/cyan gradients, glassmorphism
  cards, glow shadows on everything. (An earlier WebUI iteration leaned this
  way; the current single-accent, zero-shadow system is the more
  disciplined alternative — matched to the project's own marketing site as
  it actually renders, corner radius included, rather than invented
  independently.)
- A second accent color standing in for "more design": the dashboard
  previously ran an orange-and-blue duotone. That duotone is gone — every
  place blue used to mark "the other half" of a pairing (Position card,
  EMA26, info chips, accent panels) now uses a neutral gray or no color at
  all, on the theory that a second hue reads as decoration once one hue
  already carries brand and status.
- Exchange-app maximalism: ticker grids, blinking numbers, ten colors per
  screen. (The palette caps at one accent, one neutral, and one warning
  color; profit/loss does not get its own hue anywhere, including the
  Activity log — an earlier iteration carved out true green/red there, that
  carve-out is gone.)
- Desktop-admin chrome shrunk onto a phone: sidebars, dense tables, hover-
  dependent affordances.

## Design Principles

1. **Home is glanceable; Signal/Ops/Regime/Activity are opt-in depth.** The
   one question on Home ("am I OK?") is answerable without scrolling:
   equity → position → signal → everything else. The other four tabs trade
   some of that glanceability for detail the same single operator wants on
   demand — each still answers its own one question without scrolling.
2. **One accent, everything else is state.** Orange is the brand's only
   accent, used for identity, navigation, and LIVE status; amber warn is the
   only other color that means "look at this now." Where two halves of a
   pairing need to read as distinct (price vs. position, execution vs.
   risk), the second half is a neutral gray or an unaccented panel, never a
   second accent hue. Green and red are not used anywhere, including the
   Activity/Trade log — profit/loss reads as orange (positive) or
   dark-neutral gray (negative) everywhere in the app.
3. **Read-only means calm.** No affordances that imply control. No urgency
   theater. Anomalies get loud (amber warn); normal operation stays quiet.
4. **Phone-native, Safari-first.** Designed for iOS Safari viewport
   realities (dynamic toolbar, safe areas, touch targets ≥44px, no hover);
   desktop gets the same shell centered in a bezel, with each view's own
   grid — not a widened admin layout.
5. **Numbers are the interface.** Tabular figures, a display webfont for
   the numbers that matter most, consistent precision, aligned columns.

## Accessibility & Inclusion

WCAG AA contrast (≥4.5:1 body text) on dark surfaces. `prefers-reduced-
motion` honored for any transition. Color never the sole carrier of state
(labels accompany every accent). Touch targets ≥44×44px. No hover-only
information.
