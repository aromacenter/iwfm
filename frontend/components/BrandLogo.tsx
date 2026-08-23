"use client";

/** X-admin márka-logó a Coffee X-Presso arculatával: kövér piros X +
 *  szögletes betűk, alatta széles betűközű alcím. Szövegként renderelődik
 *  (nem kép), így éles minden méretben, és sötét témában az alcím a
 *  központi .dark felülírással automatikusan világosra vált. */

const SIZES = {
  sm: { x: "text-[26px]", rest: "text-[19px]", sub: "text-[6px]", gap: "gap-[3px]" },
  md: { x: "text-[34px]", rest: "text-[25px]", sub: "text-[7.5px]", gap: "gap-1" },
  lg: { x: "text-[76px]", rest: "text-[56px]", sub: "text-[15px]", gap: "gap-2" },
} as const;

export default function BrandLogo({ size = "md" }: { size?: keyof typeof SIZES }) {
  const s = SIZES[size];
  return (
    <div
      className={`flex select-none flex-col items-center ${s.gap}`}
      title="X-admin — X-Presso management"
    >
      <div className="flex items-center leading-none text-[#E31E24]">
        <span
          className={`font-black leading-[0.85] ${s.x}`}
          style={{ fontFamily: "var(--font-orbitron)" }}
        >
          X
        </span>
        <span className={s.rest} style={{ fontFamily: "var(--font-michroma)" }}>
          -admin
        </span>
      </div>
      <div
        className={`uppercase tracking-[0.3em] text-slate-800 ${s.sub}`}
        style={{ fontFamily: "var(--font-michroma)" }}
      >
        X-Presso management
      </div>
    </div>
  );
}
