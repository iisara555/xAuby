(() => {
  const sourceUrl = "https://github.com/iisara555/xAuby";

  const rounded = (element, radius = "14px") => {
    const inlineStyle = element.getAttribute("style") || "";
    if (inlineStyle.includes("border-radius: 50%") || element.style.borderRadius === "50%") return;
    element.style.borderRadius = radius;
  };

  const applyRoundedSystem = () => {
    document
      .querySelectorAll("button, input, select, textarea, [style*='background'], [style*='border:']")
      .forEach((element) => rounded(element));
  };

  const replaceExactText = (selector, from, to) => {
    document.querySelectorAll(selector).forEach((element) => {
      if (element.textContent?.trim() === from) element.textContent = to;
    });
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
        min-height: 100svh !important;
        --hero-blur: 0px !important;
        --hero-copy-blur: 0px !important;
        --hero-copy-opacity: 1 !important;
        --hero-copy-y: 0px !important;
      }
      .xn-hero-sticky { z-index: 1; }
      .xauby-hero-gradient {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        background:
          radial-gradient(circle at 76% 18%, rgba(255,67,26,.22), transparent 31%),
          radial-gradient(circle at 22% 78%, rgba(111,32,75,.16), transparent 38%),
          linear-gradient(145deg, #16050d 0%, #060006 52%, #0c0508 100%);
      }
      .xn-hero-content {
        opacity: 1 !important;
        filter: none !important;
        transform: none !important;
      }
      /* Keep the mobile hero copy in the first viewport instead of pinning it
         to the bottom and leaving an oversized empty band above the headline. */
      @media (max-width: 760px) {
        .xn-hero-sticky { align-items: flex-start !important; }
        .xn-hero-inner {
          align-self: stretch !important;
          padding-top: clamp(116px, 17vh, 168px) !important;
          padding-bottom: 36px !important;
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
      .xn-hero-sticky.xauby-hero-pinned {
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        right: 0 !important;
        width: 100vw !important;
      }
      .xn-hero-sticky.xauby-hero-after {
        position: absolute !important;
        top: auto !important;
        bottom: 0 !important;
        left: 0 !important;
        width: 100% !important;
      }
    `;
    (document.head || document.documentElement).append(style);
  };

  let heroPinCleanup = null;
  let heroPinTarget = null;

  const ensureHeroGradient = (heroSection) => {
    heroSection.querySelectorAll("video").forEach((video) => video.remove());
    if (heroSection.querySelector(".xauby-hero-gradient")) return;
    const heroSticky = heroSection.querySelector(".xn-hero-sticky");
    if (!heroSticky) return;
    const gradient = document.createElement("div");
    gradient.className = "xauby-hero-gradient";
    gradient.setAttribute("aria-hidden", "true");
    heroSticky.prepend(gradient);
  };

  const setupHeroPinFallback = (heroSection, heroSticky) => {
    if (heroPinTarget === heroSticky) return;
    if (heroPinCleanup) heroPinCleanup();

    heroPinTarget = heroSticky;
    let raf = 0;

    const update = () => {
      raf = 0;
      if (!document.contains(heroSection) || !document.contains(heroSticky)) return;

      const rect = heroSection.getBoundingClientRect();
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 1;
      const stickyHeight = Math.min(heroSticky.offsetHeight || viewportHeight, viewportHeight);
      const shouldPin = rect.top <= 0 && rect.bottom > stickyHeight;
      const shouldDock = rect.top <= 0 && rect.bottom <= stickyHeight;

      heroSticky.classList.toggle("xauby-hero-pinned", shouldPin);
      heroSticky.classList.toggle("xauby-hero-after", shouldDock);
    };

    const request = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };

    window.addEventListener("scroll", request, { passive: true });
    window.addEventListener("resize", request);
    request();

    heroPinCleanup = () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("scroll", request);
      window.removeEventListener("resize", request);
      heroSticky.classList.remove("xauby-hero-pinned", "xauby-hero-after");
      heroPinTarget = null;
      heroPinCleanup = null;
    };
  };

  const applyHeroScrollFix = () => {
    const heroSection = document.querySelector("[data-hero-scrub]");
    if (!heroSection) return;

    installHeroScrollFixStyles();
    heroSection.style.setProperty("--hero-blur", "0px", "important");
    heroSection.style.setProperty("--hero-copy-blur", "0px", "important");
    heroSection.style.setProperty("--hero-copy-opacity", "1", "important");
    heroSection.style.setProperty("--hero-copy-y", "0px", "important");
    heroSection.style.setProperty("min-height", "100svh", "important");
    ensureHeroGradient(heroSection);

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

    const heroSticky = heroSection.querySelector(".xn-hero-sticky");
    if (heroSticky) setupHeroPinFallback(heroSection, heroSticky);
  };

  const addResearchLink = (nav) => {
    if (!nav || nav.querySelector("a[href='#research-report']")) return;
    const link = document.createElement("a");
    link.href = "#research-report";
    link.textContent = "Research report";
    link.style.cssText =
      "display:inline-flex;align-items:center;min-height:44px;padding:0 4px;font-size:12.5px;font-weight:600;letter-spacing:.02em;color:#ff7a4d;";
    rounded(link, "10px");
    nav.prepend(link);
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

  const updateCurrentConfiguration = () => {
    replaceExactText("span", "LIVE: cdc_action_zone", "LIVE: xauby_actionzone");
    replaceExactText("span", "RUNNING: 1 pair", "LIVE: XAU · long + short");
    replaceExactText("span", "LIVE: XAU · long only", "LIVE: XAU · long + short");
    replaceExactText("span", "RUNNING: OKX only", "OKX swap · isolated · 1×");
    replaceExactText("h3", "Strategy plugins", "Current live strategy");
    replaceExactText("p", "Current live configuration", "Operating controls");

    document.querySelectorAll("p").forEach((paragraph) => {
      const text = paragraph.textContent?.trim();
      if (text === "Orders go to the exchange with a stop-loss attached before entry — protection lives on the exchange, not in the bot's memory.") {
        paragraph.textContent =
          "Orders are evaluated under the configured risk gates. The current XAU profile trades long and short as a stop-and-reverse; its regime router is disabled and protection settings must be reviewed before any live change.";
      }
      if (text === "The owner monitors from a terminal, a phone dashboard, and Telegram alerts. He watches; the system trades.") {
        paragraph.textContent =
          "The operator monitors from the web dashboard, a terminal, and Telegram alerts — every control passes through the engine's own safeguards.";
      }
    });
  };

  const addResearchReport = () => {
    if (document.getElementById("research-report")) return;
    const footer = document.querySelector("footer");
    if (!footer) return;

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
          <p style="margin:18px 0 0;max-width:66ch;font-size:15px;line-height:1.65;color:rgba(236,233,226,.7);">CDC ActionZone was re-validated across a full market cycle — the 2024–2025 gold rally (+114%) and the 2026 correction (−7.8%) — to answer two questions: does the daily-timeframe filter earn its keep, and should the live system trade shorts. The result changed the live configuration.</p>
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
          <p style="margin:0 0 15px;font-size:10.5px;font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:rgba(236,233,226,.55);">Live configuration · updated 2026-07-18</p>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;">
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">Long + short</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Stop-and-reverse · D1 filter off</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">4h · fresh zone 3</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Slope filter on (3 bars)</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">PTP 50% @ +12%</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Remainder exits on zone flip</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">OKX swap · 1×</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Isolated margin · one position</span></div>
          </div>
          <p style="margin:14px 0 0;font-size:11px;line-height:1.55;color:rgba(236,233,226,.48);">Config source: bot_config.yaml and coin_whitelist.json. Changed by this study: short execution enabled, D1 regime filter disabled. Unchanged: slope filter, partial take-profit, fresh-zone window, 1× leverage.</p>
        </div>

        <div style="padding:18px 20px;border:1px solid rgba(255,122,77,.62);background:rgba(255,122,77,.08);"><strong style="font-size:13px;">Honest caveats</strong><p style="margin:7px 0 0;max-width:80ch;font-size:12px;line-height:1.6;color:rgba(236,233,226,.74);">PAXGUSDT is a proxy, not the traded XAUUSDT perpetual. Funding uses a flat 0.004%/8h approximation, not exchange funding history. Drawdown is measured on closed-trade equity; intra-trade excursions run deeper. Past performance is research evidence, not a promise of returns.</p></div>
      </div>`;
    footer.before(section);
    applyRoundedSystem();
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
    applyRoundedSystem();
    applyHeroScrollFix();
    updateCurrentConfiguration();
    addResearchReport();
    updateLinks();
    document.querySelectorAll("header nav, footer nav").forEach((nav) => {
      addResearchLink(nav);
      addLoginLink(nav);
    });
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
