import { useEffect, useMemo, useState } from "react";

/* An isometric line drawing of the screening pipeline. Each object is a hairline box on a base plate:
   EHR cabinet -> prescreen engine -> chart agent  -> reviewer -> coordinator. */

const C = Math.cos(Math.PI / 6);
const S = 7;
type V = [number, number, number];
const P = ([x, y, z]: V): [number, number] => [(x - y) * C * S, ((x + y) * 0.5 - z) * S];
const pts = (vs: V[]) => vs.map((v) => P(v).map((n) => n.toFixed(1)).join(",")).join(" ");

function Box({ x, y, z, w, d, h, top = "var(--iso-top)", front = "var(--iso-front)", side = "var(--iso-side)" }:
  { x: number; y: number; z: number; w: number; d: number; h: number; top?: string; front?: string; side?: string }) {
  const [x1, y1, z1] = [x + w, y + d, z + h];
  return (
    <g>
      <polygon className="edge" style={{ fill: side }} points={pts([[x1, y, z], [x1, y1, z], [x1, y1, z1], [x1, y, z1]])} />
      <polygon className="edge" style={{ fill: front }} points={pts([[x, y1, z], [x1, y1, z], [x1, y1, z1], [x, y1, z1]])} />
      <polygon className="edge" style={{ fill: top }} points={pts([[x, y, z1], [x1, y, z1], [x1, y1, z1], [x, y1, z1]])} />
    </g>
  );
}

const Line = ({ a, b, cls = "detail" }: { a: V; b: V; cls?: string }) => {
  const [p, q] = [P(a), P(b)];
  return <line className={cls} x1={p[0]} y1={p[1]} x2={q[0]} y2={q[1]} />;
};

/* text lying on the plate , and text standing on a +y face */
const flat = (v: V) => { const [px, py] = P(v); return `matrix(${C},0.5,${-C},0.5,${px},${py})`; };
const onFace = (v: V) => { const [px, py] = P(v); return `matrix(${C},0.5,0,1,${px},${py})`; };

/* the funnel in motion: where each record stops (x on the cable line) */
const FLOWS: { to: number; dur: number; delay: number; eligible?: boolean }[] = [
  { to: 34, dur: 4.2, delay: 0 }, { to: 34, dur: 4.2, delay: 0.9 }, { to: 34, dur: 4.2, delay: 1.8 },
  { to: 34, dur: 4.2, delay: 2.7 }, { to: 34, dur: 4.2, delay: 3.6 },
  { to: 58, dur: 6.4, delay: 1.3 }, { to: 82, dur: 8.6, delay: 4.4 },
  { to: 103, dur: 10.5, delay: 2.2, eligible: true },
];

function flowPath(to: number): string {
  const stops: V[] = [[11, 20, 2.3], [Math.min(to, 103), 20, 2.3]];
  if (to >= 103) stops.push([102, 16.5, 2.95]);
  return stops.map((v, i) => { const [x, y] = P(v); return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`; }).join(" ");
}

export type FigureStage = "ehr" | "prescreen" | "chart" | "reviewer" | "coordinator";

export interface FigureProps {
  live: boolean;
  screen: string[];
  captions: Record<FigureStage, string>;
  active?: FigureStage | null;
  onPick?: (s: FigureStage) => void;
  status: string;
}

export default function PipelineFigure({ live, screen, captions, active, onPick, status }: FigureProps) {
  const [hover, setHover] = useState<FigureStage | null>(null);
  const [typed, setTyped] = useState(0);
  const text = screen.join("\n");
  useEffect(() => { setTyped(0); }, [text]);
  useEffect(() => {
    if (!live || typed >= text.length) return;
    const t = setTimeout(() => setTyped((n) => n + 1), 28);
    return () => clearTimeout(t);
  }, [typed, text, live]);
  const shown = live ? text.slice(0, typed).split("\n") : [];
  const focus = hover || active || null;

  const box = useMemo(() => {
    const corners: V[] = [[0, 0, 0], [114, 0, 0], [114, 34, 0], [0, 34, 0], [4, 8, 22], [18, 8, 22]];
    const ps = corners.map(P);
    const xs = ps.map((p) => p[0]), ys = ps.map((p) => p[1]);
    const pad = 30;
    return `${Math.min(...xs) - pad} ${Math.min(...ys) - pad} ${Math.max(...xs) - Math.min(...xs) + pad * 2} ${Math.max(...ys) - Math.min(...ys) + pad * 2 + 10}`;
  }, []);

  const obj = (id: FigureStage, children: React.ReactNode) => (
    <g className={`obj ${focus === id ? "on" : ""}`} onMouseEnter={() => setHover(id)} onMouseLeave={() => setHover(null)}
      onClick={() => onPick?.(id)} style={{ filter: focus && focus !== id ? "brightness(0.55)" : "none" }}>{children}</g>
  );
  const label = (x: number, t: string) => (
    <text transform={flat([x, 31.2, 2.05])} fontSize={11} letterSpacing={2} style={{ fill: "var(--iso-label)" }}>{t}</text>
  );

  return (
    <div className="panel figure">
      <span className="fig-tl" />
      <span className="fig-tr">Screening pipeline</span>
      <svg className="iso" viewBox={box} role="img" aria-label="Screening pipeline">
        <defs>
          <radialGradient id="crt" cx="50%" cy="45%" r="70%">
            <stop offset="0" stopColor="#3a3a3a" />
            <stop offset="0.6" stopColor="#151515" />
            <stop offset="1" stopColor="#070707" />
          </radialGradient>
          <filter id="soft"><feGaussianBlur stdDeviation="6" /></filter>
          <filter id="glow" x="-200%" y="-200%" width="500%" height="500%">
            <feGaussianBlur stdDeviation="2.2" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>
        {/* base plate */}
        <g className="obj" style={{ cursor: "default" }}>
          <Box x={0} y={0} z={0} w={114} d={34} h={2} top="var(--iso-plate-top)" front="var(--iso-plate-front)" side="var(--iso-plate-side)" />
          {[[2.5, 2.5], [111.5, 2.5], [111.5, 31.5], [2.5, 31.5]].map(([x, y], i) => {
            const [px, py] = P([x, y, 2]);
            return <ellipse key={i} cx={px} cy={py} rx={2.4} ry={1.4} style={{ fill: "none", stroke: "rgb(var(--fg) / 0.2)", strokeWidth: 0.7 }} />;
          })}
        </g>
        {/* cables between the stations, with a coiled keyboard cable */}
        <polyline className="detail" points={pts([[18, 20, 2], [28, 20, 2]])} />
        <polyline className="detail" points={pts([[42, 20, 2], [52, 20, 2]])} />
        <polyline className="detail" points={pts([[66, 20, 2], [77, 20, 2]])} />
        <polyline className="detail" points={pts([[88, 20, 2], [96, 20, 2]])} />
        <path className="detail" d={(() => {
          let d = ""; for (let i = 0; i <= 26; i++) {
            const x = 67 + i * 0.2, z = 2.7 + Math.sin(i * 1.3) * 0.6, y = 26.5 + Math.cos(i * 1.3) * 0.6;
            const [px, py] = P([x, y, z]); d += `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`;
          } return d;
        })()} />

        {/* live flow: patient records travel the pipeline. Most stop in the prescreen, some reach the reviewer,
            a few eligible ones reach the coordinator. Faster while a run is live. */}
        <g className="anim" key={live ? "live" : "idle"}>
          {FLOWS.map((f, i) => {
            const dur = f.dur * (live ? 0.5 : 1);
            const path = flowPath(f.to);
            return (
              <g key={i}>
                <circle r={f.eligible ? 3.2 : 2.4} style={{ fill: f.eligible ? "var(--chart-green)" : "var(--text)" }} filter="url(#glow)" opacity={0}>
                  <animateMotion dur={`${dur}s`} begin={`${f.delay * (live ? 0.5 : 1)}s`} repeatCount="indefinite" path={path} />
                  <animate attributeName="opacity" dur={`${dur}s`} begin={`${f.delay * (live ? 0.5 : 1)}s`} repeatCount="indefinite"
                    values="0;0.95;0.95;0" keyTimes="0;0.08;0.9;1" />
                </circle>
              </g>
            );
          })}
        </g>
        {obj("ehr", <>
          <Box x={4} y={8} z={2} w={14} d={14} h={20} />
          {[8, 13, 18].map((z) => <Line key={z} a={[5, 22, z]} b={[17, 22, z]} />)}
          {[5, 10, 15, 20].map((z) => <Line key={z} a={[9.5, 22, z]} b={[12.5, 22, z]} />)}
          {[11, 13, 15, 17].map((y) => <Line key={y} a={[7, y, 22]} b={[15, y, 22]} />)}
          {[6.5, 11.5, 16.5, 21].map((z, i) => {
            const [px, py] = P([16.2, 22, z]);
            return <circle key={z} cx={px} cy={py} r={1.6} style={{ fill: "var(--chart-green)" }} className="anim">
              <animate attributeName="opacity" values="0.15;1;0.15" dur={`${1.6 + i * 0.7}s`} begin={`${i * 0.4}s`} repeatCount="indefinite" />
            </circle>;
          })}
          {label(4, "EHR · OMOP")}
        </>)}
        {obj("prescreen", <>
          <Box x={28} y={10} z={2} w={14} d={12} h={7} />
          <g className="pulse-bar" style={{ animationDelay: `${0 * 0.45}s` }}><Box x={30} y={12.3} z={9} w={10} d={2} h={0.9} top="var(--iso-hi1)" /></g>
          <g className="pulse-bar" style={{ animationDelay: `${1 * 0.45}s` }}><Box x={31.5} y={15.3} z={9} w={7} d={2} h={0.9} top="var(--iso-hi2)" /></g>
          <g className="pulse-bar" style={{ animationDelay: `${2 * 0.45}s` }}><Box x={33} y={18.3} z={9} w={4} d={2} h={0.9} top="var(--iso-hi3)" /></g>
          {[30, 33, 36, 39].map((x) => <Line key={x} a={[x, 22, 3.5]} b={[x, 22, 7.5]} />)}
          {label(27.5, "PRESCREEN")}
        </>)}
        {obj("chart", <>
          <Box x={52} y={7} z={2} w={14} d={14} h={18} top="var(--iso-top2)" />
          {[10, 11.2, 12.4, 13.6, 14.8].map((y) => <Line key={y} a={[66, y, 15]} b={[66, y, 19]} />)}
          {[55, 56.4, 57.8, 59.2].map((x) => <Line key={x} a={[x, 9, 20]} b={[x, 13, 20]} />)}
          <polygon className="edge" style={{ fill: "var(--iso-bezel)" }} points={pts([[53.3, 21, 9.2], [64.7, 21, 9.2], [64.7, 21, 23.6], [53.3, 21, 23.6]])} />
          <polygon style={{ fill: live ? "url(#crt)" : "var(--iso-glass)", stroke: "rgb(var(--fg) / 0.18)", strokeWidth: 0.7 }}
            points={pts([[54.2, 21, 10.2], [63.8, 21, 10.2], [63.8, 21, 22.6], [54.2, 21, 22.6]])} />
          {live && <polygon className="screen-glow" fill="rgb(255 255 255 / 0.10)" filter="url(#soft)"
            points={pts([[55, 21, 11.5], [63, 21, 11.5], [63, 21, 21.5], [55, 21, 21.5]])} />}
          {!live && (
            <text transform={onFace([55, 21, 17])} fontSize={6} style={{ fill: "var(--iso-prompt)" }}>{">"} <tspan className="blink">▌</tspan></text>
          )}
          <polygon className="anim scan" style={{ fill: "rgb(var(--fg) / 0.07)" }}
            points={pts([[54.2, 21, 22.6], [63.8, 21, 22.6], [63.8, 21, 21.6], [54.2, 21, 21.6]])} />
          {(() => { const [px, py] = P([64, 21, 4.4]); return (
            <circle cx={px} cy={py} r={1.5} className="anim" style={{ fill: live ? "#8ee39a" : "var(--iso-led-off)" }}>
              <animate attributeName="opacity" values="1;0.2;1" dur={live ? "0.8s" : "2.6s"} repeatCount="indefinite" />
            </circle>); })()}
          {live && (
            <g>
              <text transform={onFace([55.2, 21, 20.6])} fontSize={8} fontWeight={600} fill="#f4f4f4">TrialIQ</text>
              {shown.slice(-4).map((l, i, arr) => (
                <text key={i} transform={onFace([54.9, 21, 18.2 - (arr.length - 1 - i) * 1.5])} fontSize={4.6} fill="#dcdcdc">
                  {l.slice(0, 24)}{i === arr.length - 1 && <tspan fill="#ffffff">▌</tspan>}
                </text>
              ))}
            </g>
          )}
          <Line a={[59.5, 21, 6.2]} b={[64, 21, 6.2]} />
          {[54, 54.7, 55.4, 56.1, 56.8].map((x) => <Line key={x} a={[x, 21, 3.8]} b={[x, 21, 6.6]} />)}
          <Box x={50} y={24.5} z={2} w={17} d={5} h={1.2} top="var(--iso-top2)" />
          {Array.from({ length: 4 }).flatMap((_, r) => Array.from({ length: 10 }).map((__, k) => {
            const [x, y] = [51 + k * 1.5, 25.2 + r * 1.05];
            return <polygon key={`${r}-${k}`} style={{ fill: "var(--iso-key)", stroke: "rgb(var(--fg) / 0.16)", strokeWidth: 0.4 }}
              points={pts([[x, y, 3.25], [x + 1.15, y, 3.25], [x + 1.15, y + 0.8, 3.25], [x, y + 0.8, 3.25]])} />;
          }))}
          {label(52, "CHART AGENT")}
        </>)}
        {obj("reviewer", <>
          <Box x={77} y={11} z={2} w={11} d={11} h={10} />
          <polyline className="draw-check" fill="none" strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round"
            points={pts([[80, 22, 7.5], [82, 22, 5.5], [86, 22, 10]])} style={{ stroke: "rgb(var(--fg) / 0.6)", animationDuration: live ? "2.4s" : "4.8s" }} />
          <ellipse cx={P([82.5, 16.5, 12])[0]} cy={P([82.5, 16.5, 12])[1]} rx={12} ry={7} style={{ fill: "none", stroke: "rgb(var(--fg) / 0.18)", strokeWidth: 0.8 }} />
          {label(76.5, "REVIEWER")}
        </>)}
        {obj("coordinator", <>
          <Box x={96} y={8} z={2} w={13} d={17} h={0.8} top="var(--iso-clip)" />
          <Box x={100} y={7} z={2.8} w={5} d={3} h={1.2} top="var(--iso-hi1)" />
          {[12, 14.5, 17, 19.5].map((y, i) => <Line key={y} a={[98, y, 2.85]} b={[i % 2 ? 104 : 106, y, 2.85]} />)}
          <polyline fill="none" style={{ stroke: "var(--chart-green)" }} strokeWidth={1.1} points={pts([[98.3, 22.3, 2.9], [99.2, 23.2, 2.9], [101, 21.2, 2.9]])} />
          {(() => { const [px, py] = P([102, 16.5, 2.9]); return FLOWS.filter((f) => f.eligible).map((f, i) => {
            const dur = f.dur * (live ? 0.5 : 1);
            return <ellipse key={i} cx={px} cy={py} rx={2} ry={1.15} fill="none" style={{ stroke: "var(--chart-green)" }} strokeWidth={1} opacity={0} className="anim">
              <animate attributeName="rx" values="2;2;22" keyTimes="0;0.9;1" dur={`${dur}s`} begin={`${f.delay * (live ? 0.5 : 1)}s`} repeatCount="indefinite" />
              <animate attributeName="ry" values="1.15;1.15;12.7" keyTimes="0;0.9;1" dur={`${dur}s`} begin={`${f.delay * (live ? 0.5 : 1)}s`} repeatCount="indefinite" />
              <animate attributeName="opacity" values="0;0;0.9;0" keyTimes="0;0.9;0.92;1" dur={`${dur}s`} begin={`${f.delay * (live ? 0.5 : 1)}s`} repeatCount="indefinite" />
            </ellipse>;
          }); })()}
          {label(95.5, "COORDINATOR")}
        </>)}
      </svg>
      <span className="fig-bl">{focus ? captions[focus] : "Hover an object · click to open"}</span>
      <span className="fig-br">{status}</span>
    </div>
  );
}
