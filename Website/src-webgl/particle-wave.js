/**
 * Interactive particle wave for the hero of /research-platform.html.
 *
 * Mounted inside `.xn-hero-sticky`, after the hero's backdrop layers and before
 * `.xn-hero-inner` (z-index 2), so it paints as backdrop → particles → copy.
 * Lifecycle (waiting for the bundle's document swap) lives in index.js.
 */

import {
  AdditiveBlending,
  BufferAttribute,
  BufferGeometry,
  PerspectiveCamera,
  Points,
  Scene,
  ShaderMaterial,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";

import { CALM_AMPLITUDE, CALM_TIME_SCALE, MOTION_QUERY, PAPER_RGB, ACCENT_RGB } from "./theme.js";

const COPY_SELECTOR = ".xn-hero-inner";

// Grid + framing. The plane is centred behind the camera target so the far rows
// fade into the hero's own darkness instead of ending on a hard horizon line.
const GRID_WIDTH = 110;
const GRID_DEPTH = 52;
const GRID_CENTER_Z = -6;
const CAMERA_HOME = { x: 0, y: 8.5, z: 32 };
const CAMERA_TARGET = { x: 0, y: 1.5, z: -6 };

const VERTEX_SHADER = /* glsl */ `
  uniform float uTime;
  uniform vec2 uMouse;
  uniform float uMouseStrength;
  uniform float uSize;
  uniform float uPixelRatio;
  uniform float uCalm;

  attribute float aScale;

  varying float vHeight;
  varying float vGlow;
  varying float vFade;

  void main() {
    vec3 p = position;

    // Three interfering swells rather than one sine, so the surface never
    // resolves into a visible repeat at hero dwell times. uCalm scales every
    // displacement at once, so the reduced-motion pass is a quieter version of
    // the same surface rather than a different one.
    p.y = (
        sin(p.x * 0.22 + uTime * 0.55) * 1.15
      + sin(p.z * 0.28 - uTime * 0.42) * 0.95
      + sin((p.x + p.z) * 0.15 + uTime * 0.31) * 0.70
    ) * uCalm;
    p.x += sin(p.z * 0.18 + uTime * 0.22) * 0.35 * uCalm;

    // Cursor: a lift plus an outward ring, both gaussian-damped by distance.
    // The radius is wide on purpose — a tight one lands between grid rows and
    // reads as noise rather than as a response.
    float d = distance(p.xz, uMouse);
    float falloff = exp(-d * d / 70.0);
    p.y += (falloff * 2.0 + sin(d * 0.70 - uTime * 2.6) * falloff * 2.6)
         * uMouseStrength * uCalm;

    vHeight = p.y;
    vGlow = clamp(falloff * uMouseStrength, 0.0, 1.0);
    // Far rows dissolve into the hero's own darkness. The near edge is cut too,
    // but only in the last stretch — it sits just below the bottom of the
    // frame, so the wave still reaches the viewport edge.
    vFade = smoothstep(-30.0, -12.0, p.z) * (1.0 - smoothstep(16.0, 21.0, p.z));

    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    // Clamped: without it the nearest points swell into blobs under the glow.
    gl_PointSize = min(
      uSize * aScale * (14.0 / -mv.z) * (1.0 + vGlow * 1.6),
      13.0
    ) * uPixelRatio;
  }
`;

const FRAGMENT_SHADER = /* glsl */ `
  uniform vec3 uBase;
  uniform vec3 uAccent;

  varying float vHeight;
  varying float vGlow;
  varying float vFade;

  void main() {
    // Round the point sprite; square dots read as artefacts at this size.
    vec2 offset = gl_PointCoord - 0.5;
    float r2 = dot(offset, offset);
    if (r2 > 0.25) discard;

    float lift = clamp(vHeight * 0.28 + 0.4, 0.0, 1.0);
    vec3 color = mix(uBase, uAccent, clamp(vGlow * 1.15 + lift * 0.22, 0.0, 1.0));
    float alpha = smoothstep(0.25, 0.0, r2) * vFade * (0.16 + lift * 0.38 + vGlow * 0.75);

    // Written straight to the framebuffer: ShaderMaterial does no colour-space
    // conversion, so uBase/uAccent are plain sRGB and match the brand tokens.
    gl_FragColor = vec4(color, alpha);
  }
`;

const buildGeometry = (cols, rows) => {
  const count = cols * rows;
  const positions = new Float32Array(count * 3);
  const scales = new Float32Array(count);

  for (let iz = 0, i = 0; iz < rows; iz += 1) {
    for (let ix = 0; ix < cols; ix += 1, i += 1) {
      positions[i * 3] = (ix / (cols - 1) - 0.5) * GRID_WIDTH;
      positions[i * 3 + 1] = 0;
      positions[i * 3 + 2] = (iz / (rows - 1) - 0.5) * GRID_DEPTH + GRID_CENTER_Z;
      scales[i] = 0.6 + Math.random() * 0.8;
    }
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(positions, 3));
  geometry.setAttribute("aScale", new BufferAttribute(scales, 1));
  return geometry;
};

export const createParticleWave = (host) => {
  const canvas = document.createElement("canvas");
  canvas.className = "xn-particle-wave";
  canvas.setAttribute("aria-hidden", "true");
  canvas.style.cssText =
    "position:absolute;inset:0;display:block;width:100%;height:100%;pointer-events:none;z-index:1;";

  let renderer;
  try {
    renderer = new WebGLRenderer({
      canvas,
      alpha: true,
      antialias: false,
      powerPreference: "high-performance",
    });
  } catch {
    return null; // No WebGL — the hero is unchanged, which is the correct fallback.
  }
  renderer.setClearAlpha(0);

  const compact = window.innerWidth < 760;
  const geometry = buildGeometry(compact ? 120 : 200, compact ? 70 : 110);

  const uniforms = {
    uTime: { value: 0 },
    uMouse: { value: new Vector2(0, GRID_CENTER_Z) },
    uMouseStrength: { value: 0 },
    uSize: { value: compact ? 4.2 : 5.0 },
    uPixelRatio: { value: 1 },
    uCalm: { value: 1 },
    uBase: { value: new Vector3(...PAPER_RGB) },
    uAccent: { value: new Vector3(...ACCENT_RGB) },
  };

  const points = new Points(
    geometry,
    new ShaderMaterial({
      uniforms,
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      blending: AdditiveBlending,
    })
  );

  const scene = new Scene();
  scene.add(points);

  const camera = new PerspectiveCamera(55, 1, 0.1, 200);
  camera.position.set(CAMERA_HOME.x, CAMERA_HOME.y, CAMERA_HOME.z);

  const unproject = new Vector3();
  const pointerTarget = new Vector2(0, GRID_CENTER_Z);
  const parallax = { x: 0, y: 0, targetX: 0, targetY: 0 };

  const motionQuery = window.matchMedia ? window.matchMedia(MOTION_QUERY) : null;
  let timeScale = 1;

  // Read live rather than once: toggling the OS setting takes effect without a
  // reload, and the loop never has to stop to honour it.
  const applyMotionPreference = () => {
    const calm = !!(motionQuery && motionQuery.matches);
    uniforms.uCalm.value = calm ? CALM_AMPLITUDE : 1;
    timeScale = calm ? CALM_TIME_SCALE : 1;
  };
  applyMotionPreference();
  if (motionQuery) motionQuery.addEventListener("change", applyMotionPreference);

  let strengthTarget = 0;
  let elapsed = 0;
  let lastTime = 0;
  let frame = 0;
  let visible = true;
  let disposed = false;

  const resize = () => {
    const width = host.clientWidth || 1;
    const height = host.clientHeight || 1;
    const ratio = Math.min(window.devicePixelRatio || 1, 1.75);
    uniforms.uPixelRatio.value = ratio;
    renderer.setPixelRatio(ratio);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    // The fov is vertical, so a portrait hero would frame a narrow strip of the
    // grid. Widen it there to keep the same amount of wave in shot.
    camera.fov = camera.aspect < 1 ? 72 : 55;
    camera.updateProjectionMatrix();
    camera.lookAt(CAMERA_TARGET.x, CAMERA_TARGET.y, CAMERA_TARGET.z);
  };

  /** Cast the pointer onto the y=0 plane so the ripple tracks in world space. */
  const projectPointer = (clientX, clientY) => {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    const nx = ((clientX - rect.left) / rect.width) * 2 - 1;
    const ny = -(((clientY - rect.top) / rect.height) * 2 - 1);
    const inside = nx >= -1 && nx <= 1 && ny >= -1 && ny <= 1;
    strengthTarget = inside ? 1 : 0;
    if (!inside) return;

    parallax.targetX = nx * 2.2;
    parallax.targetY = ny * 1.1;

    unproject.set(nx, ny, 0.5).unproject(camera).sub(camera.position);
    if (Math.abs(unproject.y) < 1e-4) return;
    const distance = -camera.position.y / unproject.y;
    if (distance <= 0) return;

    pointerTarget.set(
      camera.position.x + unproject.x * distance,
      camera.position.z + unproject.z * distance
    );
  };

  const onPointerMove = (event) => projectPointer(event.clientX, event.clientY);
  const onPointerLeave = () => {
    strengthTarget = 0;
    parallax.targetX = 0;
    parallax.targetY = 0;
  };

  const render = () => {
    // Own accumulator, clamped: a dropped frame or a paused tab must not jump
    // the wave forward by the whole gap.
    const now = performance.now();
    elapsed += (lastTime ? Math.min((now - lastTime) / 1000, 0.05) : 0) * timeScale;
    lastTime = now;
    uniforms.uTime.value = elapsed;
    // Damped follow: the ripple trails the cursor instead of snapping to it.
    uniforms.uMouse.value.lerp(pointerTarget, 0.08);
    uniforms.uMouseStrength.value +=
      (strengthTarget - uniforms.uMouseStrength.value) * 0.06;

    parallax.x += (parallax.targetX - parallax.x) * 0.04;
    parallax.y += (parallax.targetY - parallax.y) * 0.04;
    camera.position.x = CAMERA_HOME.x + parallax.x;
    camera.position.y = CAMERA_HOME.y + parallax.y;
    camera.lookAt(CAMERA_TARGET.x, CAMERA_TARGET.y, CAMERA_TARGET.z);

    renderer.render(scene, camera);
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
    if (frame || disposed || !visible || document.hidden) return;
    lastTime = 0; // Discard the paused interval before it reaches `elapsed`.
    frame = requestAnimationFrame(loop);
  };

  const onVisibilityChange = () => (document.hidden ? stop() : start());
  const onContextLost = (event) => {
    event.preventDefault();
    stop();
  };
  const onContextRestored = () => {
    resize();
    start();
  };

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);

  // The hero scrolls away well before the page ends; there is nothing to draw
  // after that.
  const intersectionObserver = new IntersectionObserver(
    ([entry]) => {
      visible = entry.isIntersecting;
      if (visible) start();
      else stop();
    },
    { threshold: 0 }
  );
  intersectionObserver.observe(host);

  canvas.addEventListener("webglcontextlost", onContextLost);
  canvas.addEventListener("webglcontextrestored", onContextRestored);

  window.addEventListener("pointermove", onPointerMove, { passive: true });
  window.addEventListener("pointerleave", onPointerLeave, { passive: true });
  window.addEventListener("blur", onPointerLeave);
  document.addEventListener("visibilitychange", onVisibilityChange);

  const dispose = () => {
    disposed = true;
    stop();
    resizeObserver.disconnect();
    intersectionObserver.disconnect();
    canvas.removeEventListener("webglcontextlost", onContextLost);
    canvas.removeEventListener("webglcontextrestored", onContextRestored);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerleave", onPointerLeave);
    window.removeEventListener("blur", onPointerLeave);
    document.removeEventListener("visibilitychange", onVisibilityChange);
    if (motionQuery) motionQuery.removeEventListener("change", applyMotionPreference);
    geometry.dispose();
    points.material.dispose();
    renderer.dispose();
    canvas.remove();
  };

  const copy = host.querySelector(COPY_SELECTOR);
  if (copy) host.insertBefore(canvas, copy);
  else host.appendChild(canvas);

  resize();
  start();

  return { host, canvas, dispose };
};
