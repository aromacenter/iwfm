"use client";

/** Beüzemelő „menedzser” figura beszédbuborékkal. Inline SVG, CSS-animált
 *  (beugrás, integetés, pislogás, lebegés); a buborék szövege gépelve jelenik
 *  meg. Csökkentett mozgás kérésénél minden animáció kikapcsol (globals.css). */

import { useEffect, useState } from "react";

export function useTypewriter(text: string, speed = 22): { shown: string; done: boolean } {
  const [shown, setShown] = useState("");
  const [done, setDone] = useState(false);
  useEffect(() => {
    setShown("");
    setDone(false);
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setShown(text); setDone(true); return; }
    let i = 0;
    const id = window.setInterval(() => {
      i += 1;
      setShown(text.slice(0, i));
      if (i >= text.length) { window.clearInterval(id); setDone(true); }
    }, speed);
    return () => window.clearInterval(id);
  }, [text, speed]);
  return { shown, done };
}

export function ManagerFigure({ size = 180, wave = true }: { size?: number; wave?: boolean }) {
  return (
    <svg
      viewBox="0 0 200 220"
      width={size}
      height={size * 1.1}
      className="onb-figure"
      aria-hidden="true"
    >
      {/* árnyék */}
      <ellipse cx="100" cy="212" rx="54" ry="7" fill="#0f172a" opacity=".12" />
      {/* test: ing + mellény */}
      <path d="M58 126c0-18 18-30 42-30s42 12 42 30v62H58z" fill="#e2e8f0" />
      <path d="M72 126c0-14 12-22 28-22s28 8 28 22v62H72z" fill="#f8fafc" />
      <path d="M58 126c0-18 18-30 42-30v92H58z" fill="#1e293b" opacity=".92" />
      <path d="M142 126c0-18-18-30-42-30v92h42z" fill="#1e293b" opacity=".92" />
      {/* nyakkendő */}
      <path d="M100 104l6 10-6 38-6-38z" fill="#e31e24" />
      {/* bal kar (statikus) */}
      <path d="M62 132c-14 14-18 36-14 52 2 8 12 8 14 0 2-12 6-28 14-40" fill="#1e293b" opacity=".92" />
      <circle cx="56" cy="188" r="9" fill="#f5c9a6" />
      {/* jobb kar (integet) */}
      <g className={wave ? "onb-arm" : undefined} style={{ transformOrigin: "138px 134px" }}>
        <path d="M138 134c16 2 30-10 36-28 3-8-6-14-10-6-6 12-14 20-26 20" fill="#1e293b" opacity=".92" />
        <circle cx="176" cy="98" r="10" fill="#f5c9a6" />
      </g>
      {/* nyak + fej */}
      <rect x="90" y="84" width="20" height="16" rx="6" fill="#f0b891" />
      <circle cx="100" cy="62" r="34" fill="#f5c9a6" />
      {/* haj */}
      <path d="M66 58c2-22 18-34 34-34s32 12 34 34c-6-10-16-16-34-16S72 48 66 58z" fill="#3b2a20" />
      {/* szemüveg */}
      <g fill="none" stroke="#1e293b" strokeWidth="2.5">
        <circle cx="86" cy="64" r="9" /><circle cx="114" cy="64" r="9" /><path d="M95 64h10M77 62l-8-3M123 62l8-3" />
      </g>
      {/* szemek (pislognak) */}
      <g className="onb-eyes" style={{ transformOrigin: "100px 64px" }}>
        <circle cx="86" cy="64" r="3" fill="#1e293b" /><circle cx="114" cy="64" r="3" fill="#1e293b" />
      </g>
      {/* mosoly + orr + pír */}
      <path d="M88 80c6 6 18 6 24 0" fill="none" stroke="#b3573f" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M100 68v8" fill="none" stroke="#d9a07f" strokeWidth="2" strokeLinecap="round" />
      <circle cx="78" cy="74" r="4" fill="#f59e9e" opacity=".55" /><circle cx="122" cy="74" r="4" fill="#f59e9e" opacity=".55" />
      {/* jelvény */}
      <rect x="112" y="140" width="22" height="12" rx="3" fill="#fff" />
      <rect x="115" y="143" width="12" height="2" rx="1" fill="#e31e24" /><rect x="115" y="147" width="16" height="2" rx="1" fill="#94a3b8" />
    </svg>
  );
}

export default function OnboardingGuide({
  text,
  size = 110,
  wave = false,
  typed = true,
  className = "",
}: {
  text: string;
  size?: number;
  wave?: boolean;
  typed?: boolean;
  className?: string;
}) {
  const { shown } = useTypewriter(typed ? text : "", 18);
  return (
    <div className={`onb-guide flex items-end gap-3 ${className}`}>
      <ManagerFigure size={size} wave={wave} />
      <div className="onb-bubble relative mb-6 max-w-xs rounded-2xl border border-indigo-100 bg-white px-4 py-3 text-sm text-slate-700 shadow-md">
        <span className="absolute -left-2 bottom-5 h-4 w-4 rotate-45 border-b border-l border-indigo-100 bg-white" />
        {typed ? shown : text}
        {typed && shown.length < text.length && <span className="onb-caret">▍</span>}
      </div>
    </div>
  );
}
