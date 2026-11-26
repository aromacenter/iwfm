"use client";

/** Publikus ügyfél-értékelés (186. kör): a munkalap tokenes linkjéről — 1–5
 *  csillag + egy mondat, egyszer. Belépés nélkül, az ügyfél saját eszközén
 *  (e-mail link) vagy a szerelő telefonján aláírás után. */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";

interface Info {
  serial: string;
  company_name: string;
  machine: string | null;
  technician: string | null;
  rated: boolean;
  rating: number | null;
}

const TXT = {
  hu: {
    title: "Hogyan voltál elégedett a munkánkkal?", sub: "Egy kattintás, és sokat segít nekünk.", comment: "Megjegyzés (nem kötelező)",
    send: "Küldés", thanks: "Köszönjük az értékelést!", thanksLow: "Köszönjük, hogy szóltál — hamarosan keresünk, hogy rendbe tegyük.",
    already: "Ezt a munkalapot már értékelték.", notFound: "Az értékelő link érvénytelen.", loading: "Betöltés…", err: "Nem sikerült elküldeni — próbáld újra.",
    labels: ["Nagyon rossz", "Rossz", "Közepes", "Jó", "Kiváló"], machine: "Gép", tech: "Szerelő",
  },
  en: {
    title: "How satisfied were you with our work?", sub: "One click helps us a lot.", comment: "Comment (optional)",
    send: "Send", thanks: "Thank you for your rating!", thanksLow: "Thanks for telling us — we will contact you shortly to make it right.",
    already: "This worksheet has already been rated.", notFound: "The rating link is invalid.", loading: "Loading…", err: "Could not send — please try again.",
    labels: ["Very poor", "Poor", "Average", "Good", "Excellent"], machine: "Machine", tech: "Technician",
  },
};

export default function ErtekelesPage() {
  const { token } = useParams<{ token: string }>();
  const [info, setInfo] = useState<Info | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [stars, setStars] = useState(0);
  const [hover, setHover] = useState(0);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lang, setLang] = useState<"hu" | "en">("hu");
  const L = TXT[lang];

  useEffect(() => {
    // a nyelv a böngészőből, a hidratálás után (SSR: magyar)
    const id = window.setTimeout(() => {
      try {
        if (!navigator.language.toLowerCase().startsWith("hu")) setLang("en");
      } catch { /* nincs navigator */ }
    }, 0);
    return () => window.clearTimeout(id);
  }, []);
  useEffect(() => {
    if (!token) return;
    api.get<Info>(`/api/public/rating/${token}`).then(setInfo).catch(() => setNotFound(true));
  }, [token]);

  async function send() {
    if (!stars) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/public/rating/${token}`, { rating: stars, comment: comment.trim() || null, source: "link" });
      setDone(stars);
    } catch (e) {
      if (e instanceof ApiError && e.code === "rating.already_rated") setInfo((i) => (i ? { ...i, rated: true } : i));
      else setError(L.err);
    } finally {
      setBusy(false);
    }
  }

  if (notFound) return <main className="mx-auto max-w-md p-8 text-center text-slate-600">{L.notFound}</main>;
  if (!info) return <main className="mx-auto max-w-md p-8 text-center text-slate-500">{L.loading}</main>;

  return (
    <main className="mx-auto max-w-md space-y-4 p-4 sm:p-8">
      <div className="rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm">
        <p className="text-xs uppercase tracking-wide text-slate-400">{info.company_name || "X-admin"} · {info.serial}</p>
        {(info.machine || info.technician) && (
          <p className="mt-1 text-sm text-slate-500">
            {info.machine && <>☕ {info.machine}</>}{info.machine && info.technician && " · "}{info.technician && <>🔧 {info.technician}</>}
          </p>
        )}
        {done !== null || info.rated ? (
          <div className="mt-6">
            <div className="text-4xl">{done !== null && done <= 2 ? "🙏" : "🎉"}</div>
            <p className="mt-3 text-lg font-semibold">{info.rated && done === null ? L.already : done !== null && done <= 2 ? L.thanksLow : L.thanks}</p>
          </div>
        ) : (
          <>
            <h1 className="mt-5 text-xl font-semibold">{L.title}</h1>
            <p className="mt-1 text-sm text-slate-500">{L.sub}</p>
            <div className="mt-5 flex justify-center gap-2" onMouseLeave={() => setHover(0)}>
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n} type="button" onClick={() => setStars(n)} onMouseEnter={() => setHover(n)}
                  aria-label={L.labels[n - 1]}
                  className={`text-4xl transition-transform hover:scale-110 ${(hover || stars) >= n ? "text-amber-400" : "text-slate-300"}`}
                >★</button>
              ))}
            </div>
            <div className="mt-1 h-5 text-sm text-slate-500">{(hover || stars) > 0 ? L.labels[(hover || stars) - 1] : ""}</div>
            <textarea
              value={comment} onChange={(e) => setComment(e.target.value)} rows={3} maxLength={1000}
              placeholder={L.comment} className="mt-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
            <button
              type="button" disabled={!stars || busy} onClick={send}
              className="mt-4 w-full rounded-xl bg-indigo-600 py-3 text-base font-semibold text-white disabled:opacity-40"
            >{L.send}</button>
          </>
        )}
      </div>
    </main>
  );
}
