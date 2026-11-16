"use client";

/** Levélküldés (158–159. kör): egyedi e-mail egy címre vagy minden aktív
 *  partnernek, akinél e-mail cím van rögzítve. Sima szöveg vagy formázott
 *  (HTML) levél egyszerű eszköztárral, csatolmányokkal; a cég SMTP-jével,
 *  címzettenként külön levélben; alul a kampány-napló. Csak admin. */

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface Recipients {
  count: number;
  sample: { name: string; email: string }[];
  smtp_ready: boolean;
  sender: string | null;
}

interface Campaign {
  id: string;
  subject: string;
  mode: "single" | "partners";
  to_address: string | null;
  recipient_count: number;
  sent_count: number;
  failed_count: number;
  failed: string[];
  status: "running" | "done";
  is_html: boolean;
  attachment_names: string[];
  created_by_name: string;
  created_at: string;
  finished_at: string | null;
}

interface Attachment {
  name: string;
  mime: string;
  size: number;
  data: string; // base64 data URL
}

const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";
const MAX_FILES = 5;
const MAX_TOTAL = 8 * 1024 * 1024;

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

const fmtSize = (n: number) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} kB`);

export default function LevelekPage() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const [rec, setRec] = useState<Recipients | null>(null);
  const [showList, setShowList] = useState(false);
  const [mode, setMode] = useState<"single" | "partners">("single");
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [html, setHtml] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [camps, setCamps] = useState<Campaign[]>([]);
  const editorRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api.get<Recipients>("/api/mailing/recipients").then(setRec).catch(() => {});
    api.get<Campaign[]>("/api/mailing/campaigns").then(setCamps).catch(() => {});
  }, []);
  useEffect(load, [load]);

  // futó kampány: pár másodpercenként frissül a napló
  useEffect(() => {
    if (!camps.some((c) => c.status === "running")) return;
    const id = setInterval(() => api.get<Campaign[]>("/api/mailing/campaigns").then(setCamps).catch(() => {}), 4000);
    return () => clearInterval(id);
  }, [camps]);

  // formázott módra váltáskor a sima szöveg bekezdésekké alakul (és vissza)
  function switchHtml(next: boolean) {
    if (next === html) return;
    if (next) {
      const paragraphs = body.split(/\n{2,}/).map((p) => `<p>${p.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/\n/g, "<br>")}</p>`).join("");
      setBody(paragraphs);
      setTimeout(() => { if (editorRef.current) editorRef.current.innerHTML = paragraphs; }, 0);
    } else {
      const text = editorRef.current?.innerText ?? body.replace(/<[^>]+>/g, "");
      setBody(text.trim());
    }
    setHtml(next);
  }

  function exec(cmd: string, value?: string) {
    editorRef.current?.focus();
    document.execCommand(cmd, false, value);
    setBody(editorRef.current?.innerHTML ?? "");
  }

  function addLink() {
    const url = window.prompt(t("mail.tbLinkPrompt"), "https://");
    if (url && /^https?:\/\//i.test(url)) exec("createLink", url);
  }

  async function addFiles(files: FileList | null) {
    if (!files) return;
    const next = [...attachments];
    for (const f of Array.from(files)) {
      if (next.length >= MAX_FILES) { toast(t("mail.attachTooMany"), "error"); break; }
      if (next.reduce((a, x) => a + x.size, 0) + f.size > MAX_TOTAL) { toast(t("mail.attachTooBig"), "error"); break; }
      next.push({ name: f.name, mime: f.type || "application/octet-stream", size: f.size, data: await readFile(f) });
    }
    setAttachments(next);
    if (fileRef.current) fileRef.current.value = "";
  }

  const bodyFilled = () => (html ? (editorRef.current?.innerText ?? "").trim().length > 0 : body.trim().length > 0);
  const payloadBody = () => (html ? (editorRef.current?.innerHTML ?? body) : body);
  const payloadAttachments = () => attachments.map((a) => ({ name: a.name, mime: a.mime, data: a.data }));

  async function sendTest() {
    if (!subject.trim() || !bodyFilled()) { toast(t("mail.fillFirst"), "error"); return; }
    setBusy(true);
    try {
      const r = await api.post<{ to: string }>("/api/mailing/test", { mode: "single", subject, body: payloadBody(), html, attachments: payloadAttachments() });
      toast(t("mail.testSent", { to: r.to }));
    } catch (e) { toast(errorMessage(e), "error"); } finally { setBusy(false); }
  }

  async function send() {
    if (!subject.trim() || !bodyFilled()) { toast(t("mail.fillFirst"), "error"); return; }
    if (mode === "single" && !to.includes("@")) { toast(t("mail.badAddress"), "error"); return; }
    const n = mode === "single" ? to.split(/[;,]/).filter((x) => x.includes("@")).length : (rec?.count ?? 0);
    if (!(await confirm(t("mail.confirm", { n, subject })))) return;
    setBusy(true);
    try {
      const c = await api.post<Campaign>("/api/mailing/send", { mode, to: mode === "single" ? to : null, subject, body: payloadBody(), html, attachments: payloadAttachments() });
      toast(c.status === "done" ? t("mail.sentNow", { n: c.sent_count }) : t("mail.queued", { n: c.recipient_count }));
      setSubject(""); setBody(""); setAttachments([]); if (editorRef.current) editorRef.current.innerHTML = ""; if (mode === "single") setTo("");
      load();
    } catch (e) { toast(errorMessage(e), "error"); } finally { setBusy(false); }
  }

  const tb = "rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100";

  return (
    <AppShell>
      <h1 className="mb-1 text-xl font-bold">✉️ {t("mail.title")}</h1>
      <p className="mb-5 text-sm text-slate-500">{t("mail.hint")}</p>

      {rec && !rec.smtp_ready && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{t("mail.noSmtp")}</div>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => setMode("single")} className={`rounded-lg px-3 py-1.5 text-sm font-medium ${mode === "single" ? "bg-indigo-600 text-white" : "border border-slate-300 text-slate-700"}`}>
              {t("mail.modeSingle")}
            </button>
            <button type="button" onClick={() => setMode("partners")} className={`rounded-lg px-3 py-1.5 text-sm font-medium ${mode === "partners" ? "bg-indigo-600 text-white" : "border border-slate-300 text-slate-700"}`}>
              {t("mail.modePartners", { n: rec?.count ?? 0 })}
            </button>
          </div>
          {mode === "single" ? (
            <label className="block text-sm">
              {t("mail.to")}
              <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="nev@example.com" className={inputCls} />
              <span className="mt-1 block text-xs text-slate-400">{t("mail.toHint")}</span>
            </label>
          ) : (
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
              {t("mail.partnersInfo", { n: rec?.count ?? 0 })}
              <button type="button" onClick={() => setShowList((v) => !v)} className="ml-2 text-indigo-600 hover:underline">
                {showList ? t("mail.hideList") : t("mail.showList")}
              </button>
              {showList && (
                <ul className="mt-2 max-h-48 overflow-y-auto text-xs text-slate-600">
                  {(rec?.sample ?? []).map((r) => <li key={r.email}>{r.name} · {r.email}</li>)}
                  {(rec?.count ?? 0) > (rec?.sample.length ?? 0) && <li className="text-slate-400">… +{(rec?.count ?? 0) - (rec?.sample.length ?? 0)}</li>}
                </ul>
              )}
            </div>
          )}
          <label className="mt-3 block text-sm">
            {t("mail.subject")}
            <input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} className={inputCls} />
          </label>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <span>{t("mail.body")}</span>
            <span className="ml-auto inline-flex overflow-hidden rounded-lg border border-slate-300 text-xs">
              <button type="button" onClick={() => switchHtml(false)} className={`px-2 py-1 ${!html ? "bg-indigo-600 text-white" : "text-slate-600"}`}>{t("mail.fmtPlain")}</button>
              <button type="button" onClick={() => switchHtml(true)} className={`px-2 py-1 ${html ? "bg-indigo-600 text-white" : "text-slate-600"}`}>{t("mail.fmtHtml")}</button>
            </span>
          </div>
          {html ? (
            <div className="mt-1">
              <div className="flex flex-wrap gap-1 rounded-t-lg border border-b-0 border-slate-300 bg-slate-50 p-1.5">
                <button type="button" className={tb} title={t("mail.tbBold")} onMouseDown={(e) => { e.preventDefault(); exec("bold"); }}><b>B</b></button>
                <button type="button" className={tb} title={t("mail.tbItalic")} onMouseDown={(e) => { e.preventDefault(); exec("italic"); }}><i>I</i></button>
                <button type="button" className={tb} title={t("mail.tbUnderline")} onMouseDown={(e) => { e.preventDefault(); exec("underline"); }}><u>U</u></button>
                <button type="button" className={tb} title={t("mail.tbHeading")} onMouseDown={(e) => { e.preventDefault(); exec("formatBlock", "<h2>"); }}>H</button>
                <button type="button" className={tb} title={t("mail.tbList")} onMouseDown={(e) => { e.preventDefault(); exec("insertUnorderedList"); }}>• —</button>
                <button type="button" className={tb} title={t("mail.tbNumbered")} onMouseDown={(e) => { e.preventDefault(); exec("insertOrderedList"); }}>1. —</button>
                <button type="button" className={tb} title={t("mail.tbLink")} onMouseDown={(e) => { e.preventDefault(); addLink(); }}>🔗</button>
                <button type="button" className={tb} title={t("mail.tbClear")} onMouseDown={(e) => { e.preventDefault(); exec("removeFormat"); exec("formatBlock", "<p>"); }}>✕</button>
              </div>
              <div
                ref={editorRef}
                contentEditable
                suppressContentEditableWarning
                onInput={() => setBody(editorRef.current?.innerHTML ?? "")}
                className="prose prose-sm min-h-[260px] max-w-none rounded-b-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-200"
              />
              <span className="mt-1 block text-xs text-slate-400">{t("mail.htmlHint")}</span>
            </div>
          ) : (
            <>
              <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={12} maxLength={20000} className={inputCls} />
              <span className="mt-1 block text-xs text-slate-400">{t("mail.bodyHint")}</span>
            </>
          )}

          <div className="mt-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span>{t("mail.attachments")}</span>
              <button type="button" onClick={() => fileRef.current?.click()} className="rounded-lg border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50">
                📎 {t("mail.addFile")}
              </button>
              <input ref={fileRef} type="file" multiple className="hidden" onChange={(e) => void addFiles(e.target.files)} />
              <span className="text-xs text-slate-400">{t("mail.attachHint")}</span>
            </div>
            {attachments.length > 0 && (
              <ul className="mt-2 space-y-1">
                {attachments.map((a, i) => (
                  <li key={`${a.name}-${i}`} className="flex items-center gap-2 rounded-lg bg-slate-50 px-2 py-1 text-xs">
                    <span className="font-medium">{a.name}</span>
                    <span className="text-slate-400">{fmtSize(a.size)}</span>
                    <button type="button" onClick={() => setAttachments(attachments.filter((_, j) => j !== i))} className="ml-auto text-rose-600 hover:underline">{t("mail.remove")}</button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button type="button" disabled={busy || !rec?.smtp_ready} onClick={send} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
              {mode === "single" ? t("mail.send") : t("mail.sendAll", { n: rec?.count ?? 0 })}
            </button>
            <button type="button" disabled={busy || !rec?.smtp_ready} onClick={sendTest} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
              {t("mail.sendTest")}
            </button>
            {rec?.sender && <span className="text-xs text-slate-400">{t("mail.sender", { from: rec.sender })}</span>}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-base font-semibold">{t("mail.history")}</h2>
          {camps.length === 0 && <p className="text-sm text-slate-400">{t("mail.noHistory")}</p>}
          <ul className="space-y-2">
            {camps.map((c) => (
              <li key={c.id} className="rounded-lg border border-slate-100 p-2 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{c.subject}{c.is_html ? " ✨" : ""}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs ${c.status === "done" ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"}`}>
                    {c.status === "done" ? t("mail.done") : t("mail.running")}
                  </span>
                </div>
                <div className="text-xs text-slate-500">
                  {c.created_at.slice(0, 16).replace("T", " ")} · {c.created_by_name} · {c.mode === "single" ? c.to_address : t("mail.allPartners")}
                </div>
                <div className="text-xs text-slate-600">
                  {t("mail.stats", { total: c.recipient_count, sent: c.sent_count, failed: c.failed_count })}
                  {c.attachment_names.length > 0 && <> · {t("mail.withAttachments", { names: c.attachment_names.join(", ") })}</>}
                </div>
                {c.failed.length > 0 && <div className="text-xs text-rose-600">{t("mail.failedList")}: {c.failed.join(", ")}</div>}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </AppShell>
  );
}
