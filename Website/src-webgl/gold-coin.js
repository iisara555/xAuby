/**
 * A single gold coin, overlaid above the whole page.
 *
 * The model is the operator-supplied Tripo3D GLB, optimised for the web with
 * gltf-transform (977k → 49k triangles, 28.7 MB → 449 KB, meshopt + WebP).
 * See docs in scripts/build-webgl.mjs for why everything here is bundled and
 * self-hosted: the page runs under `default-src 'self'`.
 *
 * The canvas sits above the content on purpose (`z-index` above the header)
 * with `pointer-events: none`, so the coin can pass in front of the copy
 * without ever intercepting a click.
 */

import {
  ACESFilmicToneMapping,
  BackSide,
  Box3,
  BoxGeometry,
  Color,
  DirectionalLight,
  Group,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  PMREMGenerator,
  Scene,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";

import { CALM_AMPLITUDE, CALM_TIME_SCALE, MOTION_QUERY } from "./theme.js";

const MODEL_URL = "/coin.glb";
const CAMERA_Z = 30;
/**
 * World-space size the model is normalised to, whatever the file's own scale.
 * Kept modest: this layer paints over the copy, so a large coin blanks out
 * whole lines of text as it passes.
 */
const COIN_SIZE = 4.8;

/**
 * Fall path — one single drop across the whole page, not a repeating cycle.
 * Because there is no wrap, the ends do not need to clear the frustum; they sit
 * just inside it instead, so the coin is on screen from the hero to the footer.
 * The X offsets place it right of the copy column.
 */
// The top of the path clears the sticky header: this layer paints above it, and
// at 11 the coin sat across the nav links on first load.
const FALL_TOP = 8.5;
const FALL_BOTTOM = -13;
const BASE_X = 8.5;
const BASE_X_PORTRAIT = 2.6;

/**
 * A dark room with warm emissive panels, baked to a PMREM cube. The model ships
 * as a rough, non-metallic PBR material with a baked colour texture, so this is
 * what lights it — without an environment it renders nearly flat.
 */
const buildEnvironment = (renderer) => {
  const scene = new Scene();
  // Not black: the coin spins continuously, and a room this dark left it a
  // silhouette for whole seconds at a time whenever a face turned away from a
  // panel. This is the floor of ambient reflection.
  const room = new Mesh(
    new BoxGeometry(60, 60, 60),
    new MeshBasicMaterial({ color: 0x3a2c33, side: BackSide })
  );
  scene.add(room);

  const panels = [];
  const panel = (color, intensity, x, y, z, w, h) => {
    const light = new Mesh(
      new BoxGeometry(w, h, 0.2),
      new MeshBasicMaterial({ color: new Color(color).multiplyScalar(intensity) })
    );
    light.position.set(x, y, z);
    light.lookAt(0, 0, 0);
    scene.add(light);
    panels.push(light);
  };

  // Intensities stay near 1: these reflect straight into frame, and anything
  // much brighter pushes the coin past the bloom threshold and blows out.
  // Wrapped on all six sides: whichever way the coin turns, some panel is in
  // its reflection.
  panel(0xfff4e0, 2.6, 0, 18, 6, 34, 16); // key, warm white from above
  panel(0xffffff, 1.8, 2, 2, 26, 44, 34); // front fill — what the face reflects
  panel(0xff7a3c, 1.7, -18, -4, 12, 18, 26); // brand-orange bounce
  panel(0xffd9a0, 1.3, 18, 4, -14, 20, 26); // rim, keeps the far edge readable
  panel(0xffe8c4, 1.2, 0, 2, -26, 40, 30); // back, lights the far face
  panel(0xffc98a, 0.9, 0, -16, -2, 34, 18); // floor bounce

  const pmrem = new PMREMGenerator(renderer);
  const target = pmrem.fromScene(scene, 0.04);
  pmrem.dispose();

  room.geometry.dispose();
  room.material.dispose();
  panels.forEach((p) => {
    p.geometry.dispose();
    p.material.dispose();
  });
  return target.texture;
};

/** Centre the model on its own bounding box and normalise it to COIN_SIZE. */
const fitModel = (object) => {
  const box = new Box3().setFromObject(object);
  const size = box.getSize(new Vector3());
  const centre = box.getCenter(new Vector3());
  const largest = Math.max(size.x, size.y, size.z) || 1;

  object.position.sub(centre);
  const holder = new Group();
  holder.add(object);
  holder.scale.setScalar(COIN_SIZE / largest);
  return holder;
};

export const createGoldCoin = (root) => {
  const canvas = document.createElement("canvas");
  canvas.className = "xn-gold-coin";
  canvas.setAttribute("aria-hidden", "true");

  let renderer;
  try {
    renderer = new WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      powerPreference: "high-performance",
    });
  } catch {
    return null; // No WebGL — the page is simply unchanged.
  }

  const compact = window.innerWidth < 760;
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;

  const scene = new Scene();
  const camera = new PerspectiveCamera(45, 1, 0.1, 140);
  camera.position.z = CAMERA_Z;

  const envMap = buildEnvironment(renderer);
  scene.environment = envMap;

  // Two lights, not one: at metalness 0.85 the diffuse term still carries some
  // of the surface, and it is what keeps the coin readable in the moments the
  // spin turns a face away from every panel.
  const key = new DirectionalLight(0xfff0d4, 2.2);
  key.position.set(-6, 10, 14);
  scene.add(key);

  const fill = new DirectionalLight(0xffb877, 1.1);
  fill.position.set(9, -4, 8);
  scene.add(fill);

  const pivot = new Group();
  scene.add(pivot);

  let coin = null;
  let loadedRoot = null;

  const composer = new EffectComposer(renderer);
  const renderPass = new RenderPass(scene, camera);
  renderPass.clearAlpha = 0;
  composer.addPass(renderPass);
  // High threshold, low strength: bloom belongs on the highlight that sweeps
  // the rim as the coin turns, not on the whole face.
  const bloom = new UnrealBloomPass(new Vector2(1, 1), compact ? 0.2 : 0.3, 0.55, 0.9);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  const motionQuery = window.matchMedia ? window.matchMedia(MOTION_QUERY) : null;
  let amplitude = 1;
  let timeScale = 1;
  const applyMotionPreference = () => {
    const calm = !!(motionQuery && motionQuery.matches);
    amplitude = calm ? CALM_AMPLITUDE : 1;
    timeScale = calm ? CALM_TIME_SCALE : 1;
  };
  applyMotionPreference();
  if (motionQuery) motionQuery.addEventListener("change", applyMotionPreference);

  let scrollTarget = 0;
  let scroll = 0;
  const pointer = { x: 0, y: 0, targetX: 0, targetY: 0 };
  let elapsed = 0;
  let lastTime = 0;
  let frame = 0;
  let disposed = false;

  /**
   * Scroll progress across the whole document, 0 at the hero and 1 at the
   * footer — which is exactly the progress of the single fall. Recomputed on
   * every scroll so it stays correct as the page grows (React fills #dc-root
   * after mount, then link-fixes.js appends two more sections).
   */
  const readScroll = () => {
    const span = document.documentElement.scrollHeight - (window.innerHeight || 1);
    scrollTarget = span > 0 ? window.scrollY / span : 0;
  };

  const resize = () => {
    const width = window.innerWidth || 1;
    const height = window.innerHeight || 1;
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
    renderer.setPixelRatio(ratio);
    renderer.setSize(width, height, false);
    composer.setPixelRatio(ratio);
    composer.setSize(width, height);
    camera.aspect = width / height;
    camera.fov = camera.aspect < 1 ? 58 : 45;
    camera.updateProjectionMatrix();
    readScroll();
  };

  const onPointerMove = (event) => {
    pointer.targetX = (event.clientX / (window.innerWidth || 1)) * 2 - 1;
    pointer.targetY = -((event.clientY / (window.innerHeight || 1)) * 2 - 1);
  };
  const onPointerLeave = () => {
    pointer.targetX = 0;
    pointer.targetY = 0;
  };

  const render = () => {
    const now = performance.now();
    elapsed += (lastTime ? Math.min((now - lastTime) / 1000, 0.05) : 0) * timeScale;
    lastTime = now;

    // Damped scroll: the coin arrives at a section slightly after the copy
    // does, which is what makes the layer feel physical rather than pinned.
    scroll += (scrollTarget - scroll) * 0.07;
    pointer.x += (pointer.targetX - pointer.x) * 0.045;
    pointer.y += (pointer.targetY - pointer.y) * 0.045;

    if (coin) {
      const portrait = camera.aspect < 1;
      // A single fall spanning the document: scroll position IS the progress of
      // the drop, so there is no cycle to wrap and no jump back to the top.
      const fall = Math.min(Math.max(scroll, 0), 1);
      const drop = Math.pow(fall, 1.15); // slight acceleration, as under gravity

      // The travel is NOT scaled by `amplitude`; only the flutter and tilt are.
      // Shortening the path would strand the coin mid-air at the end of the page.
      pivot.position.y = FALL_TOP - drop * (FALL_TOP - FALL_BOTTOM);

      // Sits right of centre, drifting across the fall — a thin disc never
      // drops on a plumb line.
      pivot.position.x =
        (portrait ? BASE_X_PORTRAIT : BASE_X) +
        Math.sin(fall * Math.PI * 3) * (portrait ? 1.2 : 2.1) * amplitude;
      // Biased away from the camera so a near pass never fills the viewport.
      pivot.position.z = -5 + Math.sin(fall * Math.PI * 1.5) * 4;

      // Tumbling end over end on the way down; the cursor tilts on top of that.
      pivot.rotation.x = fall * Math.PI * 7 - pointer.y * 0.4 * amplitude;
      pivot.rotation.y = fall * 1.6 + elapsed * 0.12 + pointer.x * 0.55 * amplitude;
      pivot.rotation.z = Math.cos(elapsed * 0.26) * 0.14 * amplitude;
    }

    composer.render();
  };

  const loop = () => {
    frame = requestAnimationFrame(loop);
    render();
  };

  const stop = () => {
    if (!frame) return;
    cancelAnimationFrame(frame);
    frame = 0;
  };

  const start = () => {
    if (frame || disposed || document.hidden) return;
    lastTime = 0;
    frame = requestAnimationFrame(loop);
  };

  const onVisibilityChange = () => (document.hidden ? stop() : start());
  const onScroll = () => readScroll();
  const onContextLost = (event) => {
    event.preventDefault();
    stop();
  };
  const onContextRestored = () => {
    resize();
    start();
  };

  // The page grows after mount (React fills #dc-root, then link-fixes.js
  // appends two whole sections), so re-read rather than trusting the first
  // measurement.
  const resizeObserver = new ResizeObserver(readScroll);
  resizeObserver.observe(root);

  window.addEventListener("resize", resize);
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("pointermove", onPointerMove, { passive: true });
  window.addEventListener("pointerleave", onPointerLeave, { passive: true });
  window.addEventListener("blur", onPointerLeave);
  document.addEventListener("visibilitychange", onVisibilityChange);
  canvas.addEventListener("webglcontextlost", onContextLost);
  canvas.addEventListener("webglcontextrestored", onContextRestored);

  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  loader.load(
    MODEL_URL,
    (gltf) => {
      if (disposed) return;
      loadedRoot = gltf.scene;

      // The GLB ships as a rough dielectric (metallic 0, roughness 0.9) with a
      // baked colour atlas, which renders as flat plastic. The relief is in the
      // geometry, and only a specular surface reveals it — so the material is
      // re-authored as metal here and the atlas is tinted to gold.
      loadedRoot.traverse((child) => {
        if (!child.isMesh) return;
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        materials.forEach((material) => {
          material.metalness = 0.85;
          material.roughness = 0.3;
          material.envMapIntensity = 1.7;
          if (material.color) material.color.setHex(0xffcf7a);
          material.needsUpdate = true;
        });
      });

      coin = fitModel(loadedRoot);
      pivot.add(coin);
    },
    undefined,
    (error) => console.warn("Coin model failed to load:", error)
  );

  const dispose = () => {
    disposed = true;
    stop();
    resizeObserver.disconnect();
    window.removeEventListener("resize", resize);
    window.removeEventListener("scroll", onScroll);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerleave", onPointerLeave);
    window.removeEventListener("blur", onPointerLeave);
    document.removeEventListener("visibilitychange", onVisibilityChange);
    canvas.removeEventListener("webglcontextlost", onContextLost);
    canvas.removeEventListener("webglcontextrestored", onContextRestored);
    if (motionQuery) motionQuery.removeEventListener("change", applyMotionPreference);
    if (loadedRoot) {
      loadedRoot.traverse((child) => {
        if (!child.isMesh) return;
        child.geometry.dispose();
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        materials.forEach((m) => {
          Object.values(m).forEach((value) => {
            if (value && value.isTexture) value.dispose();
          });
          m.dispose();
        });
      });
    }
    envMap.dispose();
    composer.dispose();
    renderer.dispose();
    canvas.remove();
  };

  document.body.appendChild(canvas);
  resize();
  start();

  return { host: root, canvas, dispose };
};
