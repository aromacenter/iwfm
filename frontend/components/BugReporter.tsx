"use client";

/** Lebegő hibabejelentő (🐞): minden oldalon elérhető, 3 mezős űrlap —
 * leírás, súlyosság, képernyőkép (fájl vagy beillesztés). Az oldal-URL és a
 * böngésző magától rögzül. A "Bejelentéseim" fülön zárul a kör: a javított
 * (resolved) hibát a tesztelő megerősíti vagy újranyitja. */

import { useCallback, useEffect, useRef, useState } from "react";
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

  // ─── újranyitás indoklással: mi nem stimmel még + friss kép ───
  const [reopeningId, setReopeningId] = useState<string | null>(null);
  const [reopenNote, setReopenNote] = useState("");
  const [reopenShot, setReopenShot] = useState<string | null>(null);
  // hová kerüljön az elkészült/annotált kép: az új bejelentésbe vagy az
  // újranyitó űrlapba
  const annotateTarget = useRef<"report" | "reopen">("report");

  // ─── kép-annotálás: nyilak rajzolása a képernyőképre ───
  const [annotating, setAnnotating] = useState(false);
  const [baseImg, setBaseImg] = useState<string | null>(null);
  const [arrows, setArrows] = useState<{ x1: number; y1: number; x2: number; y2: number }[]>([]);
  const [draft, setDraft] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [capturing, setCapturing] = useState(false);

  function openAnnotator(dataUrl: string) {
    const img = new Image();
    img.onload = () => {
      imgRef.current = img;
      setBaseImg(dataUrl);
      setArrows([]);
      setDraft(null);
      setAnnotating(true);
    };
    img.src = dataUrl;
  }

  function pickFile(file: File | null) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => openAnnotator(String(reader.result));
    reader.readAsDataURL(file);
  }

  // az aktuális oldal lefotózása (a bejelentő-panel kimarad a képből)
  async function capturePage(target: "report" | "reopen" = "report") {
    annotateTarget.current = target;
    setCapturing(true);
    try {
      const { toJpeg } = await import("html-to-image");
      const dataUrl = await toJpeg(document.body, {
        quality: 0.85,
        pixelRatio: 1,
        filter: (node) =>
          !(node instanceof HTMLElement && node.dataset?.bugUi === "1"),
      });
      openAnnotator(dataUrl);
    } catch {
      toast(t("bugs.captureFailed"), "error");
    } finally {
      setCapturing(false);
    }
  }

  function drawArrow(
    ctx: CanvasRenderingContext2D,
    a: { x1: number; y1: number; x2: number; y2: number },
    width: number,
  ) {
    ctx.strokeStyle = "#ef4444";
    ctx.fillStyle = "#ef4444";
    ctx.lineWidth = width;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(a.x1, a.y1);
    ctx.lineTo(a.x2, a.y2);
    ctx.stroke();
    const angle = Math.atan2(a.y2 - a.y1, a.x2 - a.x1);
    const head = width * 4;
    ctx.beginPath();
    ctx.moveTo(a.x2, a.y2);
    ctx.lineTo(a.x2 - head * Math.cos(angle - 0.45), a.y2 - head * Math.sin(angle - 0.45));
    ctx.lineTo(a.x2 - head * Math.cos(angle + 0.45), a.y2 - head * Math.sin(angle + 0.45));
    ctx.closePath();
    ctx.fill();
  }

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, 0, 0);
    const w = Math.max(4, Math.round(canvas.width / 250));
    arrows.forEach((a) => drawArrow(ctx, a, w));
    if (draft) drawArrow(ctx, draft, w);
  }, [arrows, draft]);

  useEffect(() => {
    if (annotating) redraw();
  }, [annotating, redraw]);

  function canvasPoint(e: React.PointerEvent): { x: number; y: number } {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * canvas.width,
      y: ((e.clientY - rect.top) / rect.height) * canvas.height,
    };
  }

  function onPointerDown(e: React.PointerEvent) {
    e.preventDefault();
    const p = canvasPoint(e);
    setDraft({ x1: p.x, y1: p.y, x2: p.x, y2: p.y });
  }
  function onPointerMove(e: React.PointerEvent) {
    if (!draft) return;
    const p = canvasPoint(e);
    setDraft({ ...draft, x2: p.x, y2: p.y });
  }
  function onPointerUp() {
    if (!draft) return;
    const len = Math.hypot(draft.x2 - draft.x1, draft.y2 - draft.y1);
    if (len > 15) setArrows((a) => [...a, draft]);
    setDraft(null);
  }

  function finishAnnotation() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dataUrl = canvas.toDataURL("image/jpeg", 0.8);
    if (annotateTarget.current === "reopen") setReopenShot(dataUrl);
    else setScreenshot(dataUrl);
    setAnnotating(false);
    setBaseImg(null);
  }

  // képernyőkép beillesztése vágólapról (Ctrl+V a panelen) — nyitott
  // újranyitó űrlapnál oda kerül a kép, egyébként az új bejelentésbe
  function onPaste(e: React.ClipboardEvent) {
    const item = Array.from(e.clipboardData.items).find((i) =>
      i.type.startsWith("image/"),
    );
    if (item) {
      const f = item.getAsFile();
      if (f) {
        annotateTarget.current = reopeningId ? "reopen" : "report";
        pickFile(f);
      }
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

  async function retestOk(id: string) {
    try {
      await api.post(`/api/bugs/${id}/retest-ok`, {});
      toast(t("bugs.closedToast"), "success");
      loadMine();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function submitReopen(id: string) {
    try {
      await api.post(`/api/bugs/${id}/reopen`, {
        note: reopenNote.trim() || null,
        screenshot: reopenShot,
      });
      toast(t("bugs.reopenedToast"), "success");
      setReopeningId(null);
      setReopenNote("");
      setReopenShot(null);
      loadMine();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  return (
    <>
      <button
        data-bug-ui="1"
        onClick={() => { setOpen(!open); if (!open) loadMine(); }}
        title={t("bugs.buttonTitle")}
        className="fixed bottom-24 right-5 z-[60] flex h-14 w-14 items-center justify-center rounded-full bg-rose-600 text-white shadow-lg hover:bg-rose-700"
      >
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m8 2 1.88 1.88" />
          <path d="M14.12 3.88 16 2" />
          <path d="M9 7.13v-1a3.003 3.003 0 1 1 6 0v1" />
          <path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6" />
          <path d="M12 20v-9" />
          <path d="M6.53 9C4.6 8.8 3 7.1 3 5" />
          <path d="M6 13H2" />
          <path d="M3 21c0-2.1 1.7-3.9 3.8-4" />
          <path d="M20.97 5c0 2.1-1.6 3.8-3.5 4" />
          <path d="M22 13h-4" />
          <path d="M17.2 17c2.1.1 3.8 1.9 3.8 4" />
        </svg>
        {needsRetest && (
          <span className="absolute -right-0.5 -top-0.5 h-3.5 w-3.5 rounded-full border-2 border-white bg-amber-400" />
        )}
      </button>

      {open && (
        <div
          data-bug-ui="1"
          onPaste={onPaste}
          className="fixed bottom-40 right-5 z-[60] w-[340px] max-w-[calc(100vw-40px)] rounded-2xl border border-slate-200 bg-white p-4 shadow-2xl"
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
              <button
                type="button"
                onClick={() => capturePage("report")}
                disabled={capturing}
                className="w-full rounded-lg border border-rose-300 px-3 py-2 text-sm font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50"
              >
                {capturing ? t("bugs.capturing") : `📸 ${t("bugs.capturePage")}`}
              </button>
              <div className="flex items-center gap-2 text-sm">
                <label className="flex-1 cursor-pointer rounded-lg border border-dashed border-slate-300 px-3 py-2 text-center text-slate-500 hover:border-rose-400">
                  {screenshot ? t("bugs.screenshotOk") : t("bugs.screenshotAdd")}
                  <input
                    type="file"
                    accept="image/png,image/jpeg"
                    className="hidden"
                    onChange={(e) => {
                      annotateTarget.current = "report";
                      pickFile(e.target.files?.[0] ?? null);
                    }}
                  />
                </label>
                {screenshot && (
                  <>
                    <button type="button" title={t("bugs.editArrows")} onClick={() => { annotateTarget.current = "report"; openAnnotator(screenshot); }} className="text-slate-400 hover:text-rose-600">✏️</button>
                    <button type="button" onClick={() => setScreenshot(null)} className="text-slate-400 hover:text-rose-600">✕</button>
                  </>
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
                  {b.status === "resolved" && reopeningId !== b.id && (
                    <div className="mt-2 flex gap-2">
                      <button
                        onClick={() => retestOk(b.id)}
                        className="flex-1 rounded-lg bg-emerald-600 px-2 py-1 text-xs font-semibold text-white hover:bg-emerald-700"
                      >
                        ✔️ {t("bugs.retestOk")}
                      </button>
                      <button
                        onClick={() => { setReopeningId(b.id); setReopenNote(""); setReopenShot(null); }}
                        className="flex-1 rounded-lg bg-rose-600 px-2 py-1 text-xs font-semibold text-white hover:bg-rose-700"
                      >
                        ↩️ {t("bugs.reopen")}
                      </button>
                    </div>
                  )}
                  {b.status === "resolved" && reopeningId === b.id && (
                    <div className="mt-2 space-y-1.5 rounded-lg border border-rose-200 bg-rose-50 p-2">
                      <textarea
                        value={reopenNote}
                        onChange={(e) => setReopenNote(e.target.value)}
                        placeholder={t("bugs.reopenNotePh")}
                        rows={3}
                        className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
                      />
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => capturePage("reopen")}
                          disabled={capturing}
                          className="flex-1 rounded-lg border border-rose-300 bg-white px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50"
                        >
                          {capturing ? t("bugs.capturing") : `📸 ${reopenShot ? t("bugs.screenshotOk") : t("bugs.capturePage")}`}
                        </button>
                        <label className="cursor-pointer rounded-lg border border-dashed border-slate-300 bg-white px-2 py-1 text-xs text-slate-500 hover:border-rose-400">
                          📎
                          <input
                            type="file"
                            accept="image/png,image/jpeg"
                            className="hidden"
                            onChange={(e) => {
                              annotateTarget.current = "reopen";
                              pickFile(e.target.files?.[0] ?? null);
                            }}
                          />
                        </label>
                        {reopenShot && (
                          <button type="button" onClick={() => setReopenShot(null)} className="text-slate-400 hover:text-rose-600">✕</button>
                        )}
                      </div>
                      <div className="flex gap-1.5">
                        <button
                          onClick={() => submitReopen(b.id)}
                          className="flex-1 rounded-lg bg-rose-600 px-2 py-1 text-xs font-semibold text-white hover:bg-rose-700"
                        >
                          ↩️ {t("bugs.reopenSend")}
                        </button>
                        <button
                          onClick={() => setReopeningId(null)}
                          className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-100"
                        >
                          {t("common.cancel")}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Nyilazó: húzással piros nyilak a képernyőképre */}
      {annotating && baseImg && (
        <div data-bug-ui="1" className="fixed inset-0 z-[70] flex flex-col bg-black/90">
          <p className="shrink-0 py-1.5 text-center text-sm font-medium text-white">
            {t("bugs.annotateHint")}
          </p>
          {/* akkora előnézet, amekkora csak lehet: teljes szélesség, görgethető magasság */}
          <div className="w-full flex-1 overflow-auto">
            <canvas
              ref={canvasRef}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerLeave={onPointerUp}
              className="cursor-crosshair"
              style={{
                touchAction: "none",
                width: "100%",
                height: "auto",
                display: "block",
              }}
            />
          </div>
          <div className="flex shrink-0 justify-center gap-2 py-2">
            <button
              onClick={() => setArrows((a) => a.slice(0, -1))}
              disabled={arrows.length === 0}
              className="rounded-lg bg-white/15 px-4 py-2 text-sm font-medium text-white hover:bg-white/25 disabled:opacity-40"
            >
              ↩️ {t("bugs.undoArrow")}
            </button>
            <button
              onClick={() => { setAnnotating(false); setBaseImg(null); }}
              className="rounded-lg bg-white/15 px-4 py-2 text-sm font-medium text-white hover:bg-white/25"
            >
              {t("common.cancel")}
            </button>
            <button
              onClick={finishAnnotation}
              className="rounded-lg bg-rose-600 px-5 py-2 text-sm font-semibold text-white hover:bg-rose-700"
            >
              ✓ {t("bugs.annotateDone")}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
