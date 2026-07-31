/**
 * Brand tokens and shared motion policy for the WebGL layers.
 *
 * The colours mirror Website/app/globals.css. They are kept as plain 0..1 sRGB
 * triples because the raw ShaderMaterials write straight to the framebuffer
 * without three's colour management — passing them through THREE.Color would
 * convert to Linear-sRGB and darken them.
 */

export const BG_HEX = 0x060006;
export const PAPER_RGB = [0xec / 255, 0xe9 / 255, 0xe2 / 255];
export const ACCENT_RGB = [0xff / 255, 0x43 / 255, 0x1a / 255];

/**
 * Reduced motion means less motion, not none: the scenes keep running and keep
 * answering the cursor, on a slower clock and at a lower amplitude. Freezing
 * them outright read as a broken page.
 */
export const MOTION_QUERY = "(prefers-reduced-motion: reduce)";
export const CALM_AMPLITUDE = 0.55;
export const CALM_TIME_SCALE = 0.4;
