"use client";

/** Saját aláírás (152. kör, x-presso 7ca9c6f6): a felhasználó elmentett
 * aláírása — a munkalapokon egy kattintással beszúrható, és az általa
 * kiküldött hivatalos árajánlat-PDF-re is ez kerül. Rajzolható ujjal/egérrel,
 * vagy feltölthető PNG-ként. Ugyanazt a mintát kezeli, mint a munkalap
 * „mentés mintaként” gombja (/api/auth/me/signature). */

import { useEffect, useState } from "react";
import SignatureCanvas from "@/components/SignatureCanvas";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

const MAX_CHARS = 290_000; // a szerver 300 000 karakteres data URL-t fogad el

/** Tetszőleges kép → átméretezett PNG data URL (az aláírás-minta formátuma). */
function fileToPng(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("read"));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("image"));
      img.onload = () => {
        const scale = Math.min(1, 600 / img.width, 240 / img.height);
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(img.width * scale));
        canvas.height = Math.max(1, Math.round(img.height * scale));
        const ctx = canvas.getContext("2d");
        if (!ctx) return reject(new Error("canvas"));
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/png"));
      };
      img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
  });
}

export default function MySignatureDialog({ onClose }: { onClose: () => void }) {
  const { t } = useT();
  const { toast } = useUI();
  const [current, setCurrent] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api.get<{ signature: string | null }>("/api/auth/me/signature")
      .then((r) => { if (active) setCurrent(r.signature); })
      .catch(() => {})
      .finally(() => { if (active) setLoaded(true); });
    return () => { active = false; };
  }, []);

  async function save(value: string | null) {
    if (value && value.length > MAX_CHARS) {
      toast(t("mySignature.tooLarge"), "error");
      return;
    }
    setBusy(true);
    try {
      await api.put("/api/auth/me/signature", { signature: value });
      setCurrent(value);
      setDraft(null);
      toast(value ? t("mySignature.saved") : t("mySignature.removed"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      await save(await fileToPng(file));
    } catch {
      toast(t("mySignature.badFile"), "error");
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">
      <div className="relative max-h-[90vh] w-full max-w-md space-y-3 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
        <button type="button" onClick={onClose} aria-label={t("common.close")} className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">
          ✕
        </button>
        <h2 className="text-lg font-semibold">{t("mySignature.title")}</h2>
        <p className="text-sm text-slate-500">{t("mySignature.hint")}</p>

        <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
          <p className="text-xs font-semibold uppercase text-slate-500">{t("mySignature.current")}</p>
          {!loaded ? (
            <p className="mt-2 text-sm text-slate-400">…</p>
          ) : current ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={current} alt={t("mySignature.title")} style={{ backgroundColor: "#ffffff" }} className="mt-2 h-20 w-full rounded-lg border border-slate-200 object-contain" />
              <button type="button" disabled={busy} onClick={() => void save(null)} className="mt-2 rounded-lg border border-rose-200 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-50 disabled:opacity-50">
                {t("mySignature.remove")}
              </button>
            </>
          ) : (
            <p className="mt-2 text-sm text-amber-700">{t("mySignature.none")}</p>
          )}
        </div>

        <SignatureCanvas label={t("mySignature.draw")} onChange={setDraft} />
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={busy || !draft} onClick={() => void save(draft)} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            {t("mySignature.saveDrawn")}
          </button>
          <label className="cursor-pointer rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
            {t("mySignature.upload")}
            <input type="file" accept="image/png,image/jpeg" className="hidden" onChange={(e) => void onFile(e)} />
          </label>
        </div>
      </div>
    </div>
  );
}
