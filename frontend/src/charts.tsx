import { useEffect, useId, useMemo, useState } from "react";

/* Small SVG charts, no library:
   Gauge      a half ring of segments that light up to the value
   AreaChart  a line with a soft gradient fill and a hover read-out
   TickMeter  a row of ticks, red to amber to green, filled to the value
   Timeline   list rows with a colored rule, a time line and a status pill */

const GREEN = ["#3f8f4c", "#4fae5c", "#62c96f", "#86df92", "#b4f0bb"];

function useAnimated(target: number, ms = 700) {
  const [v, setV] = useState(0);
  useEffect(() => {
    let raf = 0;
    const start = performance.now();
    const from = v;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      setV(from + (target - from) * (1 - Math.pow(1 - t, 3)));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target]); // eslint-disable-line react-hooks/exhaustive-deps
  return v;
}

export function Gauge({ value, max, label, display, segments = 26, size = 260 }:
  { value: number; max: number; label: string; display?: string; segments?: number; size?: number }) {
  const frac = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  const shown = useAnimated(frac);
  const lit = Math.round(shown * segments);
  const [w, h] = [200, 116];
  const [cx, cy, r0, r1] = [100, 104, 68, 96];
  return (
    <div className="gauge" style={{ maxWidth: size }}>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${label}: ${display ?? `${value}/${max}`}`}>
        {Array.from({ length: segments }).map((_, i) => {
          const a = Math.PI - (i / (segments - 1)) * Math.PI;
          const [c, s] = [Math.cos(a), Math.sin(a)];
          const on = i < lit;
          const color = on ? GREEN[Math.min(GREEN.length - 1, Math.floor((i / Math.max(1, lit)) * GREEN.length))] : "var(--gauge-off)";
          return <line key={i} x1={cx + c * r0} y1={cy - s * r0} x2={cx + c * r1} y2={cy - s * r1} style={{ stroke: color }}
            strokeWidth={7.2} strokeLinecap="round" />;
        })}
        <text x={cx} y={cy - 16} textAnchor="middle" className="gauge-v">{display ?? `${value} / ${max}`}</text>
        <text x={cx} y={cy + 2} textAnchor="middle" className="gauge-l">{label}</text>
      </svg>
    </div>
  );
}

export function AreaChart({ points, height = 150, format = (v: number) => String(Math.round(v)), xLabel = (x: number) => String(x) }:
  { points: { x: number; y: number }[]; height?: number; format?: (v: number) => string; xLabel?: (x: number) => string }) {
  const id = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);
  const w = 600;
  const pad = { t: 10, b: 6 };
  const geo = useMemo(() => {
    if (points.length < 2) return null;
    const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
    const y1 = Math.max(1, ...ys);
    const X = (x: number) => ((x - x0) / Math.max(1e-9, x1 - x0)) * w;
    const Y = (y: number) => pad.t + (1 - y / y1) * (height - pad.t - pad.b);
    let line = "";
    points.forEach((p, i) => {
      if (i === 0) line += `M${X(p.x)},${Y(p.y)}`;
      else line += `H${X(p.x)}V${Y(p.y)}`; // steps: each patient adds one
    });
    return { X, Y, line, area: `${line}V${height}H${X(points[0].x)}Z` };
  }, [points, height]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!geo) return <div className="empty" style={{ height }}>Waiting for data</div>;
  const hp = hover != null ? points[hover] : null;
  return (
    <div className="area" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" style={{ height }}
        onMouseMove={(e) => {
          const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const x = ((e.clientX - box.left) / box.width) * w;
          let best = 0;
          points.forEach((p, i) => { if (Math.abs(geo.X(p.x) - x) < Math.abs(geo.X(points[best].x) - x)) best = i; });
          setHover(best);
        }}>
        <defs>
          <linearGradient id={`g${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#62c96f" stopOpacity="0.32" />
            <stop offset="1" stopColor="#62c96f" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((f) => <line key={f} x1={0} x2={w} y1={height * f} y2={height * f} style={{ stroke: "rgb(var(--fg) / 0.06)" }} />)}
        <path d={geo.area} fill={`url(#g${id})`} />
        <path d={geo.line} fill="none" style={{ stroke: "var(--chart-green)" }} strokeWidth={1.8} vectorEffect="non-scaling-stroke" />
        {hp && <line x1={geo.X(hp.x)} x2={geo.X(hp.x)} y1={0} y2={height} style={{ stroke: "rgb(var(--fg) / 0.25)" }} vectorEffect="non-scaling-stroke" />}
      </svg>
      {hp && (
        <div className="area-tip" style={{ left: `${(geo.X(hp.x) / w) * 100}%` }}>
          <b>{format(hp.y)}</b> · {xLabel(hp.x)}
        </div>
      )}
    </div>
  );
}

export function TickMeter({ value, max = 1, ticks = 12, label, title }:
  { value: number; max?: number; ticks?: number; label?: string; title?: string }) {
  const frac = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  const on = Math.round(frac * ticks);
  const color = (i: number) => {
    const t = i / Math.max(1, ticks - 1);
    return t < 0.34 ? "#e8806f" : t < 0.67 ? "#e6c45e" : "#6fd67b";
  };
  return (
    <span className="ticks" title={title}>
      <span className="ticks-bar">
        {Array.from({ length: ticks }).map((_, i) => (
          <i key={i} style={{ background: i < on ? color(i) : "rgb(var(--fg) / 0.1)" }} />
        ))}
      </span>
      {label !== undefined && <span className="ticks-l">{label}</span>}
    </span>
  );
}

export interface TimelineItem {
  key: string;
  time: string;
  meta?: string;
  title: React.ReactNode;
  tone: "green" | "amber" | "red" | "blue" | "grey" | "violet";
  pill?: React.ReactNode;
  onClick?: () => void;
}

export function Timeline({ items, empty = "Nothing yet" }: { items: TimelineItem[]; empty?: string }) {
  if (!items.length) return <div className="empty">{empty}</div>;
  return (
    <div className="timeline">
      {items.map((it) => (
        <div key={it.key} className={`tl-row ${it.onClick ? "clickable-row" : ""}`} onClick={it.onClick}>
          <span className={`tl-rule ${it.tone}`} />
          <div className="tl-body">
            <div className="tl-time">{it.time}{it.meta && <> · {it.meta}</>}</div>
            <div className="tl-title">{it.title}</div>
          </div>
          {it.pill && <div className="tl-pill">{it.pill}</div>}
        </div>
      ))}
    </div>
  );
}

/* Meter: parts of one whole as rounded segments; a striped (hatched) part is the one that matters. */
export function Meter({ parts }: { parts: { value: number; tone: string; striped?: boolean }[] }) {
  const total = parts.reduce((n, p) => n + p.value, 0);
  const shown = useAnimated(1, 800);
  if (!total) return <div className="meter"><span className="seg empty" style={{ flex: 1 }} /></div>;
  return (
    <div className="meter">
      {parts.filter((p) => p.value > 0).map((p, i) => (
        <span key={i} className={`seg ${p.tone} ${p.striped ? "striped" : ""}`} style={{ flexGrow: p.value * shown + 0.0001, flexBasis: 0 }} />
      ))}
    </div>
  );
}

/* FlowBars: stacked bars (a below, b on top) joined by soft bands, so the change from one run to the next reads
   as a flow. The total sits above each bar. */
export function FlowBars({ bars, height = 230 }: { bars: { label: string; a: number; b: number; total: number; title?: string }[]; height?: number }) {
  const grow = useAnimated(1, 900);
  const [hover, setHover] = useState<number | null>(null);
  if (!bars.length) return <div className="empty">No chart reviews yet</div>;
  const W = 640, top = 30, base = height - 30, bw = 40, gap = 4;
  const max = Math.max(1, ...bars.map((b) => b.a + b.b));
  const slot = W / bars.length;
  const x = (i: number) => slot * i + (slot - bw) / 2;
  const h = (v: number) => (v / max) * (base - top) * grow;
  const ya = (i: number) => base - h(bars[i].a);
  const yb = (i: number) => ya(i) - (bars[i].b ? gap : 0) - h(bars[i].b);
  const band = (i: number, y1: (j: number) => number, y0: (j: number) => number) =>
    `M${x(i) + bw + 3} ${y0(i)} L${x(i) + bw + 3} ${y1(i)} L${x(i + 1) - 3} ${y1(i + 1)} L${x(i + 1) - 3} ${y0(i + 1)} Z`;
  const h2 = hover != null ? bars[hover] : null;
  return (
    <div className="flowbars">
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Accepted and stopped answers per run" onMouseLeave={() => setHover(null)}>
        <defs>
          <pattern id="fb-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--red)" opacity="0.55" /><line x1="0" y1="0" x2="0" y2="6" stroke="var(--red)" strokeWidth="3" />
          </pattern>
        </defs>
        {bars.slice(0, -1).map((_, i) => (
          <g key={`band${i}`}>
            <path d={band(i, ya, () => base)} className="fb-band a" />
            <path d={band(i, yb, (j) => ya(j) - (bars[j].b ? gap : 0))} className="fb-band b" />
          </g>
        ))}
        {bars.map((b, i) => (
          <g key={i} onMouseEnter={() => setHover(i)} className={hover === i ? "fb-bar on" : "fb-bar"}>
            <rect x={x(i) - 8} y={top - 20} width={bw + 16} height={base - top + 44} fill="transparent" />
            <rect x={x(i)} y={ya(i)} width={bw} height={Math.max(0, base - ya(i))} rx="7" className="fb-a" />
            {b.b > 0 && <rect x={x(i)} y={yb(i)} width={bw} height={Math.max(0, h(b.b))} rx="7" fill="url(#fb-hatch)" />}
            <text x={x(i) + bw / 2} y={yb(i) - 9} textAnchor="middle" className="fb-total">{b.total}</text>
            <text x={x(i) + bw / 2} y={height - 8} textAnchor="middle" className="fb-label">{b.label}</text>
          </g>
        ))}
      </svg>
      <div className="trace-detail">{h2 ? <><b>{h2.title}</b> · {h2.a} accepted · {h2.b} stopped · {h2.total - h2.a - h2.b} open</> : "Point to a run"}</div>
    </div>
  );
}

/* IsoBlocks: one isometric block per item on a plate, like the cube of the logo. A raised green block is right, a
   red block is wrong, a flat amber tile was left open for a person. The blocks rise in a wave, back to front. */
export type IsoItem = { state: "right" | "wrong" | "open"; label: string };

export function IsoBlocks({ items, cols = 8, onHover }: { items: IsoItem[]; cols?: number; onHover?: (i: IsoItem | null) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  const grow = useAnimated(1, 1400);
  const rows = Math.ceil(items.length / cols);
  const tw = 28, th = 14, H = 22, pad = 10;
  const ox = (rows * tw) / 2 + pad;
  const oy = H + pad + th / 2;
  const W = ((cols + rows) * tw) / 2 + pad * 2;
  const Ht = oy + ((cols + rows) * th) / 2 + pad;
  const at = (r: number, c: number) => ({ x: ox + ((c - r) * tw) / 2, y: oy + ((c + r) * th) / 2 });
  // cells in drawing order, back to front; the items fill them in that order. Right at the back, open in front.
  const order = items.map((_, i) => i).sort((a, b) => {
    const [ra, ca, rb, cb] = [Math.floor(a / cols), a % cols, Math.floor(b / cols), b % cols];
    return ra + ca - (rb + cb) || ca - cb;
  });
  const rank: Record<IsoItem["state"], number> = { right: 0, wrong: 1, open: 2 };
  const sorted = items.map((it, i) => ({ it, i })).sort((x, y) => rank[x.it.state] - rank[y.it.state] || x.i - y.i).map((x) => x.it);
  const cellItem = new Map(order.map((cell, n) => [cell, sorted[n]]));
  const g = (x: number, y: number, h: number, k = 0.86) => {
    const [w, v] = [(tw / 2) * k, (th / 2) * k];
    return { top: [x, y - v - h], right: [x + w, y - h], bottom: [x, y + v - h], left: [x - w, y - h], gr: [x + w, y], gb: [x, y + v], gl: [x - w, y] };
  };
  const pts = (...p: number[][]) => p.map((q) => q.join(",")).join(" ");
  const corner = (r: number, c: number) => at(r - 0.5, c - 0.5);
  const plate = [corner(0, 0), corner(0, cols), corner(rows, cols), corner(rows, 0)].map((p) => [p.x, p.y]);
  const span = order.length + 10;
  return (
    <svg viewBox={`0 0 ${W} ${Ht}`} className="isoblocks" role="img" onMouseLeave={() => { setHover(null); onHover?.(null); }}
      aria-label={`${items.filter((i) => i.state === "right").length} right, ${items.filter((i) => i.state === "wrong").length} wrong, ${items.filter((i) => i.state === "open").length} open`}>
      <polygon points={pts(...plate.map(([x, y]) => [x, y + 6]))} className="ib-plate-side" />
      <polygon points={pts(...plate)} className="ib-plate" />
      {order.map((i, n) => {
        const it = cellItem.get(i)!;
        const { x, y } = at(Math.floor(i / cols), i % cols);
        const rise = Math.max(0, Math.min(1, (grow * span - n) / 10));
        const base = it.state === "open" ? 2 : it.state === "wrong" ? H * 0.8 : H;
        const h = base * rise + (hover === i ? 5 : 0);
        const f = g(x, y, h);
        return (
          <g key={i} className={`ib ${it.state} ${hover === i ? "on" : ""}`} onMouseEnter={() => { setHover(i); onHover?.(it); }}>
            <polygon points={pts(f.left, f.bottom, f.gb, f.gl)} className="l" />
            <polygon points={pts(f.bottom, f.right, f.gr, f.gb)} className="r" />
            <polygon points={pts(f.top, f.right, f.bottom, f.left)} className="t" />
          </g>
        );
      })}
    </svg>
  );
}
