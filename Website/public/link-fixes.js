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
      @media (max-width: 720px) {
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

  const addRoadmapLink = (nav) => {
    if (!nav || nav.querySelector("a[href='#roadmap']")) return;
    const link = document.createElement("a");
    link.href = "#roadmap";
    link.textContent = "Roadmap";
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
    replaceExactText("span", "RUNNING: 1 pair", "LIVE: XAU · long only");
    replaceExactText("span", "RUNNING: OKX only", "OKX swap · isolated · 1×");
    replaceExactText("h3", "Strategy plugins", "Current live strategy");
    replaceExactText("p", "Current live configuration", "Operating controls");

    document.querySelectorAll("p").forEach((paragraph) => {
      const text = paragraph.textContent?.trim();
      if (text === "Orders go to the exchange with a stop-loss attached before entry — protection lives on the exchange, not in the bot's memory.") {
        paragraph.textContent =
          "Orders are evaluated under the configured risk gates. The current XAU profile is long-only; its regime router is disabled and protection settings must be reviewed before any live change.";
      }
      if (text === "The owner monitors from a terminal, a phone dashboard, and Telegram alerts. He watches; the system trades.") {
        paragraph.textContent =
          "Today the operator uses the dashboard, terminal, and Telegram alerts. Phase 1 of the roadmap moves normal operations to the webapp without bypassing engine safeguards.";
      }
    });
  };

  const addResearchUpdate = (section) => {
    if (!section || section.querySelector("[data-report-update]")) return;
    const milestones = section.querySelector("ol");
    if (!milestones) return;

    const update = document.createElement("div");
    update.dataset.reportUpdate = "2026-07-14";
    update.style.cssText = "margin:0 0 16px;";
    update.innerHTML = `
      <div style="padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.24);background:#1e1e1b;margin-bottom:16px;">
        <p style="margin:0 0 15px;font-size:10.5px;font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:rgba(236,233,226,.55);">Latest research-backed configuration</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;">
          <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">XAU / XAUUSDT</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">PAXGUSDT research proxy · 4h / 1d</span></div>
          <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">Long only</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">D1 regime filter · fresh zone window 3</span></div>
          <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">Strict ROI</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">8% now · 5% after 1d · 3% after 3d</span></div>
          <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">BTC: SIM ONLY</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">At least 8 trades · PF > 1.3 · MDD <= 12%</span></div>
        </div>
        <p style="margin:14px 0 0;font-size:11px;line-height:1.55;color:rgba(236,233,226,.48);">Short execution, ATR stop, trailing stop, and breakeven are disabled. The XAU profile retains the slope filter and takes 50% partial profit at +12%. This research information is not a return promise.</p>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-bottom:16px;">
        <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">BACKTEST</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">PF 2.00</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">46% win rate · 8% max drawdown</span></div>
        <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">WALK-FORWARD</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">4 / 5</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">Profitable folds in the reported result</span></div>
        <div style="padding:18px;border:1px solid rgba(236,233,226,.24);background:#1e1e1b;"><span style="display:block;font-size:10px;font-weight:600;letter-spacing:.12em;color:#ff7a4d;">PLANNING RANGE</span><strong style="display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em;">PF 1.3-1.7</strong><span style="display:block;margin-top:4px;font-size:11px;color:rgba(236,233,226,.58);">Use this range, not PF 2.00, for planning</span></div>
      </div>
      <div style="padding:18px 20px;border:1px solid rgba(255,122,77,.62);background:rgba(255,122,77,.08);margin-bottom:16px;"><strong style="font-size:13px;">Validation note</strong><p style="margin:7px 0 0;max-width:80ch;font-size:12px;line-height:1.6;color:rgba(236,233,226,.74);">The report flags a walk-forward discrepancy around folds 1-2. Confirm raw fold results and replay validation before considering the ROI profile deploy-ready. The earlier Efficient Frontier allocation used an older XAU configuration and must be recalculated before capital is assigned.</p></div>`;
    milestones.before(update);
    applyRoundedSystem();
  };

  const addRoadmap = () => {
    if (document.getElementById("roadmap")) return;
    const footer = document.querySelector("footer");
    if (!footer) return;

    const section = document.createElement("section");
    section.id = "roadmap";
    section.setAttribute("aria-labelledby", "roadmap-heading");
    section.style.cssText =
      "margin:0 auto;padding:0 0 clamp(64px,9vw,112px);scroll-margin-top:76px;";
    section.innerHTML = `
      <div style="border-top:1px solid rgba(236,233,226,.25);padding-top:clamp(40px,7vw,80px);">
        <p style="margin:0 0 14px;font-size:10.5px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#ff7a4d;">Product roadmap · 2026</p>
        <div style="display:flex;flex-wrap:wrap;align-items:end;justify-content:space-between;gap:18px;margin-bottom:28px;">
          <div style="max-width:720px;">
            <h2 id="roadmap-heading" style="margin:0;font-size:clamp(30px,4.5vw,56px);font-weight:600;line-height:1;letter-spacing:-.03em;">Webapp first.<br>SaaS when ready<span style="color:#ff431a;">.</span></h2>
            <p style="margin:18px 0 0;max-width:62ch;font-size:15px;line-height:1.65;color:rgba(236,233,226,.7);">xAuby is moving from a single-operator trading system to a web-controlled, multi-instance platform. Each phase protects the same principle: the UI never bypasses the engine’s risk and execution safeguards.</p>
          </div>
          <span style="padding:9px 13px;border:1px solid rgba(236,233,226,.3);font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#ece9e2;">Measured growth · not feature rush</span>
        </div>

        <div style="padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.24);background:#1e1e1b;margin-bottom:16px;">
          <p style="margin:0 0 15px;font-size:10.5px;font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:rgba(236,233,226,.55);">Current live configuration</p>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;">
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">XAU / XAUUSDT</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Single enabled market · PAXGUSDT research proxy</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">xauby_actionzone</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">4h primary · 1d confirmation · long only</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">OKX swap · 1×</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">CCXT adapter · isolated margin</span></div>
            <div style="padding:14px;border:1px solid rgba(236,233,226,.16);background:#161614;"><strong style="display:block;font-size:15px;">2% / 25% / 6%</strong><span style="display:block;margin-top:6px;font-size:11px;color:rgba(236,233,226,.58);">Risk per trade · allocation cap · daily loss cap</span></div>
          </div>
          <p style="margin:14px 0 0;font-size:11px;line-height:1.55;color:rgba(236,233,226,.48);">Config source: bot_config.yaml and coin_whitelist.json. Current profile: one open position maximum, 50% partial take-profit at +12%, D1 regime filter and fresh-zone window of 3. Regime router and live short execution are disabled.</p>
        </div>

        <ol style="list-style:none;margin:0;padding:0;display:grid;gap:10px;">
          <li style="display:grid;grid-template-columns:minmax(58px,92px) minmax(0,1fr);gap:18px;padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.22);background:rgba(236,233,226,.03);">
            <span style="font-size:13px;font-weight:600;letter-spacing:.12em;color:#ff6a3c;">PHASE 0</span><div><h3 style="margin:0;font-size:20px;">Harden the seams</h3><p style="margin:8px 0 0;max-width:70ch;font-size:14px;line-height:1.6;color:rgba(236,233,226,.68);">Make config writers instance-aware, freeze the state contract, consolidate File-IPC into a signed command queue, add systemd units, and measure real RSS before scaling.</p></div>
          </li>
          <li style="display:grid;grid-template-columns:minmax(58px,92px) minmax(0,1fr);gap:18px;padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.22);background:rgba(236,233,226,.03);">
            <span style="font-size:13px;font-weight:600;letter-spacing:.12em;color:#ff6a3c;">PHASE 1</span><div><h3 style="margin:0;font-size:20px;">100% webapp, one operator</h3><p style="margin:8px 0 0;max-width:70ch;font-size:14px;line-height:1.6;color:rgba(236,233,226,.68);">Add authenticated write APIs, command feedback, SSE updates, pair management, guarded sim/live controls, and a config editor — all through queue or config-mutator paths.</p></div>
          </li>
          <li style="display:grid;grid-template-columns:minmax(58px,92px) minmax(0,1fr);gap:18px;padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.03);">
            <span style="font-size:13px;font-weight:600;letter-spacing:.12em;color:#ff6a3c;">PHASE 2</span><div><h3 style="margin:0;font-size:20px;">Multiple instances, one host</h3><p style="margin:8px 0 0;max-width:70ch;font-size:14px;line-height:1.6;color:rgba(236,233,226,.68);">Use systemd as the process manager, introduce an instance registry and switcher, share public market data safely, and expose account-lock health.</p></div>
          </li>
          <li style="display:grid;grid-template-columns:minmax(58px,92px) minmax(0,1fr);gap:18px;padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.22);background:rgba(236,233,226,.03);">
            <span style="font-size:13px;font-weight:600;letter-spacing:.12em;color:#ff6a3c;">PHASE 3</span><div><h3 style="margin:0;font-size:20px;">SaaS with guarded tenancy</h3><p style="margin:8px 0 0;max-width:70ch;font-size:14px;line-height:1.6;color:rgba(236,233,226,.68);">Add encrypted exchange credentials, tenant isolation, plans, simulation-first onboarding, billing, backups, and a security review before opening paid live trading.</p></div>
          </li>
          <li style="display:grid;grid-template-columns:minmax(58px,92px) minmax(0,1fr);gap:18px;padding:clamp(20px,3vw,30px);border:1px solid rgba(236,233,226,.22);background:rgba(236,233,226,.03);">
            <span style="font-size:13px;font-weight:600;letter-spacing:.12em;color:#8a877f;">PHASE 4</span><div><h3 style="margin:0;font-size:20px;">Scale out deliberately</h3><p style="margin:8px 0 0;max-width:70ch;font-size:14px;line-height:1.6;color:rgba(236,233,226,.68);">When live tenants exceed a single host’s measured capacity, use a control-plane node, worker nodes, tenant-local SQLite data, and private node networking.</p></div>
          </li>
        </ol>
      </div>`;
    footer.before(section);
    addResearchUpdate(section);
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
      } else if (label.includes("WebUI")) {
        link.href = "#dashboard";
        link.textContent = "Explore the dashboard";
      } else {
        link.href = "#start";
        link.textContent = "Get started";
      }
    });
  };

  const apply = () => {
    applyRoundedSystem();
    applyHeroScrollFix();
    updateCurrentConfiguration();
    addRoadmap();
    updateLinks();
    document.querySelectorAll("header nav, footer nav").forEach((nav) => {
      addRoadmapLink(nav);
      addLoginLink(nav);
    });
  };

  const observer = new MutationObserver(apply);
  observer.observe(document, { childList: true, subtree: true });
  apply();
  window.setTimeout(() => observer.disconnect(), 15000);
})();
