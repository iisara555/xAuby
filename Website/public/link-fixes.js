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

  const updateCurrentConfiguration = () => {
    replaceExactText("span", "LIVE: cdc_action_zone", "LIVE: xauby_actionzone");
    replaceExactText("span", "RUNNING: 1 pair", "LIVE: XAU · long only");
    replaceExactText("span", "RUNNING: OKX only", "OKX swap · isolated · 1×");
    replaceExactText("h3", "Strategy plugins", "Current live strategy");

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
    updateCurrentConfiguration();
    addRoadmap();
    updateLinks();
    document.querySelectorAll("header nav, footer nav").forEach(addRoadmapLink);
  };

  const observer = new MutationObserver(apply);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  apply();
  window.setTimeout(() => observer.disconnect(), 15000);
})();
