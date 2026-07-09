import { useEffect, useRef } from 'react';

type Ripple = { x: number; y: number; startTime: number };

// 1:1 port of cue.sf.tools ASCII orb (noise-driven sphere + click ripples).
export function AsciiOrb({
  size = 28,
  className = 'text-sky-400',
  interactive = true,
  animated = true,
}: {
  size?: number;
  className?: string;
  interactive?: boolean;
  /** When false, renders a single static frame (no animation loop). */
  animated?: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const preRef = useRef<HTMLPreElement>(null);
  const ripplesRef = useRef<Ripple[]>([]);
  const getTimeRef = useRef<(now: number) => number>(() => 0);
  const fontPx = (size / 28) * 1.8;
  const blueHue = !className.includes('text-black');

  useEffect(() => {
    const pre = preRef.current;
    const wrap = wrapRef.current;
    if (!pre || !wrap) return;

    const Fc = ' .:-=+*#%@';
    const Jc = ' ';
    const Ic = 28;
    const Wc = 28;
    const Q0 = 30;
    const ep = 20;
    const ar = 1;
    const V0 = 20;
    const tp = 1.6;

    const clamp = (i: number, s: number, c: number) => Math.max(s, Math.min(c, i));
    const frac = (i: number) => i - Math.floor(i);
    const lerp = (i: number, s: number, c: number) => i + (s - i) * c;
    const smooth = (i: number) => i * i * (3 - 2 * i);
    const hash = (i: number, s: number, c: number) =>
      frac(Math.sin(i * 127.1 + s * 311.7 + c * 74.7) * 43758.5453123);
    const noise = (i: number, s: number, c: number) => {
      const r = Math.floor(i);
      const o = Math.floor(s);
      const h = r + 1;
      const m = o + 1;
      const g = smooth(i - r);
      const v = smooth(s - o);
      const p = hash(r, o, c);
      const T = hash(h, o, c);
      const A = hash(r, m, c);
      const L = hash(h, m, c);
      return lerp(lerp(p, T, g), lerp(A, L, g), v);
    };
    const field = (i: number, s: number, c: number, r: number) => {
      const o = noise(i / ep, s / ep + c, r);
      const h = noise(i / 11 + 17.1, s / 11 + c * 1.35 + 3.2, r + 11);
      return clamp(o * 0.68 + h * 0.32, 0, 1);
    };

    const render = (i: number, s: number, c: number, r: number, o: Ripple[], h: number) => {
      const m = i / 2;
      const g = s / 2;
      const v = Math.max(1, i / 2 - 1);
      const p = Math.max(1, s / (2 * h) - 1);
      const T = Math.min(v, p);
      const A = T * T;
      const L = 1 / T;
      const Z = 1 / h;
      let K = '';
      for (let G = 0; G < s; G++) {
        const Y = (G - g) * Z;
        const V = Y * Y;
        for (let z = 0; z < i; z++) {
          if (V >= A) {
            K += Jc;
            continue;
          }
          const O = z - m;
          const q = Math.sqrt(O * O + V) * L;
          if (q >= 1) {
            K += Jc;
            continue;
          }
          const U = 1 - q * q;
          let H = field(z, G, c, r) * U;
          for (let fi2 = 0; fi2 < o.length; fi2++) {
            const F = o[fi2];
            const ge = c - F.startTime;
            if (ge < 0 || ge > ar) continue;
            const Ee = z - F.x;
            const ze = (G - F.y) * Z;
            const bt = Math.sqrt(Ee * Ee + ze * ze);
            const tt = ge * V0;
            const Ie = Math.abs(bt - tt);
            if (Ie >= tp || q >= 0.95) continue;
            const D = 1 - ge / ar;
            const X = (1 - Ie / tp) * D;
            const J = 1 - Math.pow(q, 3);
            H = Math.min(1, H + X * 0.8 * J);
          }
          const P = Math.min(Fc.length - 1, Math.floor(H * Fc.length));
          K += Fc[P] || Jc;
        }
        if (G < s - 1) K += '\n';
      }
      return K;
    };

    const probe = document.createElement('span');
    probe.textContent = 'M';
    probe.style.position = 'absolute';
    probe.style.visibility = 'hidden';
    probe.style.whiteSpace = 'pre';
    pre.appendChild(probe);
    const gr = probe.getBoundingClientRect();
    const charW = gr.width || 8;
    const charH = gr.height || 12;
    const aspect = charW / charH;
    pre.removeChild(probe);

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const startTime = performance.now();
    const seed = Math.random() * 1000;
    const frameInterval = 1000 / Q0;
    let rafId = 0;
    let lastFrame = -Infinity;
    const staticTime = 2.5;

    const getTime = (now: number) =>
      !animated || reducedMotion ? staticTime : (now - startTime) / 1000;
    getTimeRef.current = getTime;

    const draw = (now: number) => {
      const t = getTime(now);
      ripplesRef.current = ripplesRef.current.filter(p => t - p.startTime < ar);
      pre.textContent = render(Ic, Wc, t, seed, ripplesRef.current, aspect);
    };

    const addRipple = (x: number, y: number, now: number) => {
      ripplesRef.current = ripplesRef.current.concat([{ x, y, startTime: getTime(now) }]);
      draw(now);
    };

    const loop = (now: number) => {
      if (now - lastFrame >= frameInterval) {
        lastFrame = now;
        draw(now);
      }
      rafId = requestAnimationFrame(loop);
    };

    const onClick = (event: MouseEvent) => {
      const rect = pre.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const x = ((event.clientX - rect.left) / rect.width) * (Ic - 1);
      const y = ((event.clientY - rect.top) / rect.height) * (Wc - 1);
      addRipple(x, y, performance.now());
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      addRipple(Ic / 2, Wc / 2, performance.now());
    };

    draw(startTime);
    if (animated && !reducedMotion) rafId = requestAnimationFrame(loop);

    if (interactive && animated) {
      wrap.addEventListener('click', onClick);
      wrap.addEventListener('keydown', onKeyDown);
    }

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      if (interactive && animated) {
        wrap.removeEventListener('click', onClick);
        wrap.removeEventListener('keydown', onKeyDown);
      }
    };
  }, [interactive, animated]);

  return (
    <div
      ref={wrapRef}
      className={`ascii-orb-wrap relative flex shrink-0 items-center justify-center ${
        interactive
          ? 'cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-2'
          : 'pointer-events-none'
      }`}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-hidden="true"
      style={{ width: size, height: size }}
    >
      {blueHue && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 scale-110 rounded-full"
          style={{
            background: 'radial-gradient(circle, rgba(56, 189, 248, 0.22) 0%, rgba(37, 99, 235, 0.08) 45%, transparent 72%)',
          }}
        />
      )}
      <pre
        ref={preRef}
        className={`ascii-orb relative z-[1] m-0 select-none whitespace-pre font-mono ${className}`}
        style={{
          fontSize: `${fontPx}px`,
          lineHeight: `${fontPx}px`,
          ...(blueHue
            ? { textShadow: '0 0 10px rgba(56, 189, 248, 0.55), 0 0 22px rgba(37, 99, 235, 0.28)' }
            : {}),
        }}
      />
    </div>
  );
}
