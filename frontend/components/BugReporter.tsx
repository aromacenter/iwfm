"use client";

/** Lebegő hibabejelentő (🐞): minden oldalon elérhető, 3 mezős űrlap —
 * leírás, súlyosság, képernyőkép (fájl vagy beillesztés). Az oldal-URL és a
 * böngésző magától rögzül. A "Bejelentéseim" fülön zárul a kör: a javított
 * (resolved) hibát a tesztelő megerősíti vagy újranyitja. */

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface MyBug {
  id: string;
  description: string;
  severity: string;
  status: string;
  page_url: string;
  created_at: string;
  resolution_note: string | null;
}

const SEVERITY_CHIP: Record<string, string> = {
  blocker: "bg-rose-100 text-rose-800",
  major: "bg-amber-100 text-amber-800",
  minor: "bg-sky-100 text-sky-800",
  cosmetic: "bg-slate-100 text-slate-600",
};

const STATUS_CHIP: Record<string, string> = {
  new: "bg-slate-100 text-slate-700",
  confirmed: "bg-indigo-100 text-indigo-800",
  duplicate: "bg-slate-100 text-slate-500",
  rejected: "bg-slate-100 text-slate-500 line-through",
  resolved: "bg-emerald-100 text-emerald-800",
  closed: "bg-emerald-50 text-emerald-600",
  reopened: "bg-rose-100 text-rose-800",
};

export default function BugReporter() {
  const { t } = useT();
  const { toast } = useUI();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"report" | "mine">("report");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState("minor");
  const [screenshot, setScreenshot] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mine, setMine] = useState<MyBug[]>([]);

  const loadMine = useCallback(() => {
    api.get<MyBug[]>("/api/bugs/mine").then(setMine).catch(() => {});
  }, []);
  useEffect(loadMine, [loadMine]);

  // van-e újratesztelésre váró (resolved) bejelentésem? → pötty a gombon
  const needsRetest = mine.some((b) => b.status === "resolved");

  function pickFile(file: File | null) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setScreenshot(String(reader.result));
    reader.readAsDataURL(file);
  }

  // képernyőkép beillesztése vágólapról (Ctrl+V a panelen)
  function onPaste(e: React.ClipboardEvent) {
    const item = Array.from(e.clipboardData.items).find((i) =>
      i.type.startsWith("image/"),
    );
    if (item) {
      const f = item.getAsFile();
      if (f) pickFile(f);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/bugs", {
        description,
        severity,
        page_url: window.location.href,
        user_agent: navigator.userAgent.slice(0, 250),
        screenshot,
      });
      toast(t("bugs.sent"), "success");
      setDescription("");
      setSeverity("minor");
      setScreenshot(null);
      loadMine();
      setTab("mine");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function retest(id: string, ok: boolean) {
    try {
      await api.post(`/api/bugs/${id}/${ok ? "retest-ok" : "reopen"}`, {});
      toast(t(ok ? "bugs.closedToast" : "bugs.reopenedToast"), "success");
      loadMine();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  return (
    <>
      <button
        onClick={() => { setOpen(!open); if (!open) loadMine(); }}
        title={t("bugs.buttonTitle")}
        className="fixed bottom-5 left-5 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-rose-600 text-xl text-white shadow-lg hover:bg-rose-700"
      >
        🐞
        {needsRetest && (
          <span className="absolute -right-0.5 -top-0.5 h-3.5 w-3.5 rounded-full border-2 border-white bg-amber-400" />
        )}
      </button>

      {open && (
        <div
          onPaste={onPaste}
          className="fixed bottom-20 left-5 z-40 w-[340px] max-w-[calc(100vw-40px)] rounded-2xl border border-slate-200 bg-white p-4 shadow-2xl"
        >
          <div className="mb-3 flex rounded-lg border border-slate-200 p-0.5 text-sm font-medium">
            <button
              onClick={() => setTab("report")}
              className={`flex-1 rounded-md px-2 py-1 ${tab === "report" ? "bg-rose-600 text-white" : "text-slate-600"}`}
            >
              {t("bugs.tabReport")}
            </button>
            <button
              onClick={() => setTab("mine")}
              className={`flex-1 rounded-md px-2 py-1 ${tab === "mine" ? "bg-rose-600 text-white" : "text-slate-600"}`}
            >
              {t("bugs.tabMine")} {needsRetest && "⏳"}
            </button>
          </div>

          {tab === "report" ? (
            <form onSubmit={submit} className="space-y-2.5">
              <textarea
                required
                minLength={5}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("bugs.descriptionPh")}
                rows={4}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              />
              <select
                value={severity}
                onChange={(e) => setSeverity(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              >
                <option value="blocker">🛑 {t("bugs.sev.blocker")}</option>
                <option value="major">🟠 {t("bugs.sev.major")}</option>
                <option value="minor">🔵 {t("bugs.sev.minor")}</option>
                <option value="cosmetic">⚪ {t("bugs.sev.cosmetic")}</option>
              </select>
              <div className="flex items-center gap-2 text-sm">
                <label className="flex-1 cursor-pointer rounded-lg border border-dashed border-slate-300 px-3 py-2 text-center text-slate-500 hover:border-rose-400">
                  {screenshot ? t("bugs.screenshotOk") : t("bugs.screenshotAdd")}
                  <input
                    type="file"
                    accept="image/png,image/jpeg"
                    className="hidden"
                    onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
                  />
                </label>
                {screenshot && (
                  <button type="button" onClick={() => setScreenshot(null)} className="text-slate-400 hover:text-rose-600">✕</button>
                )}
              </div>
              <p className="text-[11px] text-slate-400">{t("bugs.autoNote")}</p>
              <button
                disabled={busy}
                className="w-full rounded-lg bg-rose-600 px-3 py-2 text-sm font-semibold text-white hover:bg-rose-700 disabled:opacity-50"
              >
                {busy ? t("common.saving") : t("bugs.send")}
              </button>
            </form>
          ) : (
            <div className="max-h-[360px] space-y-2 overflow-y-auto">
              {mine.length === 0 && (
                <p className="py-6 text-center text-sm text-slate-400">{t("bugs.mineEmpty")}</p>
              )}
              {mine.map((b) => (
                <div key={b.id} className="rounded-xl border border-slate-200 p-2.5 text-sm">
                  <div className="mb-1 flex flex-wrap gap-1">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${SEVERITY_CHIP[b.severity]}`}>
                      {t(`bugs.sev.${b.severity}`)}
                    </span>
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_CHIP[b.status]}`}>
                      {t(`bugs.status.${b.status}`)}
                    </span>
                  </div>
                  <p className="line-clamp-2 text-slate-700">{b.description}</p>
                  {b.resolution_note && (
                    <p className="mt-1 text-xs text-emerald-700">💬 {b.resolution_note}</p>
                  )}
                  {b.status === "resolved" && (
                    <div className="mt-2 flex gap-2">
                      <button
                        onClick={() => retest(b.id, true)}
                        className="flex-1 rounded-lg bg-emerald-600 px-2 py-1 text-xs font-semibold text-white hover:bg-emerald-700"
                      >
                        ✔️ {t("bugs.retestOk")}
                      </button>
                      <button
                        onClick={() => retest(b.id, false)}
                        className="flex-1 rounded-lg bg-rose-600 px-2 py-1 text-xs font-semibold text-white hover:bg-rose-700"
                      >
                        ↩️ {t("bugs.reopen")}
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
