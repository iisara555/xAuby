(() => {
  const SOURCE_URL = "https://github.com/iisara555/xAuby";
  const SUPPORT_URL = "https://donate.stripe.com/4gM9AU5lOc2s9o88d74Ni00";

  const NAV_ITEMS = [
    ["#system", "Trading"],
    ["#architecture", "Platform"],
    ["#risk", "Risk"],
    ["#evidence", "Evidence"],
    ["#roadmap", "Roadmap"],
    ["#workspace", "Workspace"],
    ["#support", "Support"],
  ];

  const PITCH_MARKUP = `
    <section class="xn-hero-scrub" data-hero-scrub aria-labelledby="hero-heading">
      <div class="xn-hero-sticky">
        <div class="xn-hero-inner">
          <div class="xn-hero-content">
            <p class="xn-hero-kicker">Owner-operated live system · OKX · gold + bitcoin</p>
            <h1 id="hero-heading" class="xn-hero-title">Real capital.<br>Reproducible evidence<span style="color:#ff431a;">.</span></h1>
            <p class="xn-hero-text">xAuby is an open-source trading platform already executing its founder's capital through two certified 4-hour systems. Its edge is governance: a live preset is tied to a reproducible certificate, every decision is persisted, and execution can be replayed from signal to fill. The mission is not to sell a backtest. It is to build the evidence layer automated trading is missing.</p>
            <div class="xn-hero-actions">
              <a href="#system">See how it trades</a>
              <a href="#roadmap">Why back the roadmap</a>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section id="pitch-proof" class="xp-proof" aria-label="Current production footprint">
      <p class="xp-proof-label">Production footprint · verified from the owner tenant configuration</p>
      <div class="xp-metric-grid">
        <div><strong>2</strong><span>Live pairs · XAU + BTC</span></div>
        <div><strong>2</strong><span>Fingerprint-matched presets</span></div>
        <div><strong>1×</strong><span>Maximum leverage</span></div>
        <div><strong>Invite only</strong><span>Pilot · public signup off</span></div>
      </div>
      <p class="xp-footnote">Owner capital only. xAuby does not accept deposits, manage public money, sell signals, or promise returns.</p>
    </section>

    <section id="system" class="xp-section" aria-labelledby="system-heading">
      <div class="xp-heading-row">
        <div><p class="xp-eyebrow">The live trading method</p><h2 id="system-heading">Two markets. Two jobs. One governed engine<span>.</span></h2></div>
        <p class="xp-kicker">Both pairs run direct strategies · regime router off</p>
      </div>
      <p class="xp-lead">xAuby does not rotate among experimental strategies in production. A strict tenant whitelist pins one certified profile to each pair; research plugins remain blocked from live execution.</p>

      <div class="xp-strategy-grid">
        <article class="xp-card xp-strategy-card">
          <div class="xp-card-top"><span class="xp-pill xp-pill-live">LIVE · LONG ONLY</span><span class="xp-code">XAUUSDT · 4H + 1D</span></div>
          <h3>XAU · xAuby ActionZone</h3>
          <p>Looks for a fresh 4H GREEN transition from the smoothed EMA12/EMA26 ActionZone, within a three-bar window. The entry candle must close with at least 0.5 thrust, and the last closed daily bar must remain in an allowed bullish or neutral zone.</p>
          <ul class="xp-detail-list">
            <li><strong>Entry</strong><span>Fresh GREEN zone · D1 gate · long only</span></li>
            <li><strong>Exit</strong><span>8% ROI initially, 5% after day 1, 3% after day 3, or a RED zone</span></li>
            <li><strong>Capital</strong><span>65% portfolio budget; profile may use 95% of that allocation</span></li>
            <li><strong>Protection</strong><span>No exchange-side stop; this is disclosed, measured, and deliberately not hidden</span></li>
          </ul>
        </article>

        <article class="xp-card xp-strategy-card">
          <div class="xp-card-top"><span class="xp-pill xp-pill-live">LIVE · LONG + SHORT</span><span class="xp-code">BTCUSDT · 4H</span></div>
          <h3>BTC · SuperTrend + EMA200</h3>
          <p>Waits for a fresh SuperTrend flip using a 4.0 multiplier and ATR(10), then requires price to agree with EMA200. A bullish flip above EMA200 opens long; a bearish flip below it opens short.</p>
          <ul class="xp-detail-list">
            <li><strong>Entry</strong><span>Fresh SuperTrend flip · EMA200 alignment</span></li>
            <li><strong>Exit</strong><span>Opposite SuperTrend flip, EMA200 loss/reclaim, or confirmed stop</span></li>
            <li><strong>Protection</strong><span>3× ATR initial stop · 2× ATR trailing · breakeven activates at 1.2× ATR</span></li>
            <li><strong>Capital</strong><span>30% portfolio budget · isolated swap · 1×</span></li>
          </ul>
        </article>
      </div>

      <ol class="xp-flow" aria-label="Production execution flow">
        <li><span>01</span><div><h3>Observe</h3><p>REST candles and WebSocket ticks feed a 60-second control loop. Closed 4H bars drive signals; stale data and reconnect windows block new entries.</p></div></li>
        <li><span>02</span><div><h3>Resolve</h3><p>The tenant whitelist selects symbol, strategy, timeframe, allowed side, live mode, leverage, allocation and the certificate-bound execution profile.</p></div></li>
        <li><span>03</span><div><h3>Decide</h3><p>The pair's own strategy returns open, close or hold. Most ticks do nothing. Production does not route these pairs into another strategy.</p></div></li>
        <li><span>04</span><div><h3>Gate</h3><p>Capital budgets, daily realised loss, trade count, cooldowns, feed health, drawdown and maximum open positions are checked before dispatch.</p></div></li>
        <li><span>05</span><div><h3>Execute</h3><p>Orders go to OKX USDT swaps in isolated, one-way mode at 1×. Reduce-only exits, account locks and order reconciliation protect against duplicate or wrong-side execution.</p></div></li>
        <li><span>06</span><div><h3>Persist</h3><p>Trades, ticks, signals and lifecycle events are written to SQLite and durable event logs so the live decision can be replayed and audited.</p></div></li>
        <li><span>07</span><div><h3>Operate</h3><p>The owner uses the Pilot Workspace, terminal and Telegram. External dead-man and health timers keep checking even when the engine itself cannot report.</p></div></li>
      </ol>
    </section>

    <section id="architecture" class="xp-section" aria-labelledby="architecture-heading">
      <div class="xp-heading-row">
        <div><p class="xp-eyebrow">The platform thesis</p><h2 id="architecture-heading">The moat is not another indicator<span>.</span></h2></div>
        <p class="xp-kicker">Evidence compounds · hype does not</p>
      </div>
      <p class="xp-lead">The durable product is the chain from research to certificate to runtime to realised result. Each additional certified preset expands the product inventory; the control plane and operating safeguards are reused across markets and tenants.</p>
      <div class="xp-three-grid">
        <article class="xp-card"><span class="xp-card-num">01</span><h3>Certificate-bound runtime</h3><p>Live presets carry a config fingerprint, protocol, venue source and measured evidence. If the profile drifts, the catalog falls back to not assessed instead of borrowing an old verdict.</p><span class="xp-pill">LIVE · 2 PRESETS</span></article>
        <article class="xp-card"><span class="xp-card-num">02</span><h3>Replayable operations</h3><p>Strategy exit math is shared with replay, durable events preserve lifecycle evidence, and scheduled parity reports compare realised fee, slippage and PnL with simulator assumptions.</p><span class="xp-pill">LIVE · AUDITABLE</span></article>
        <article class="xp-card"><span class="xp-card-num">03</span><h3>Invite-only control plane</h3><p>FastAPI, Next.js and systemd isolate tenant config and runtime state. Credentials use envelope encryption; TOTP, Trade PIN, audit trails and certified-preset gates protect high-risk actions.</p><span class="xp-pill">PILOT · 3 USERS / 2 ENGINES</span></article>
      </div>
      <div class="xp-truth-grid">
        <div><strong>Already real</strong><p>Owner engine, two live markets, web control plane, health/dead-man timers, certificate pipeline, durable replay and guarded manual orders.</p></div>
        <div><strong>Not claimed yet</strong><p>No public signup, billing, managed accounts, proven six-month live edge, external plugin isolation or production-ready mass tenancy.</p></div>
      </div>
      <a class="xp-text-link" href="${SOURCE_URL}">Inspect the code that runs live <span aria-hidden="true">→</span></a>
    </section>

    <section id="risk" class="xp-section" aria-labelledby="risk-heading">
      <div class="xp-panel">
        <div class="xp-heading-row">
          <div><p class="xp-eyebrow">Capital controls</p><h2 id="risk-heading">Risk is explicit—even where protection is imperfect<span>.</span></h2></div>
          <span class="xp-pill">OWNER TENANT · CURRENT CONFIG</span>
        </div>
        <p class="xp-lead">The engine enforces portfolio and execution gates, but xAuby does not turn configuration percentages into false certainty. XAU and BTC protect capital differently, and the page says so plainly.</p>
        <div class="xp-metric-grid xp-risk-grid">
          <div><strong>65 / 30</strong><span>XAU / BTC budget %</span></div>
          <div><strong>3%</strong><span>Daily realised loss cap</span></div>
          <div><strong>2</strong><span>Open positions max</span></div>
          <div><strong>3</strong><span>Daily trades max</span></div>
          <div><strong>25%</strong><span>Drawdown entry guard</span></div>
          <div><strong>1×</strong><span>Maximum leverage</span></div>
        </div>

        <div class="xp-calculator">
          <div class="xp-calculator-head"><h3>Budget arithmetic at your equity</h3><label for="xp-equity">Equity <strong id="xp-equity-label">10,000 USDT</strong></label></div>
          <input id="xp-equity" type="range" min="1000" max="100000" step="1000" value="10000" aria-label="Account equity in USDT">
          <div class="xp-three-grid xp-budget-grid">
            <div><strong data-budget="0.65">6,500 USDT</strong><span>XAU allocation ceiling · 65%</span></div>
            <div><strong data-budget="0.30">3,000 USDT</strong><span>BTC allocation ceiling · 30%</span></div>
            <div><strong data-budget="0.03">300 USDT</strong><span>Daily realised loss cap · 3%</span></div>
          </div>
        </div>

        <div class="xp-warning"><strong>The honest boundary</strong><p>XAU's 1% risk setting is a sizing input, not a maximum-loss promise: the live XAU profile has no exchange stop and exits through its ROI ladder or a RED zone. The 25% drawdown guard blocks new entries but is configured not to force-close positions. BTC does use an exchange-side ATR stop and trailing protection. Daily loss is measured from closed trades, not unrealised loss.</p></div>
      </div>
    </section>

    <section id="evidence" class="xp-section" aria-labelledby="evidence-heading">
      <div class="xp-heading-row">
        <div><p class="xp-eyebrow">Current certificates</p><h2 id="evidence-heading">Evidence before expansion<span>.</span></h2></div>
        <p class="xp-kicker">Backtest evidence · not live returns · not a forecast</p>
      </div>
      <p class="xp-lead">These are the exact point-in-time records behind the owner tenant's two live presets. Costs are included. Limitations travel with the headline instead of being buried below it.</p>

      <div class="xp-evidence-grid">
        <article class="xp-card xp-evidence-card">
          <div class="xp-card-top"><span class="xp-pill xp-pill-live">CERTIFIED · 2026-07-29</span><span class="xp-code">OKX XAUT-USDT · 4H</span></div>
          <h3>XAU ActionZone · long only + D1</h3>
          <div class="xp-metric-grid xp-certificate-metrics">
            <div><strong>+80.72%</strong><span>Net · 4.0 years</span></div>
            <div><strong>2.18</strong><span>Profit factor</span></div>
            <div><strong>45.1%</strong><span>Win rate</span></div>
            <div><strong>8.5%</strong><span>Max drawdown</span></div>
            <div><strong>102</strong><span>Trades</span></div>
          </div>
          <p class="xp-caveat"><strong>Read before quoting:</strong> the same four-year XAUT period helped select the winner from a 432-cell grid. It is a same-venue proxy for the shorter XAU swap series, not a pristine holdout; the native swap cross-check contains only 25 trades over 1.3 years, and no six-month shadow record exists.</p>
          <a class="xp-text-link" href="${SOURCE_URL}/blob/main/docs/research/xau_long_only_d1_certificate_2026-07-29.md">Read the XAU certificate →</a>
        </article>

        <article class="xp-card xp-evidence-card">
          <div class="xp-card-top"><span class="xp-pill xp-pill-live">CERTIFIED · 2026-07-27</span><span class="xp-code">OKX BTC-USDT-SWAP · 4H</span></div>
          <h3>BTC SuperTrend + EMA200 · long + short</h3>
          <div class="xp-metric-grid xp-certificate-metrics">
            <div><strong>+19.35%</strong><span>Net · 6.6 years</span></div>
            <div><strong>1.52</strong><span>Profit factor</span></div>
            <div><strong>38.1%</strong><span>Win rate</span></div>
            <div><strong>9.8%</strong><span>Max drawdown</span></div>
            <div><strong>134</strong><span>Trades</span></div>
          </div>
          <p class="xp-caveat"><strong>Read before quoting:</strong> this is native venue evidence and the same strategy also passed a 66-month fixed-config walk-forward study. The edge is still thin: the certificate bootstrap's 5th percentile is −2.17%, so profitability is uncertain even though the pre-registered gate passed.</p>
          <a class="xp-text-link" href="${SOURCE_URL}/blob/main/docs/research/btc_supertrend_ema200_certificate_2026-07.md">Read the BTC certificate →</a>
        </article>
      </div>

      <div class="xp-definition"><strong>What “certified” means here</strong><p>The exact execution profile cleared a repository protocol and its fingerprint matches the live preset. It does not mean all-weather, guaranteed, statistically final or suitable for another person's capital. A config change invalidates the borrowed verdict.</p></div>
    </section>

    <section id="roadmap" class="xp-section" aria-labelledby="roadmap-heading">
      <div class="xp-heading-row">
        <div><p class="xp-eyebrow">Roadmap · evidence-gated growth</p><h2 id="roadmap-heading">Earn the right to scale<span>.</span></h2></div>
        <p class="xp-kicker">Research → pilot → proof → commercial</p>
      </div>
      <p class="xp-lead">xAuby already has the hard middle—live execution, multi-tenant controls and reproducible certification. The roadmap deliberately refuses to put billing ahead of operational evidence.</p>

      <ol class="xp-roadmap">
        <li class="is-current"><span class="xp-roadmap-marker">NOW</span><div><h3>Prove the owner-operated core</h3><p>Two certificate-bound live pairs, venue-aligned data, durable events, replay/result parity, account locks, API circuit breaker, dead-man timer, security audit and guarded Pilot Workspace.</p><strong>STATUS · OPERATING</strong></div></li>
        <li><span class="xp-roadmap-marker">NEXT</span><div><h3>Run the design-partner pilot</h3><p>Invite two partners in SIM-only mode, prove self-service onboarding for two weeks, keep Telegram working through reboot, and make off-site encrypted backup plus restore drills operational.</p><strong>GATE · 2 PARTNERS / 2 WEEKS</strong></div></li>
        <li><span class="xp-roadmap-marker">PROOF</span><div><h3>Build a live evidence record worth selling</h3><p>Maintain at least six months of live history on the exact certified profiles, grow the catalog to four certified presets, let two partners operate without hand-holding, and record 60 days without a P1 incident.</p><strong>GATE · NO SHORTCUT</strong></div></li>
        <li><span class="xp-roadmap-marker">Q4+</span><div><h3>Commercialise only after the gates</h3><p>Complete legal terms, privacy, risk disclosure, data export and deletion first; then add billing and capacity. Managed secrets, tenant resource quotas and real plugin isolation precede any external strategy marketplace.</p><strong>STATUS · CONDITIONAL</strong></div></li>
      </ol>

      <div class="xp-use-of-support">
        <h3>What development support unlocks</h3>
        <div class="xp-four-grid">
          <div><span>01</span><strong>Better evidence</strong><p>Native venue history, untouched holdouts, longer shadow records and more reproducible certificates.</p></div>
          <div><span>02</span><strong>Safer runtime</strong><p>Plugin isolation, managed secrets, restore drills, resource quotas and deeper incident automation.</p></div>
          <div><span>03</span><strong>Useful pilots</strong><p>Self-service onboarding, clearer evidence UX and real design-partner feedback before scale.</p></div>
          <div><span>04</span><strong>Responsible scale</strong><p>Legal and commercial foundations only after the live and pilot evidence gates are met.</p></div>
        </div>
      </div>
    </section>

    <section id="workspace" class="xp-section" aria-labelledby="workspace-heading">
      <div class="xp-heading-row">
        <div><p class="xp-eyebrow">Pilot Workspace</p><h2 id="workspace-heading">An operator console, not a performance theatre<span>.</span></h2></div>
        <span class="xp-pill">LIVE · INVITE ONLY</span>
      </div>
      <p class="xp-lead">The console exposes what an operator needs to make a safe decision—and records the dangerous actions. It is no longer read-only: manual orders are available in production, but they cannot bypass capital gates or execution locks.</p>
      <div class="xp-five-grid">
        <div><strong>Home</strong><p>Equity, allocation, position, engine, WebSocket, REST and tick latency.</p></div>
        <div><strong>Signal</strong><p>Current action, reason, confidence and the strategy-owned checklist.</p></div>
        <div><strong>Activity</strong><p>Trade log and durable engine events in order.</p></div>
        <div><strong>Settings</strong><p>Certified preset, bounded risk, exchange and Telegram controls.</p></div>
        <div><strong>Manual trade</strong><p>Preview, expiring challenge, 8–12 digit Trade PIN and full audit trail.</p></div>
      </div>
      <div class="xp-truth-grid">
        <div><strong>High-risk actions step up</strong><p>Live activation requires a recent exchange test, verified TOTP, Trade PIN and a live-certified preset. Admin actions also require verified TOTP.</p></div>
        <div><strong>Pilot limits are intentional</strong><p>Production is capped at three users and two active engines. Public signup returns 404; external onboarding remains invite-only.</p></div>
      </div>
      <a class="xp-button" href="/login">Sign in to the Pilot Workspace <span aria-hidden="true">→</span></a>
    </section>

    <section id="support" class="xp-section" aria-labelledby="support-heading">
      <div class="xp-support-panel">
        <p class="xp-eyebrow">The pitch</p>
        <h2 id="support-heading">Built with skin in the game. Scaled with gates<span>.</span></h2>
        <p>xAuby began as one engineer building the system he was willing to trust with his own money. That founder–operator alignment is still the product filter: research must reproduce, risk must be visible, and growth must wait for evidence.</p>
        <p>The ask is focused: help fund better native data, stronger certification, hardened multi-tenant operations and an honest design-partner pilot. Support accelerates the work; it does not buy signals, managed returns, equity, or access to the owner's trading account.</p>
        <div class="xp-support-actions">
          <a class="xp-button" href="${SUPPORT_URL}" target="_blank" rel="noopener noreferrer">Support development <span aria-hidden="true">→</span></a>
          <a class="xp-button xp-button-secondary" href="${SOURCE_URL}">Collaborate on GitHub</a>
        </div>
        <div class="xp-operator"><div class="xp-avatar" aria-hidden="true">IK</div><div><strong>Itsara Kaewruang</strong><span>Founder · builder · live operator</span></div></div>
      </div>
    </section>
  `;

  const installStyles = () => {
    if (document.getElementById("xauby-pitch-styles")) return;
    const style = document.createElement("style");
    style.id = "xauby-pitch-styles";
    style.textContent = `
      :root { --xp-ink:#ece9e2; --xp-muted:rgba(236,233,226,.62); --xp-faint:rgba(236,233,226,.5); --xp-line:rgba(236,233,226,.2); --xp-panel:#1e1e1b; --xp-accent:#ff431a; --xp-soft:#ff7a4d; }
      html { scroll-behavior:smooth; }
      body { background:#060006; }
      main#top { overflow:visible !important; }
      .xn-hero-scrub { min-height:0 !important; isolation:isolate; margin-left:calc(50% - 50vw); width:100vw; }
      .xn-hero-sticky { position:relative !important; min-height:clamp(620px,88svh,900px) !important; height:auto !important; overflow:hidden; background:#060006; }
      .xn-hero-inner { position:relative; z-index:2; padding-top:clamp(120px,18vh,190px) !important; padding-bottom:clamp(86px,12vh,140px) !important; }
      .xn-hero-content { opacity:1 !important; filter:none !important; transform:none !important; max-width:820px; }
      .xn-hero-title { font-size:clamp(48px,9vw,112px) !important; }
      .xn-hero-actions a { transition:background .18s ease,color .18s ease,border-color .18s ease,transform .18s ease; }
      .xn-hero-actions a:hover { transform:translateY(-1px); }
      .xn-hero-actions a:first-child:hover { background:var(--xp-accent) !important; }
      .xn-hero-actions a:last-child:hover { border-color:var(--xp-soft) !important; color:var(--xp-soft) !important; }
      .xn-particle-wave { opacity:.82; }
      .xp-proof { border-top:1px solid var(--xp-line); border-bottom:1px solid var(--xp-line); padding:20px 0 16px; }
      .xp-proof-label,.xp-kicker,.xp-eyebrow { margin:0; font-size:10.5px; font-weight:650; letter-spacing:.13em; text-transform:uppercase; color:var(--xp-faint); }
      .xp-eyebrow { color:var(--xp-soft); margin-bottom:13px; }
      .xp-footnote { margin:12px 0 0; font-size:11.5px; line-height:1.55; color:var(--xp-faint); }
      .xp-section { padding:clamp(70px,10vw,126px) 0 0; scroll-margin-top:76px; }
      .xp-heading-row { display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between; gap:14px 28px; }
      .xp-heading-row h2,.xp-support-panel h2 { margin:0; max-width:820px; font-size:clamp(30px,4.3vw,54px); font-weight:600; line-height:1.02; letter-spacing:-.035em; text-wrap:balance; }
      .xp-heading-row h2 span,.xp-support-panel h2 span { color:var(--xp-accent); }
      .xp-kicker { max-width:320px; text-align:right; }
      .xp-lead { margin:18px 0 0; max-width:72ch; font-size:15px; line-height:1.65; color:rgba(236,233,226,.7); }
      .xp-strategy-grid,.xp-evidence-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin-top:34px; }
      .xp-three-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin-top:30px; }
      .xp-four-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px; background:var(--xp-line); border:1px solid var(--xp-line); }
      .xp-five-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:1px; margin-top:30px; background:var(--xp-line); border:1px solid var(--xp-line); }
      .xp-card,.xp-panel,.xp-support-panel { border:1px solid rgba(236,233,226,.24); background:var(--xp-panel); }
      .xp-card { padding:clamp(22px,3vw,34px); }
      .xp-card h3 { margin:18px 0 0; font-size:clamp(19px,2vw,25px); font-weight:600; letter-spacing:-.02em; }
      .xp-card p { margin:12px 0 0; font-size:13.5px; line-height:1.62; color:var(--xp-muted); }
      .xp-card-top { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px; }
      .xp-card-num { color:var(--xp-soft); font-size:13px; font-variant-numeric:tabular-nums; }
      .xp-code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:10.5px; color:var(--xp-faint); }
      .xp-pill { display:inline-flex; align-items:center; min-height:28px; padding:0 10px; border:1px solid rgba(236,233,226,.28); font-size:9.5px; font-weight:700; letter-spacing:.09em; color:rgba(236,233,226,.75); }
      .xp-pill-live { color:#161614; background:var(--xp-ink); border-color:var(--xp-ink); }
      .xp-detail-list { list-style:none; margin:20px 0 0; padding:0; }
      .xp-detail-list li { display:grid; grid-template-columns:90px minmax(0,1fr); gap:12px; padding:10px 0; border-top:1px solid rgba(236,233,226,.14); font-size:12px; line-height:1.5; }
      .xp-detail-list strong { color:var(--xp-soft); }
      .xp-detail-list span { color:var(--xp-muted); }
      .xp-flow { list-style:none; margin:36px 0 0; padding:0; }
      .xp-flow li { display:grid; grid-template-columns:64px minmax(0,1fr); gap:16px; padding:20px 0; border-top:1px solid var(--xp-line); }
      .xp-flow>li>span { color:var(--xp-soft); font-size:18px; font-variant-numeric:tabular-nums; }
      .xp-flow h3 { margin:0; font-size:17px; }
      .xp-flow p { margin:5px 0 0; max-width:78ch; color:var(--xp-muted); font-size:13.5px; line-height:1.6; }
      .xp-truth-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1px; margin-top:14px; background:var(--xp-line); border:1px solid var(--xp-line); }
      .xp-truth-grid>div,.xp-four-grid>div,.xp-five-grid>div { background:#151514; padding:20px; }
      .xp-truth-grid strong,.xp-four-grid strong,.xp-five-grid strong { display:block; font-size:13px; color:var(--xp-ink); }
      .xp-truth-grid p,.xp-four-grid p,.xp-five-grid p { margin:7px 0 0; font-size:12px; line-height:1.55; color:var(--xp-muted); }
      .xp-four-grid span { display:block; margin-bottom:12px; color:var(--xp-soft); font-size:11px; }
      .xp-text-link { display:inline-flex; margin-top:24px; color:var(--xp-soft); font-size:13px; font-weight:650; }
      .xp-panel { padding:clamp(26px,4vw,48px); }
      .xp-metric-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); margin-top:12px; }
      .xp-metric-grid>div { min-width:0; padding:16px 14px 10px; border-left:1px solid rgba(236,233,226,.14); }
      .xp-metric-grid strong { display:block; font-size:clamp(25px,3vw,38px); font-weight:600; line-height:1; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
      .xp-metric-grid span { display:block; margin-top:8px; font-size:9.5px; font-weight:600; line-height:1.35; letter-spacing:.09em; text-transform:uppercase; color:var(--xp-faint); }
      .xp-risk-grid { grid-template-columns:repeat(6,minmax(0,1fr)); margin-top:30px; border-top:1px solid var(--xp-line); }
      .xp-risk-grid strong { font-size:clamp(22px,2.6vw,32px); }
      .xp-calculator { margin-top:30px; padding-top:24px; border-top:1px solid var(--xp-line); }
      .xp-calculator-head { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:10px; }
      .xp-calculator h3 { margin:0; font-size:17px; }
      .xp-calculator label { font-size:12px; color:var(--xp-faint); }
      .xp-calculator label strong { color:var(--xp-ink); }
      #xp-equity { width:100%; height:44px; margin-top:8px; accent-color:var(--xp-accent); }
      .xp-budget-grid { margin-top:0; }
      .xp-budget-grid>div { padding:18px; border-top:1px solid var(--xp-line); }
      .xp-budget-grid strong { display:block; color:var(--xp-soft); font-size:clamp(20px,2.4vw,28px); font-variant-numeric:tabular-nums; }
      .xp-budget-grid span { display:block; margin-top:7px; font-size:10px; letter-spacing:.07em; text-transform:uppercase; color:var(--xp-faint); }
      .xp-warning,.xp-definition { margin-top:20px; padding:18px 20px; border:1px solid rgba(255,122,77,.62); background:rgba(255,122,77,.08); }
      .xp-warning strong,.xp-definition strong { font-size:13px; }
      .xp-warning p,.xp-definition p { margin:7px 0 0; max-width:88ch; font-size:12px; line-height:1.62; color:rgba(236,233,226,.74); }
      .xp-certificate-metrics { grid-template-columns:repeat(5,minmax(0,1fr)); margin-top:24px; border-top:1px solid var(--xp-line); }
      .xp-certificate-metrics>div { padding-left:10px; }
      .xp-certificate-metrics strong { font-size:clamp(19px,2.1vw,27px); }
      .xp-caveat { padding-top:14px; border-top:1px solid rgba(236,233,226,.14); }
      .xp-caveat strong { color:var(--xp-ink); }
      .xp-roadmap { list-style:none; margin:34px 0 0; padding:0; border-top:1px solid var(--xp-line); }
      .xp-roadmap li { display:grid; grid-template-columns:100px minmax(0,1fr); gap:20px; padding:26px 0; border-bottom:1px solid var(--xp-line); }
      .xp-roadmap-marker { width:max-content; padding:7px 10px; border:1px solid rgba(236,233,226,.28); color:var(--xp-muted); font-size:10px; font-weight:700; letter-spacing:.1em; }
      .xp-roadmap .is-current .xp-roadmap-marker { background:var(--xp-accent); color:#fff; border-color:var(--xp-accent); }
      .xp-roadmap h3 { margin:0; font-size:20px; }
      .xp-roadmap p { margin:8px 0 0; max-width:76ch; color:var(--xp-muted); font-size:13.5px; line-height:1.6; }
      .xp-roadmap div>strong { display:block; margin-top:11px; color:var(--xp-soft); font-size:10px; letter-spacing:.1em; }
      .xp-use-of-support { margin-top:34px; }
      .xp-use-of-support>h3 { margin:0 0 14px; font-size:20px; }
      .xp-button { display:inline-flex; align-items:center; justify-content:center; min-height:48px; margin-top:28px; padding:0 26px; background:var(--xp-ink); color:#161614; font-size:13.5px; font-weight:650; border:1px solid var(--xp-ink); transition:background .18s ease,border-color .18s ease,transform .18s ease; }
      .xp-button:hover { background:var(--xp-accent); border-color:var(--xp-accent); color:#161614; transform:translateY(-1px); }
      .xp-button-secondary { background:transparent; color:var(--xp-ink); border-color:rgba(236,233,226,.35); }
      .xp-support-panel { padding:clamp(30px,5vw,58px); }
      .xp-support-panel>p:not(.xp-eyebrow) { max-width:72ch; margin:18px 0 0; color:rgba(236,233,226,.7); font-size:15px; line-height:1.65; }
      .xp-support-actions { display:flex; flex-wrap:wrap; gap:12px; }
      .xp-operator { display:flex; align-items:center; gap:13px; margin-top:36px; padding-top:24px; border-top:1px solid var(--xp-line); }
      .xp-avatar { display:grid; place-items:center; width:48px; height:48px; border:1px solid rgba(236,233,226,.3); color:var(--xp-soft); font-size:13px; font-weight:700; }
      .xp-operator strong,.xp-operator span { display:block; }
      .xp-operator span { margin-top:3px; font-size:11px; color:var(--xp-faint); }
      .xauby-login-link { display:inline-flex !important; align-items:center; justify-content:center; min-height:40px; padding:0 16px !important; background:var(--xp-accent) !important; border:1px solid var(--xp-accent) !important; color:#fff !important; font-size:12px !important; font-weight:650 !important; white-space:nowrap; }
      .xauby-donate-link { color:var(--xp-soft) !important; }
      @media (max-width:1023px) {
        header nav>.xn-desktop-only { display:none !important; }
        .xp-five-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .xp-four-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .xp-risk-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
      }
      @media (max-width:760px) {
        .xn-hero-sticky { min-height:700px !important; }
        .xn-hero-inner { align-self:auto !important; padding-top:130px !important; }
        .xn-gold-coin { transform:translate(90px,-145px) scale(.68); transform-origin:center center; }
        .xp-strategy-grid,.xp-evidence-grid,.xp-three-grid,.xp-truth-grid { grid-template-columns:1fr; }
        .xp-metric-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .xp-risk-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .xp-certificate-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }
        .xp-four-grid,.xp-five-grid { grid-template-columns:1fr; }
        .xp-kicker { text-align:left; }
        .xp-roadmap li { grid-template-columns:1fr; gap:12px; }
        .xp-flow li { grid-template-columns:44px minmax(0,1fr); }
      }
      @media (prefers-reduced-motion:reduce) { html { scroll-behavior:auto; } * { animation:none !important; transition:none !important; } }
    `;
    (document.head || document.documentElement).append(style);
  };

  const renderPitch = () => {
    const main = document.getElementById("top");
    if (!main || main.querySelector("sc-for, sc-if") || document.getElementById("pitch-proof")) return false;
    main.innerHTML = PITCH_MARKUP;
    return true;
  };

  const installBudgetCalculator = () => {
    const input = document.getElementById("xp-equity");
    if (!input || input.dataset.ready) return;
    input.dataset.ready = "true";
    const format = (value) => Math.round(value).toLocaleString("en-US") + " USDT";
    const update = () => {
      const equity = Number(input.value);
      const label = document.getElementById("xp-equity-label");
      if (label) label.textContent = format(equity);
      document.querySelectorAll("[data-budget]").forEach((node) => {
        node.textContent = format(equity * Number(node.dataset.budget));
      });
    };
    input.addEventListener("input", update);
    update();
  };

  const makeNavLink = (href, label, footer = false) => {
    const link = document.createElement("a");
    link.href = href;
    link.textContent = label;
    if (!footer) link.className = "xn-desktop-only";
    link.style.cssText = footer
      ? "display:inline-flex;align-items:center;min-height:40px;font-size:13px;font-weight:500;color:rgba(236,233,226,.72);"
      : "display:inline-flex;align-items:center;min-height:44px;padding:0 4px;font-size:12.5px;font-weight:500;letter-spacing:.02em;color:rgba(236,233,226,.72);";
    return link;
  };

  const syncHeader = () => {
    const nav = document.querySelector("header nav");
    if (!nav || !document.getElementById("pitch-proof") || nav.dataset.xaubyPitchNav) return;
    nav.dataset.xaubyPitchNav = "true";
    nav.querySelectorAll(":scope > a").forEach((link) => link.remove());
    const badge = [...nav.children].find((node) => node.tagName === "SPAN");
    NAV_ITEMS.forEach(([href, label]) => nav.insertBefore(makeNavLink(href, label), badge || null));
    if (badge) {
      const dot = document.createElement("span");
      dot.style.cssText = "width:7px;height:7px;border-radius:50%;background:#ff431a;";
      badge.textContent = "OWNER LIVE";
      badge.prepend(dot);
      badge.setAttribute("aria-label", "Owner trading engine is live");
    }
    const login = document.createElement("a");
    login.href = "/login";
    login.textContent = "Sign in";
    login.className = "xauby-login-link";
    nav.append(login);
  };

  const syncFooter = () => {
    const footer = document.querySelector("footer");
    if (!footer || !document.getElementById("pitch-proof") || footer.dataset.xaubyPitchFooter) return;
    footer.dataset.xaubyPitchFooter = "true";
    const paragraphs = footer.querySelectorAll("p");
    if (paragraphs[1]) paragraphs[1].textContent = "Evidence-governed gold + bitcoin trading platform";
    if (paragraphs[2]) paragraphs[2].textContent = "For research and education. xAuby trades the founder's capital; it does not accept deposits, sell signals or guarantee results. Backtests are point-in-time evidence, not forecasts. Trading can lose substantial capital.";
    const nav = footer.querySelector("nav");
    if (!nav) return;
    nav.innerHTML = "";
    NAV_ITEMS.forEach(([href, label]) => nav.append(makeNavLink(href, label, true)));
    const github = makeNavLink(SOURCE_URL, "GitHub — iisara555/xAuby", true);
    github.style.color = "#ff7a4d";
    nav.append(github);
    const support = makeNavLink(SUPPORT_URL, "Support development", true);
    support.className = "xauby-donate-link";
    support.target = "_blank";
    support.rel = "noopener noreferrer";
    nav.append(support);
  };

  let settleTimer;
  const apply = () => {
    installStyles();
    renderPitch();
    installBudgetCalculator();
    syncHeader();
    syncFooter();
    if (
      !settleTimer &&
      document.getElementById("pitch-proof") &&
      document.querySelector("header nav")?.dataset.xaubyPitchNav &&
      document.querySelector("footer")?.dataset.xaubyPitchFooter
    ) {
      settleTimer = window.setTimeout(() => observer.disconnect(), 15000);
    }
  };

  const observer = new MutationObserver(apply);
  observer.observe(document, { childList: true, subtree: true });
  apply();
})();
