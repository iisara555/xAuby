# Product

## Register

product

## Users

One person: the bot's owner/operator. He runs the xAuby perpetual-swap trading
bot (gold/XAU on OKX) on a VPS and checks on it from his iPhone (Safari, often
via Tailscale) or a desktop browser. Context is a quick glance — waiting in
line, in bed, between tasks — not a work session. The job: "is the bot alive,
what's my equity, what position am I in, did anything happen?" answered in
under ten seconds.

## Product Purpose

The WebUI is a strictly read-only monitoring dashboard over the live engine
state (`/api/state`, `/api/health`, `/api/recent-events`, `/api/trades`,
`/api/candles`). It never places, cancels, or modifies trades. Success = the
owner trusts a five-second glance: equity, position, signal, regime, and
system health are legible instantly on a phone, and anomalies (degraded feed,
stale state, offline engine) are impossible to miss.

## Brand Personality

Private-bank calm, precise, personal. This is one man's gold desk, not a
mass-market exchange app. Feels like a premium fintech product: a confident
balance figure, quiet dark surfaces, one deliberate accent, numbers treated
as first-class typography. Trust comes from restraint and legibility, not
from decoration.

## Anti-references

- The generic neon-crypto dashboard: purple/cyan gradients, glassmorphism
  cards, glow shadows on everything. (The previous WebUI iteration leaned
  this way; the redesign moves away from it deliberately.)
- Exchange-app maximalism: ticker grids, blinking numbers, ten colors per
  screen.
- Desktop-admin chrome shrunk onto a phone: sidebars, dense tables, hover-
  dependent affordances.

## Design Principles

1. **Glanceable first.** The one question per screen ("am I OK?") is
   answerable without scrolling. Hierarchy: equity → position → signal →
   everything else.
2. **Semantic color only.** Green/red/amber mean profit/loss/warning and
   nothing else; the single brand accent (gold — it trades gold) marks
   identity and selection, never state.
3. **Read-only means calm.** No affordances that imply control. No urgency
   theater. Anomalies get loud; normal operation stays quiet.
4. **Phone-native, Safari-first.** Designed for iOS Safari viewport
   realities (dynamic toolbar, safe areas, touch targets ≥44px, no hover),
   desktop gets the phone layout centered — not the other way around.
5. **Numbers are the interface.** Tabular figures, consistent precision,
   aligned columns; typography does the work decoration used to do.

## Accessibility & Inclusion

WCAG AA contrast (≥4.5:1 body text) on all dark surfaces. `prefers-reduced-
motion` honored for any transition. Color never the sole carrier of state
(labels accompany green/red). Touch targets ≥44×44px. No hover-only
information.
