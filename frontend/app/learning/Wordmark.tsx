"use client";

/** X-Learning szóvédjegy — ugyanaz a betűpár, mint az X-admin logónál. */

export default function LearningWordmark({ size = "md" }: { size?: "md" | "lg" }) {
  // nagy méretben mobilon kisebb, hogy egy sorban maradjon
  const x = size === "lg" ? "text-[44px] sm:text-[64px]" : "text-[30px]";
  const rest = size === "lg" ? "text-[32px] sm:text-[46px]" : "text-[22px]";
  return (
    <div className="flex select-none items-center whitespace-nowrap leading-none">
      <span className={`font-black leading-[0.85] text-[#E31E24] ${x}`} style={{ fontFamily: "var(--font-orbitron)" }}>
        X
      </span>
      <span className={`leading-none text-slate-800 ${rest}`} style={{ fontFamily: "var(--font-michroma)" }}>
        -Learning
      </span>
    </div>
  );
}
