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
white-labeled template. The execution leans warmer and more graphic than
generic "premium fintech minimalism": a bright orange-and-blue duotone
against a near-black shell, light cream "paper" cards for the numbers that
matter most (balance, signal, detail heroes), and a display webfont (Space
Grotesk) for the big figures. Trust comes from restraint in what's *shown*
(no clutter, no controls) rather than from a muted palette — the color
itself is allowed to be confident.

## Anti-references

- The generic neon-crypto dashboard: purple/cyan gradients, glassmorphism
  cards, glow shadows on everything. (The previous WebUI iteration leaned
  this way; the current orange/blue duotone is a deliberate, more
  disciplined alternative — a bold accent color without the
  gradient-on-everything excess.)
- Exchange-app maximalism: ticker grids, blinking numbers, ten colors per
  screen. (The duotone system intentionally caps the palette at two
  accents plus one warning color; profit/loss does not get its own hue on
  the dark shell.)
- Desktop-admin chrome shrunk onto a phone: sidebars, dense tables, hover-
  dependent affordances.

## Design Principles

1. **Home is glanceable; Signal/Ops/Regime/Activity are opt-in depth.** The
   one question on Home ("am I OK?") is answerable without scrolling:
   equity → position → signal → everything else. The other four tabs trade
   some of that glanceability for detail the same single operator wants on
   demand — each still answers its own one question without scrolling.
2. **Two accents, everything else is state.** Orange and blue are the
   brand's duotone, used for identity, navigation, and to distinguish the
   two halves of a pairing (price vs. position, execution vs. risk); amber
   warn is the only color that means "look at this now." Green/red are
   reserved for the Activity trade log, not used for PnL on the dark
   shell.
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
