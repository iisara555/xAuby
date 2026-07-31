/**
 * Bundles src-webgl into public/webgl-scene.js.
 *
 * /research-platform.html is served under `default-src 'self'` (next.config.ts)
 * and is a static file outside Next's build, so its WebGL layers cannot come
 * from a CDN and are not compiled by Next. They are bundled here instead:
 * three's post-processing addons import bare `three`, which no browser can
 * resolve without an import map, and bundling also keeps one copy of three
 * shared between the particle wave and the coins.
 *
 * The output is committed — Vercel serves public/ as-is and never runs this.
 * Re-run after editing anything under src-webgl/ or bumping three:
 *   npm run build:webgl
 */
import { build } from "esbuild";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

const result = await build({
  entryPoints: [join(root, "src-webgl", "index.js")],
  outfile: join(root, "public", "webgl-scene.js"),
  bundle: true,
  format: "esm",
  target: ["es2020"],
  minify: true,
  legalComments: "none",
  banner: { js: "/* three.js (MIT) © three.js authors — bundled from src-webgl. */" },
  metafile: true,
});

const [outPath, meta] = Object.entries(result.metafile.outputs)[0];
console.log(`${outPath.replace(/\\/g, "/")}  ${(meta.bytes / 1024).toFixed(1)} kB`);
