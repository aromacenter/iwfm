"use client";

/** Fotónéző nagyítással (2aeefcbd): a kép a képernyőre illesztve nyílik,
 *  két ujjal nagyítható/mozgatható (mint a térkép), egy ujjal húzható,
 *  dupla koppintás 2.5×-re / vissza, egérgörgő nagyít. ✕ vagy Esc zár. */

import { useCallback, useEffect, useRef, useState } from "react";

export default function PhotoLightbox({ src, onClose }: { src: string | null; onClose: () => void }) {
  const [scale, setScale] = useState(1);
  const [tx, setTx] = useState(0);
  const [ty, setTy] = useState(0);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ dist: number; scale: number; cx: number; cy: number; tx: number; ty: number } | null>(null);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const lastTap = useRef(0);

  const reset = useCallback(() => { setScale(1); setTx(0); setTy(0); }, []);
  useEffect(() => { reset(); }, [src, reset]);
  useEffect(() => {
    if (!src) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [src, onClose]);

  if (!src) return null;

  const clampScale = (s: number) => Math.min(8, Math.max(1, s));

  function onPointerDown(e: React.PointerEvent) {
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { dist: Math.hypot(a.x - b.x, a.y - b.y), scale, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, tx, ty };
      drag.current = null;
    } else if (pointers.current.size === 1) {
      drag.current = { x: e.clientX, y: e.clientY, tx, ty };
      const now = Date.now();
      if (now - lastTap.current < 300) {
        // dupla koppintás: 2.5× a koppintás helye körül, vagy vissza
        if (scale > 1) reset(); else { setScale(2.5); setTx((window.innerWidth / 2 - e.clientX) * 1.5); setTy((window.innerHeight / 2 - e.clientY) * 1.5); }
        lastTap.current = 0;
      } else lastTap.current = now;
    }
  }
  function onPointerMove(e: React.PointerEvent) {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2 && pinch.current) {
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const next = clampScale(pinch.current.scale * (dist / Math.max(1, pinch.current.dist)));
      const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      setScale(next);
      setTx(pinch.current.tx + (cx - pinch.current.cx));
      setTy(pinch.current.ty + (cy - pinch.current.cy));
    } else if (pointers.current.size === 1 && drag.current && scale > 1) {
      setTx(drag.current.tx + (e.clientX - drag.current.x));
      setTy(drag.current.ty + (e.clientY - drag.current.y));
    }
  }
  function onPointerUp(e: React.PointerEvent) {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pointers.current.size === 0) drag.current = null;
    if (pointers.current.size === 1) { const [p] = [...pointers.current.values()]; drag.current = { x: p.x, y: p.y, tx, ty }; }
  }
  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    setScale((s) => clampScale(s * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
  }

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/90" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        className="relative h-full w-full touch-none select-none overflow-hidden"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onWheel={onWheel}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt=""
          draggable={false}
          className="absolute left-1/2 top-1/2 max-h-full max-w-full"
          style={{ transform: `translate(-50%, -50%) translate(${tx}px, ${ty}px) scale(${scale})`, transformOrigin: "center", transition: pointers.current.size ? "none" : "transform 80ms" }}
        />
      </div>
      <div className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full bg-black/60 px-3 py-1 text-xs text-white">
        {Math.round(scale * 100)}% · két ujjal nagyítás, húzással mozgatás, dupla koppintás
      </div>
      <button type="button" onClick={onClose} aria-label="Bezárás" className="absolute right-3 top-3 rounded-full bg-black/60 px-3 py-1.5 text-lg leading-none text-white hover:bg-black/80">✕</button>
      {scale > 1 && (
        <button type="button" onClick={reset} className="absolute left-3 top-3 rounded-full bg-black/60 px-3 py-1.5 text-xs text-white hover:bg-black/80">100%</button>
      )}
    </div>
  );
}
