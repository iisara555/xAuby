(() => {
  const sourceUrl = "https://github.com/iisara555/xAuby";
  // A Stripe-hosted payment link, so this is a plain outbound navigation and
  // nothing has to be loaded from Stripe. Embedding Checkout or a pricing
  // table would need js.stripe.com, which the page's CSP (script-src 'self',
  // set in Website/next.config.ts) blocks — and relaxing that for a donate
  // widget would weaken the whole page.
  const donateUrl = "https://donate.stripe.com/4gM9AU5lOc2s9o88d74Ni00";

  // The product UI (Website/app/globals.css) is built on cut corners, not
  // rounded rectangles — the landing page's own design agrees, using radius
  // only for 50% dots. We tag surfaces here and let CSS own the geometry, so
  // the treatment survives after the MutationObserver disconnects.
  const CONTROL_TAGS = new Set(["BUTTON", "INPUT", "SELECT", "TEXTAREA"]);
  // Below this, an element is typography or a chip, not a surface. The old
  // blanket selector matched inline-gradient text spans, which is exactly how
  // a corner treatment ends up on a headline.
  const MIN_SURFACE = 96;

  const tagCutCorners = (element) => {
    if (element.dataset.xnCut) return;
    const inlineStyle = element.getAttribute("style") || "";
    if (inlineStyle.includes("border-radius: 50%") || element.style.borderRadius === "50%") return;
    // Never clip the pinned hero or any scroll-pinned ancestor: clip-path
    // establishes a containing block and would break sticky positioning.
    if (element.closest("[data-hero-scrub], .xn-hero-sticky")) return;

    if (CONTROL_TAGS.has(element.tagName)) {
      element.dataset.xnCut = "control";
      return;
    }
    const rect = element.getBoundingClientRect();
    if (rect.width < MIN_SURFACE || rect.height < MIN_SURFACE) return;
    if (window.getComputedStyle(element).position === "sticky") return;
    element.dataset.xnCut = "card";
  };

  const applyCutCornerSystem = () => {
    document
      .querySelectorAll("button, input, select, textarea, [style*='background'], [style*='border:']")
      .forEach((element) => tagCutCorners(element));
  };

  // Everything visual lives in CSS rather than inline styles: the observer
  // below disconnects ~15s after load, and CSS keeps working after that.
  const installPolishStyles = () => {
    if (document.getElementById("xauby-polish")) return;
    const style = document.createElement("style");
    style.id = "xauby-polish";
    style.textContent = `
      :root {
        --xn-card-cut: 14px;
        --xn-control-cut: 7px;
        --xn-accent: #ff431a;
        --xn-ease: cubic-bezier(.22,.61,.36,1);
      }

      /* --- Cut corners, mirroring globals.css. clip-path also clips the box
         shadow, so drop it the way the product cards do. --- */
      @supports (clip-path: polygon(0 0)) {
        [data-xn-cut="card"] {
          border-radius: 0 !important;
          box-shadow: none !important;
          clip-path: polygon(
            var(--xn-card-cut) 0, 100% 0,
            100% calc(100% - var(--xn-card-cut)),
            calc(100% - var(--xn-card-cut)) 100%,
            0 100%, 0 var(--xn-card-cut)
          );
        }
        [data-xn-cut="control"] {
          border-radius: 0 !important;
          clip-path: polygon(
            var(--xn-control-cut) 0, 100% 0,
            100% calc(100% - var(--xn-control-cut)),
            calc(100% - var(--xn-control-cut)) 100%,
            0 100%, 0 var(--xn-control-cut)
          );
        }
      }

      @media (prefers-reduced-motion: no-preference) {
        /* --- Reveal: crisper easing, and a cascade so a list arrives as a
           sequence instead of one block. --- */
        [style*="translateY(14px)"],
        [style*="translateY(12px)"] {
          transition-duration: 420ms !important;
          transition-timing-function: var(--xn-ease) !important;
        }
        /* The revealed items carry an inline \`transition\` shorthand, which
           resets transition-delay to 0s and outranks this sheet — so the
           cascade has to be !important to land. Scoped to the reveal
           signature so ordinary lists are untouched. */
        li[style*="transition"]:nth-child(1) { transition-delay: 0ms !important; }
        li[style*="transition"]:nth-child(2) { transition-delay: 45ms !important; }
        li[style*="transition"]:nth-child(3) { transition-delay: 90ms !important; }
        li[style*="transition"]:nth-child(4) { transition-delay: 135ms !important; }
        li[style*="transition"]:nth-child(5) { transition-delay: 180ms !important; }
        li[style*="transition"]:nth-child(6) { transition-delay: 225ms !important; }
        li[style*="transition"]:nth-child(7) { transition-delay: 270ms !important; }
        li[style*="transition"]:nth-child(n+8) { transition-delay: 315ms !important; }

        /* --- Hover: quick in, slower out, with a lift and an accent edge. --- */
        [data-xn-cut="card"], [data-xn-cut="control"] {
          transition: border-color 200ms var(--xn-ease), background 200ms var(--xn-ease),
                      transform 200ms var(--xn-ease);
        }
        [data-xn-cut="card"]:hover, [data-xn-cut="control"]:hover {
          transition-duration: 120ms;
          transform: translateY(-1px);
        }
        [data-xn-cut="control"]:hover { border-color: var(--xn-accent) !important; }

        /* --- Signature moment: the regime router re-deciding. The decision
           panel is the aria-live region; when its text changes the LED pulses,
           the verdict rises into place and an accent rule sweeps the rule
           above it. --- */
        [aria-live="polite"] > span[aria-hidden="true"] {
          transition: background 260ms var(--xn-ease), box-shadow 260ms var(--xn-ease);
        }
        .xn-router-changed > span[aria-hidden="true"] {
          animation: xn-led-pulse 620ms var(--xn-ease);
        }
        .xn-router-changed > div > div > strong {
          animation: xn-verdict-rise 380ms var(--xn-ease);
        }
        .xn-router-changed::before {
          content: "";
          position: absolute; left: 0; top: -1px; height: 1px;
          background: var(--xn-accent);
          animation: xn-rule-sweep 560ms var(--xn-ease);
        }
        @keyframes xn-led-pulse {
          0%   { transform: scale(1);   box-shadow: 0 0 0 1px rgba(236,233,226,.25); }
          38%  { transform: scale(1.5); box-shadow: 0 0 0 6px rgba(255,67,26,.16); }
          100% { transform: scale(1);   box-shadow: 0 0 0 1px rgba(236,233,226,.25); }
        }
        @keyframes xn-verdict-rise {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: none; }
        }
        @keyframes xn-rule-sweep {
          from { width: 0; opacity: 1; }
          to   { width: 100%; opacity: 0; }
        }
      }

      /* Pressed regime reads as selected, not merely hovered. */
      [aria-pressed="true"] {
        border-color: var(--xn-accent) !important;
        box-shadow: inset 0 0 0 1px rgba(255,67,26,.35);
      }
    `;
    document.head.appendChild(style);
  };

  // Flag the decision panel for one animation cycle whenever the router's
  // verdict actually changes. Text-driven, so it works for any control that
  // updates the live region.
  //
  // This gets its own observer rather than riding the main one: that one is
  // disconnected ~15s after load, which is precisely when a visitor starts
  // clicking the regime buttons.
  let routerVerdict = null;
  let routerWatched = null;

  const flashRouter = (panel) => {
    const verdict = panel.textContent?.trim() ?? "";
    if (!verdict) return;
    if (routerVerdict === null) { routerVerdict = verdict; return; }
    if (verdict === routerVerdict) return;
    routerVerdict = verdict;
    if (getComputedStyle(panel).position === "static") panel.style.position = "relative";
    panel.classList.remove("xn-router-changed");
    void panel.offsetWidth; // restart the animation
    panel.classList.add("xn-router-changed");
  };

  const installRouterWatch = () => {
    const panel = document.querySelector('[aria-live="polite"]');
    if (!panel || routerWatched === panel) return;
    routerWatched = panel;
    routerVerdict = panel.textContent?.trim() || null;
    new MutationObserver(() => flashRouter(panel)).observe(panel, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  };

  const replaceExactText = (selector, from, to) => {
    document.querySelectorAll(selector).forEach((element) => {
      if (element.textContent?.trim() === from) element.textContent = to;
    });
  };

  // Stat grids render as a value element followed by its label. Scoped to
  // `root` so a label like "Win rate" or "Max drawdown" — reused across the
  // hero strip, the backtest section, and the research report cards with
  // different values in each — only updates the one instance it's meant to.
  //
  // The label is NOT always a leaf: most of these render as
  // `<div><span>Win rate</span></div>`, so the old `children.length === 0`
  // test skipped the wrapper (which has the value as its sibling) and matched
  // only the inner span (which has no sibling at all). Every figure on the page
  // silently kept its stale value. Match on text, then climb to the first
  // ancestor that actually has a preceding sibling.
  const setStatByLabel = (root, labelText, newValue) => {
    if (!root) return;
    root.querySelectorAll("div, span, p").forEach((el) => {
      if (el.textContent?.trim() !== labelText) return;

      let node = el;
      while (node && node !== root && !node.previousElementSibling) {
        node = node.parentElement;
      }
      const valueEl = node && node !== root ? node.previousElementSibling : null;
      if (valueEl && valueEl.textContent?.trim() !== newValue) {
        valueEl.textContent = newValue;
      }
    });
  };

  // Remove a risk/backtest stat block entirely (value + label pair) by its
  // label text, scoped to `root`.
  const removeStatByLabel = (root, labelText) => {
    if (!root) return;
    root.querySelectorAll("div, span").forEach((el) => {
      if (el.children.length === 0 && el.textContent?.trim() === labelText && el.parentElement) {
        el.parentElement.remove();
      }
    });
  };

  // #bt-big carries its own count-up animation baked into the compiled
  // bundle, targeting a stale hardcoded number — it can re-fire on scroll
  // independently of this file's observer, so a one-time text overwrite
  // isn't reliable. Watch the element directly and snap it back every time
  // the animation (or anything else) changes it.
  const CERTIFIED_XAU_NET = "+80.72%";
  const fixBigBacktestNumber = () => {
    const big = document.getElementById("bt-big");
    if (!big) return;
    if (big.textContent.trim() !== CERTIFIED_XAU_NET) big.textContent = CERTIFIED_XAU_NET;
    if (!big.dataset.xnBtWatched) {
      big.dataset.xnBtWatched = "true";
      new MutationObserver(() => {
        if (big.textContent.trim() !== CERTIFIED_XAU_NET) big.textContent = CERTIFIED_XAU_NET;
      }).observe(big, { childList: true, characterData: true, subtree: true });
    }
  };

  const supportsOverflowClip = () =>
    typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("overflow-x", "clip");

  const setStyle = (element, prop, value) => {
    if (element.style[prop] !== value) element.style[prop] = value;
  };

  const installHeroScrollFixStyles = () => {
    if (document.getElementById("xauby-hero-scroll-fix")) return;
    const style = document.createElement("style");
    style.id = "xauby-hero-scroll-fix";
    style.textContent = `
      .xn-hero-scrub {
        isolation: isolate;
        min-height: 0 !important;
        --hero-blur: 0px !important;
        --hero-copy-blur: 0px !important;
        --hero-copy-opacity: 1 !important;
        --hero-copy-y: 0px !important;
      }
      /* No video, no scroll-jack: the hero is a normal top-of-page block now,
         sized to its own content instead of pinned at a forced 100svh — that
         forced height was the source of the large empty band beneath the
         copy. Flat background instead of a gradient overlay: .xn-hero-sticky
         already carries #060006 from the compiled template. */
      .xn-hero-sticky {
        position: static !important;
        height: auto !important;
        min-height: 0 !important;
      }
      .xn-hero-content {
        opacity: 1 !important;
        filter: none !important;
        transform: none !important;
      }
      @media (max-width: 760px) {
        .xn-hero-inner {
          padding-top: clamp(96px, 15vh, 140px) !important;
        }
      }
      .xauby-login-link {
        display: inline-flex !important;
        align-items: center;
        justify-content: center;
        min-height: 42px;
        padding: 0 18px !important;
        border: 1px solid #ff431a !important;
        border-radius: 999px !important;
        background: #ff431a !important;
        color: #fff !important;
        font-size: 12.5px !important;
        font-weight: 650 !important;
        letter-spacing: .02em !important;
        line-height: 1 !important;
        text-decoration: none !important;
        white-space: nowrap;
        box-shadow: 0 8px 28px rgba(255,67,26,.2);
        transition: transform .18s ease, background .18s ease, border-color .18s ease;
      }
      .xauby-login-link:hover {
        transform: translateY(-1px);
        border-color: #ff6a3c !important;
        background: #ff6a3c !important;
      }
      .xauby-login-link:focus-visible {
        outline: 2px solid #ece9e2;
        outline-offset: 3px;
      }
      footer .xauby-login-link {
        min-height: 38px;
        padding-inline: 15px !important;
      }
      /* Outline rather than solid: Sign in is the page's primary action and
         should stay the only filled control in the footer. */
      .xauby-donate-link {
        display: inline-flex !important;
        align-items: center;
        justify-content: center;
        min-height: 38px;
        margin-top: 8px;
        padding: 0 15px !important;
        border: 1px solid rgba(255,122,77,.62) !important;
        border-radius: 999px !important;
        background: transparent !important;
        color: #ff7a4d !important;
        font-size: 12.5px !important;
        font-weight: 600 !important;
        letter-spacing: .02em !important;
        line-height: 1 !important;
        text-decoration: none !important;
        white-space: nowrap;
        transition: border-color .18s ease, color .18s ease, background .18s ease;
      }
      .xauby-donate-link:hover {
        border-color: #ff6a3c !important;
        background: rgba(255,122,77,.1) !important;
        color: #ff9468 !important;
      }
      .xauby-donate-link:focus-visible {
        outline: 2px solid #ece9e2;
        outline-offset: 3px;
      }
      header nav {
        flex-wrap: nowrap;
        min-width: 0;
      }
      /* The section nav needs ~1000px before every link fits next to the
         wordmark; below that only the Sign in action stays, so it can never
         be pushed past the right edge on phones or tablets. */
      @media (max-width: 1023px) {
        header nav {
          flex: 0 0 auto;
          gap: 0 !important;
        }
        header nav > :not(.xauby-login-link) {
          display: none !important;
        }
        header .xauby-login-link {
          min-height: 40px;
          padding-inline: 15px !important;
        }
      }
    `;
    (document.head || document.documentElement).append(style);
  };

  const stripHeroVideo = (heroSection) => {
    heroSection.querySelectorAll("video").forEach((video) => video.remove());
    // A gradient overlay used to sit here in place of the video. Removed:
    // .xn-hero-sticky already carries the flat #060006 page background from
    // the compiled template, so no replacement element is needed.
    heroSection.querySelectorAll(".xauby-hero-gradient").forEach((el) => el.remove());
  };

  const applyHeroScrollFix = () => {
    const heroSection = document.querySelector("[data-hero-scrub]");
    if (!heroSection) return;

    installHeroScrollFixStyles();
    heroSection.style.setProperty("--hero-blur", "0px", "important");
    heroSection.style.setProperty("--hero-copy-blur", "0px", "important");
    heroSection.style.setProperty("--hero-copy-opacity", "1", "important");
    heroSection.style.setProperty("--hero-copy-y", "0px", "important");
    heroSection.style.setProperty("min-height", "0", "important");
    stripHeroVideo(heroSection);

    const overflowX = supportsOverflowClip() ? "clip" : "visible";
    setStyle(document.documentElement, "overflowX", overflowX);
    if (document.body) setStyle(document.body, "overflowX", overflowX);

    let node = heroSection.parentElement;
    while (node && node !== document.documentElement) {
      const inlineStyle = node.getAttribute("style") || "";
      const computed = window.getComputedStyle(node);
      if (inlineStyle.includes("overflow-x: hidden") || computed.overflowX === "hidden") {
        setStyle(node, "overflowX", overflowX);
        if (!["auto", "scroll", "hidden"].includes(computed.overflowY)) {
          setStyle(node, "overflowY", "visible");
        }
      }
      node = node.parentElement;
    }
  };

  const SECTION_LINK_STYLE =
    "display:inline-flex;align-items:center;min-height:44px;padding:0 4px;font-size:12.5px;font-weight:600;letter-spacing:.02em;color:#ff7a4d;";

  const addSectionLink = (nav, href, label) => {
    if (!nav || nav.querySelector(`a[href='${href}']`)) return;
    const link = document.createElement("a");
    link.href = href;
    link.textContent = label;
    link.style.cssText = SECTION_LINK_STYLE;
    nav.prepend(link);
  };

  const addResearchLink = (nav) => addSectionLink(nav, "#research-report", "Research report");
  const addBtcLink = (nav) => addSectionLink(nav, "#btc-pair", "BTC");

  /**
   * A nav is a table of contents, so its order has to match the order of the
   * sections on the page. Both links above are added before the bundle has
   * rendered the rest of the menu, which left the page's two LAST sections
   * sitting first in the bar. Sorting by the target's real offset fixes that
   * regardless of when each link arrives.
   *
   * Only in-page links move; the LIVE badge and Sign in are not sections and
   * keep their place at the end.
   */
  const sortNavBySectionOrder = (nav) => {
    if (!nav) return;
    const links = [...nav.querySelectorAll(":scope > a[href^='#']")];
    if (links.length < 2) return;

    const positioned = links.map((link) => {
      const target = document.getElementById(link.getAttribute("href").slice(1));
      // A link whose section has not been injected yet sorts last, which is
      // where both of ours belong anyway.
      return { link, top: target ? target.getBoundingClientRect().top + window.scrollY : Infinity };
    });

    const sorted = [...positioned].sort((a, b) => a.top - b.top);
    // Already correct: bail rather than churn the DOM on every mutation.
    if (sorted.every((entry, i) => entry.link === positioned[i].link)) return;

    const marker = links[links.length - 1].nextSibling;
    sorted.forEach(({ link }) => nav.insertBefore(link, marker));
  };

  const addLoginLink = (nav) => {
    if (!nav || nav.querySelector("[data-xauby-login]")) return;
    const link = document.createElement("a");
    link.href = "/login";
    link.textContent = "Sign in";
    link.className = "xauby-login-link";
    link.dataset.xaubyLogin = "true";
    link.setAttribute("aria-label", "Sign in to xAuby");
    nav.append(link);
  };

  /**
   * Footer only, deliberately. The mobile rule above hides everything in the
   * header nav except the Sign in action, so a donate link placed there would
   * simply vanish below 1024px. The footer nav has no such rule and is where
   * a support link conventionally belongs anyway.
   *
   * "Support development" rather than anything implying signals or returns:
   * this is a donation toward the project, not the purchase of a service.
   */
  const addDonateLink = (nav) => {
    if (!nav || nav.querySelector("[data-xauby-donate]")) return;
    const link = document.createElement("a");
    link.href = donateUrl;
    link.textContent = "Support development";
    link.className = "xauby-donate-link";
    link.dataset.xaubyDonate = "true";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", "Support xAuby development via Stripe");
    nav.append(link);
  };

  const updateCurrentConfiguration = () => {
    replaceExactText("span", "LIVE: cdc_action_zone", "LIVE: xauby_actionzone");
    replaceExactText("span", "LIVE: xauby_actionzone", "LIVE: 2 strategies");
    replaceExactText("span", "RUNNING: 1 pair", "LIVE: 2 pairs · XAU + BTC");
    replaceExactText("span", "RUNNING: OKX only", "OKX swap · isolated · 1×");
    replaceExactText("h3", "Strategy plugins", "Live strategies");
    replaceExactText("h2", "One market today. Built for many.", "Two markets today. Built for many.");

    document.querySelectorAll("p").forEach((paragraph) => {
      const text = paragraph.textContent?.trim();
      if (text === "xAuby researches, tests, and trades one market: gold. Every rule in the live system earns its place through documented research — 200+ configurations tested against six years of history, walk-forward validated, published as reports — before it touches real capital.") {
        paragraph.textContent =
          "xAuby researches, tests, and trades two markets: gold and bitcoin. Every rule in the live system earns its place through documented research — hundreds of configurations tested against years of history, walk-forward validated, published as reports — before it touches real capital.";
      }
      if (text === "Orders go to the exchange with a stop-loss attached before entry — protection lives on the exchange, not in the bot's memory.") {
        paragraph.textContent =
          "Orders are evaluated under the configured risk gates. XAU trades long-only with a daily-timeframe regime filter and no exchange stop (exits are rule-driven); BTC trades long and short with a real ATR stop and trailing on the exchange. Neither pair's regime router is enabled — routing between strategies is disabled in production.";
      }
      if (text === "The owner monitors from a terminal, a phone dashboard, and Telegram alerts. He watches; the system trades.") {
        paragraph.textContent =
          "The operator monitors from the web dashboard, a terminal, and Telegram alerts — every control passes through the engine's own safeguards.";
      }
    });
  };

  // Hero strip + Backtest section + Risk section: replace the pre-rename
  // figures (PAXGUSDT proxy, 6y, PF 1.78/WR 36.4%/DD 18.52%, 1 open position,
  // 50% partial TP) with the certified 2026-07-29 okx-xau-actionzone-v1
  // numbers (OKX XAUT-USDT 4h, 4.0y, PF 2.18/WR 45.1%/DD 8.5%/net +80.72%/102
  // trades) and the current risk config (2 open positions, no partial TP).
  const updateBacktestAndRiskFigures = () => {
    const heroStrip = document.querySelector('section[aria-label="Backtest score"]');
    const backtestSection = document.getElementById("backtest");
    const riskSection = document.getElementById("risk");

    replaceExactText("p", "As-deployed backtest — PAXGUSDT proxy, 6 years, 176 trades", "As-deployed backtest — certified on OKX XAUT-USDT, 4.0 years, 102 trades");
    // Set under both labels: apply() runs on every mutation, so by the second
    // pass the label has already been rewritten and the original no longer
    // matches. Whichever one is present, the value lands.
    setStatByLabel(heroStrip, "Net profit, 6 years", CERTIFIED_XAU_NET);
    replaceExactText("div", "Net profit, 6 years", "Net profit, 4.0 years");
    setStatByLabel(heroStrip, "Net profit, 4.0 years", CERTIFIED_XAU_NET);
    setStatByLabel(heroStrip, "Win rate", "45.1%");
    setStatByLabel(heroStrip, "Profit factor", "2.18");
    setStatByLabel(heroStrip, "Max drawdown", "8.5%");

    replaceExactText("p", "Backtest — 6 years · PAXGUSDT proxy · 12,810 4h bars · 150+ runs", "Backtest — certified 2026-07-29 · OKX XAUT-USDT 4h · 432-cell grid search");
    replaceExactText("h2", "From −16.9% to +92.8% on the same six years.", "Certified net positive on real exchange data.");
    replaceExactText("p", "The unfiltered base strategy lost 16.9% over six years of gold history. Two disciplined changes — a trend filter and a partial take-profit — turned the same rules into +92.8%, across 150+ documented runs. The exact configuration that won the backtest is the one running live today.", "This long-only, D1-filtered configuration was selected from a 432-cell parameter grid and re-measured directly on OKX XAUT-USDT 4h — not the PAXGUSDT proxy used during the search. It cleared the pre-registered acceptance gate net positive against its long-only baseline, and it's the exact configuration running live today.");
    replaceExactText("span", "Net profit, 6 years · 10,000 → 19,281 USDT", "Net profit, 4.0 years · 10,000 → 18,072 USDT");
    fixBigBacktestNumber();
    setStatByLabel(backtestSection, "Win rate", "45.1%");
    setStatByLabel(backtestSection, "Profit factor", "2.18");
    setStatByLabel(backtestSection, "Max drawdown", "8.5%");
    // No sourced payoff-ratio figure for the certified config — swap the slot
    // for a number that is sourced, rather than guess.
    setStatByLabel(backtestSection, "Payoff ratio", "102");
    replaceExactText("div", "Payoff ratio", "Trades");
    setStatByLabel(backtestSection, "Trades", "102");
    replaceExactText("p", "Backtest only, not a guarantee of future returns. Figures describe PAXGUSDT 4h history (Aug 2020 – Jul 2026) used as a proxy for XAU, which lacks deep history on its own; capital compounds across trades, so total return carries higher variance than win rate, profit factor, or drawdown.", "Certified on OKX XAUT-USDT 4h (Jul 2022 – Jul 2026), net of fee/slippage/funding. Selected from the same 432-cell grid it's measured against, with no pristine holdout — treat as evidence, not a guarantee. Capital compounds across trades, so total return carries higher variance than win rate, profit factor, or drawdown.");

    // The per-year bars are from the old 6-year PAXGUSDT run: they start in
    // 2020, while the certified series is OKX XAUT-USDT Jul 2022 – Jul 2026.
    // No per-year breakdown exists for the certified run in any research
    // document, so the honest fix is to label the source rather than invent
    // numbers or silently drop the two years that fall outside it.
    replaceExactText("div", "Annual return", "Annual return · PAXGUSDT proxy run");
    replaceExactText("span", "Annual return", "Annual return · PAXGUSDT proxy run");
    replaceExactText("p", "Annual return", "Annual return · PAXGUSDT proxy run");

    // The research library documents studies R1–R4, all run on the 6-year
    // PAXGUSDT series. Their figures are correct as history and are left alone.
    // What is not correct is presenting the config they measured as the current
    // one: R2 is titled "the as-deployed config" but describes the 176-trade
    // profile that the 2026-07-29 certificate replaced, and R1 cites a 1.78
    // headline that is now 2.18.
    ["h3", "h4", "strong", "div", "span"].forEach((selector) => {
      replaceExactText(selector, "Deep dive — the as-deployed config", "Deep dive — the config deployed in July 2026");
    });

    replaceExactText(
      "p",
      "~200 configurations tested with an institutional protocol: 70/30 in-sample/out-of-sample split plus 5-fold walk-forward. The most robust config changes just three values from live — long-only, D1 regime filter, wider fresh-zone window — and is the only leader profitable in both the 2021–23 sideways years and the 2024–26 bull run. (Under this stricter protocol the live config scores PF 1.64 — its headline 1.78 comes from the full-period deep dive.)",
      "~200 configurations tested with an institutional protocol: 70/30 in-sample/out-of-sample split plus 5-fold walk-forward. Its recommendation — long-only, D1 regime filter, wider fresh-zone window — is what the live system became: the certificate above measures exactly that profile on OKX XAUT-USDT. The PF figures in this card come from this study's own stricter protocol on the PAXGUSDT series, not from the certified run."
    );

    replaceExactText(
      "p",
      "Trade-by-trade anatomy of all 176 trades: the system loses small and often (median loss under 1%) and pays for it with a right tail of +4% to +12% winners, held 3.9× longer than losers. The slope filter cut max drawdown from 35% to 18.5% while removing only 4 trades.",
      "Trade-by-trade anatomy of all 176 trades of the configuration live at the time, since replaced by the 2026-07-29 certified profile: the system loses small and often (median loss under 1%) and pays for it with a right tail of +4% to +12% winners, held 3.9× longer than losers. The slope filter cut max drawdown from 35% to 18.5% while removing only 4 trades."
    );

    setStatByLabel(riskSection, "Open position, max", "2");
    removeStatByLabel(riskSection, "Partial TP at +12%");

    // R3's recommendation predates the current config: BTC has since gone
    // live as its own certified pair, not a gold-portfolio sleeve.
    replaceExactText(
      "p",
      "The two systems' equity curves are uncorrelated (0.015), so blending is real diversification: a 20% BTC sleeve cuts max drawdown 23% while giving up under 1pp of annual return. Recommendation: keep 100% gold live, prove BTC in forward simulation first.",
      "The two systems' equity curves are uncorrelated (0.015), so blending is real diversification: a 20% BTC sleeve cuts max drawdown 23% while giving up under 1pp of annual return. This recommendation predates the current config — BTC (SuperTrend EMA200) has since gone live as its own certified pair rather than a gold-portfolio sleeve; see the BTC section below."
    );
  };

  const addResearchReport = () => {
    if (document.getElementById("research-report")) return;
    // Insert inside <main>, not before <footer> directly: <main> carries the
    // page's max-width/centering (1160px + side padding); footer is its
    // sibling, so a section placed outside it stretches full width instead
    // of matching every other section.
    const main = document.getElementById("top");
    if (!main) return;

    const section = document.createElement("section");
    section.id = "research-report";
    section.setAttribute("aria-labelledby", "research-report-heading");
    section.style.cssText =
      "margin:0 auto;padding:0 0 clamp(64px,9vw,112px);scroll-margin-top:76px;";
    section.innerHTML = `
      <div style="border-top:1px solid rgba(236,233,226,.25);padding-top:clamp(40px,7vw,80px);">
        <p style="margin:0 0 14px;font-size:10.5px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#ff7a4d;">Research report · July 2026</p>
        <div style="max-width:760px;margin-bottom:28px;">
          <h2 id="research-report-heading" style="margin:0;font-size:clamp(30px,4.5vw,56px);font-weight:600;line-height:1;letter-spacing:-.03em;">Both sides of gold<span style="color:#ff431a;">.</span></h2>
          <p style="margin:18px 0 0;max-width:66ch;font-size:15px;line-height:1.65;color:rgba(236,233,226,.7);">CDC ActionZone was re-validated across a full market cycle — the 2024–2025 gold rally (+114%) and the 2026 correction (−7.8%) — to answer two questions: does the daily-timeframe filter earn its keep, and should the live system trade shorts. The result changed the live configuration at the time — a broader 432-cell search on 2026-07-29 landed on a different, now-certified profile (long-only, D1 on both sides), shown below.</p>
        </div>

        <div style="padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.24);background:#1e1e1b;margin-bottom:16px;">
          <p style="margin:0 0 15px;font-size:10.5px;font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:rgba(236,233,226,.55);">Method</p>
          <p style="margin:0;max-width:80ch;font-size:13px;line-height:1.65;color:rgba(236,233,226,.72);">PAXGUSDT research proxy (deep history for XAU), 4h primary / 1d confirmation, engine-parity replay with the production strategy plugin. Every figure is net of costs: 0.05% taker fee per side, 2 bps slippage, and perpetual funding of 0.004% per 8h charged to longs and credited to shorts. Sizing mirrors live CDC-pure: 95% of equity, no exchange stop, exits on zone flips, 50% partial take-profit at +12%.</p>
        </div>

        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-bottom:16px;">
          <div style="padding:clamp(18px,2.5vw,26px);border:1px solid rgba(236,233,226,.24);background:#1e1e1b;">
            <span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">RALLY 2024–2025 (+114%)</span>
            <div style="margin-top:12px;display:grid;gap:8px;font-size:12px;line-height:1.5;color:rgba(236,233,226,.72);">
              <div><strong style="color:#ece9e2;">Long only · D1 on</strong> — 57 trades · WR 56% · PF 2.82 · <strong style="color:#ece9e2;">+64.9%</strong> · DD 5.6%</div>
              <div><strong style="color:#ece9e2;">Long+short · D1 off</strong> — 126 trades · WR 44% · PF 1.70 · +59.5% · DD 8.4%</div>
              <div style="color:rgba(236,233,226,.5);">In a one-way rally, shorts only pay whipsaw. Long-only wins this regime outright.</div>
            </div>
          </div>
          <div style="padding:clamp(18px,2.5vw,26px);border:1px solid rgba(236,233,226,.24);background:#1e1e1b;">
            <span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">CORRECTION 2026 (−7.8%)</span>
            <div style="margin-top:12px;display:grid;gap:8px;font-size:12px;line-height:1.5;color:rgba(236,233,226,.72);">
              <div><strong style="color:#ece9e2;">Long only · D1 on</strong> — 11 trades · WR 55% · PF 1.84 · +8.2% · DD 5.3%</div>
              <div><strong style="color:#ece9e2;">Long+short · D1 off</strong> — 40 trades (22 short) · WR 48% · PF 1.90 · <strong style="color:#ece9e2;">+28.4%</strong> · DD 7.6%</div>
              <div style="color:rgba(236,233,226,.5);">Shorts earn 3.5× the long-only result in the down leg. The D1 filter drags here and does not reduce drawdown.</div>
            </div>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-bottom:16px;">
          <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">FULL CYCLE · LONG+SHORT</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">+105%</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">vs +78% long-only · max DD 8.4% at 1×</span></div>
          <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">WALK-FORWARD</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">18 / 25</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">Profitable out-of-sample months · 6m optimize → 1m test, slid monthly</span></div>
          <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">PARAMETER STABILITY</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">24 / 25</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">Windows choosing fresh-zone 3 · slope filter kept in 22/25</span></div>
        </div>

        <div style="padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.24);background:#1e1e1b;margin-bottom:16px;">
          <p style="margin:0 0 15px;font-size:10.5px;font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:rgba(236,233,226,.55);">This study's configuration · 2026-07-18 · superseded 2026-07-29</p>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;">
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">Long + short</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Stop-and-reverse · D1 filter off</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">4h · fresh zone 3</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Slope filter on (3 bars)</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">PTP 50% @ +12%</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Remainder exits on zone flip</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">OKX swap · 1×</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Isolated margin · one position</span></div>
          </div>
          <p style="margin:14px 0 0;font-size:11px;line-height:1.55;color:rgba(236,233,226,.48);">This is what shipped from this study on 2026-07-18, not the current config. A broader 432-cell search on 2026-07-29 replaced it: XAU is now long-only, D1 filter on for both sides, and the partial take-profit was removed entirely (exits run on a minimal-ROI ladder instead). Current certified figures are in the Backtest section above; config source: bot_config.yaml and coin_whitelist.json.</p>
        </div>

        <div style="padding:18px 20px;border:1px solid rgba(255,122,77,.62);background:rgba(255,122,77,.08);"><strong style="font-size:13px;">Honest caveats</strong><p style="margin:7px 0 0;max-width:80ch;font-size:12px;line-height:1.6;color:rgba(236,233,226,.74);">PAXGUSDT is a proxy, not the traded XAUUSDT perpetual. Funding uses a flat 0.004%/8h approximation, not exchange funding history. Drawdown is measured on closed-trade equity; intra-trade excursions run deeper. Past performance is research evidence, not a promise of returns.</p></div>
      </div>`;
    main.appendChild(section);
    applyCutCornerSystem();
  };

  // BTC (supertrend_ema200) went live as its own certified pair alongside
  // XAU — the compiled bundle predates this and only mentions BTC as a
  // hypothetical portfolio-diversification sleeve (the frontier dial above).
  // Mirrors addResearchReport()'s section-injection pattern.
  const addBtcSection = () => {
    if (document.getElementById("btc-pair")) return;
    const main = document.getElementById("top");
    if (!main) return;

    const section = document.createElement("section");
    section.id = "btc-pair";
    section.setAttribute("aria-labelledby", "btc-pair-heading");
    section.style.cssText =
      "margin:0 auto;padding:0 0 clamp(64px,9vw,112px);scroll-margin-top:76px;";
    section.innerHTML = `
      <div style="border-top:1px solid rgba(236,233,226,.25);padding-top:clamp(40px,7vw,80px);">
        <p style="margin:0 0 14px;font-size:10.5px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#ff7a4d;">Second live pair · certified 2026-07-27</p>
        <div style="max-width:760px;margin-bottom:28px;">
          <h2 id="btc-pair-heading" style="margin:0;font-size:clamp(30px,4.5vw,56px);font-weight:600;line-height:1;letter-spacing:-.03em;">Gold isn't the only market anymore<span style="color:#ff431a;">.</span></h2>
          <p style="margin:18px 0 0;max-width:66ch;font-size:15px;line-height:1.65;color:rgba(236,233,226,.7);">A second strategy, SuperTrend EMA200, trades BTC-USDT-SWAP on OKX as its own pair — long and short, its own risk budget, its own certificate. It doesn't touch XAU's capital or rules; each pair runs in full isolation, per the architecture above.</p>
        </div>

        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-bottom:16px;">
          <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">NET · 6.6 YEARS</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">+19.35%</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">vs +4.17% long-only baseline · edge +15.18pp</span></div>
          <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">PROFIT FACTOR</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">1.52</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">Win rate 38.1% · 134 trades</span></div>
          <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">MAX DRAWDOWN</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">9.8%</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">OKX BTC-USDT-SWAP 4h · Dec 2019 – Jul 2026</span></div>
        </div>

        <div style="padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.24);background:#1e1e1b;margin-bottom:16px;">
          <p style="margin:0 0 15px;font-size:10.5px;font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:rgba(236,233,226,.55);">Live configuration</p>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;">
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">Long + short</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">SuperTrend flip entries</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">4h · EMA200 filter</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">No confirmation timeframe</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">Real ATR stop</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">3.0× ATR · trailing 2.0× · breakeven on</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">OKX swap · 1×</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Isolated margin</span></div>
          </div>
          <p style="margin:14px 0 0;font-size:11px;line-height:1.55;color:rgba(236,233,226,.48);">Config source: bot_config.yaml and coin_whitelist.json. Unlike XAU, BTC keeps a real exchange-side stop and takes no partial take-profit — protection lives on the exchange, not in a rule.</p>
        </div>

        <div style="padding:18px 20px;border:1px solid rgba(255,122,77,.62);background:rgba(255,122,77,.08);"><strong style="font-size:13px;">Honest caveats</strong><p style="margin:7px 0 0;max-width:80ch;font-size:12px;line-height:1.6;color:rgba(236,233,226,.74);">This edge is thinner than XAU's: the certificate's own bootstrap puts the 90% confidence lower bound close to zero, and the realised equity path did worse than most random reshuffles of its own trades. It cleared its pre-registered acceptance gate, but treat it as a live-deployed strategy with a genuinely uncertain edge — not a strong one. Past performance is research evidence, not a promise of returns.</p></div>
      </div>`;
    main.appendChild(section);
    applyCutCornerSystem();
  };

  const updateLinks = () => {
    document.querySelectorAll(`a[href^="${sourceUrl}"]`).forEach((link) => {
      const label = link.textContent?.trim() ?? "";
      if (label.includes("Inspect")) {
        link.href = "#research";
        link.textContent = "Read the research";
      } else if (label.includes("View source")) {
        link.href = "#system";
        link.textContent = "Explore the system →";
      } else if (label.includes("dashboard")) {
        link.href = "#dashboard";
        link.textContent = "Explore the dashboard";
      } else {
        link.href = "#start";
        link.textContent = "Get started";
      }
    });
  };

  let disconnectTimer = 0;

  const apply = () => {
    // The login/nav rules must exist even before the hero section streams in,
    // otherwise a slow unpack leaves the header without the mobile fallback.
    installHeroScrollFixStyles();
    installPolishStyles();
    applyCutCornerSystem();
    installRouterWatch();
    applyHeroScrollFix();
    updateCurrentConfiguration();
    updateBacktestAndRiskFigures();
    addResearchReport();
    addBtcSection();
    updateLinks();
    document.querySelectorAll("header nav, footer nav").forEach((nav) => {
      addResearchLink(nav);
      addBtcLink(nav);
      addLoginLink(nav);
      sortNavBySectionOrder(nav);
    });
    document.querySelectorAll("footer nav").forEach(addDonateLink);
    // Only start the shutdown countdown once the header actually carries the
    // Sign in link — the bundled page can take longer than any fixed delay to
    // unpack on a slow mobile connection.
    if (!disconnectTimer && document.querySelector("header nav [data-xauby-login]")) {
      disconnectTimer = window.setTimeout(() => observer.disconnect(), 15000);
    }
  };

  const observer = new MutationObserver(apply);
  observer.observe(document, { childList: true, subtree: true });
  apply();
})();
