/**
 * WebGL layers for /research-platform.html — entry point.
 *
 * Two scenes ship together so they share one copy of three:
 *   - the hero particle wave, mounted inside `.xn-hero-sticky`
 *   - the gold coin, one fixed layer overlaid above the entire page
 *
 * research-platform.html is a self-unpacking bundle whose loader replaces
 * `document.documentElement` on DOMContentLoaded, destroying anything mounted
 * before that. Like link-fixes.js, we hold a live MutationObserver and mount
 * once the real DOM exists. three is bundled in, so mounting is synchronous
 * from that point — there is no import to lose the DOM across.
 */

import { createParticleWave } from "./particle-wave.js";
import { createGoldCoin } from "./gold-coin.js";

const HERO_SELECTOR = "[data-hero-scrub] .xn-hero-sticky";
const ROOT_SELECTOR = "#dc-root";
const STYLE_ID = "xn-webgl-layers";

/**
 * The coin canvas is an overlay: it paints above every element on the page,
 * including the sticky header, so the coin can pass in front of the copy.
 * `pointer-events: none` is what makes that safe — without it the layer would
 * swallow every click on the site.
 */
const installStyles = () => {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .xn-gold-coin {
      position: fixed;
      inset: 0;
      display: block;
      width: 100%;
      height: 100%;
      pointer-events: none;
      z-index: 2147483000;
    }
  `;
  document.head.appendChild(style);
};

const scenes = [
  { find: () => document.querySelector(HERO_SELECTOR), create: createParticleWave },
  { find: () => document.querySelector(ROOT_SELECTOR), create: createGoldCoin },
];

let settleTimer = 0;

const tryMount = () => {
  let allMounted = true;

  for (const scene of scenes) {
    if (scene.unsupported) continue;
    if (scene.instance) {
      if (scene.instance.host.isConnected && scene.instance.canvas.isConnected) continue;
      scene.instance.dispose();
      scene.instance = null;
    }

    const host = scene.find();
    if (!host) {
      allMounted = false;
      continue;
    }

    installStyles();
    try {
      scene.instance = scene.create(host);
    } catch (error) {
      scene.unsupported = true;
      console.warn("WebGL layer unavailable:", error);
      continue;
    }
    // A null return is the documented "no WebGL context" path; retrying it on
    // every mutation would just burn frames.
    if (!scene.instance) scene.unsupported = true;
  }

  if (allMounted && !settleTimer) {
    settleTimer = window.setTimeout(() => observer.disconnect(), 20000);
  }
};

const observer = new MutationObserver(tryMount);
observer.observe(document, { childList: true, subtree: true });
tryMount();
