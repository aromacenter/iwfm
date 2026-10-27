"use client";

/** Beüzemelő varázsló: friss telepítésnél az admin ezen megy végig —
 * cégadatok + logó, első telephely, kollégák meghívása. Minden lépés
 * kihagyható; a meglévő beállítás-végpontokat használja. */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import OnboardingGuide, { ManagerFigure, useTypewriter } from "@/components/OnboardingGuide";
import { api, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";

interface WsSettings {
  company_name: string | null;
  company_address: string | null;
  footer_text: string | null;
  customer_footer_text: string | null;
  intake_footer_text: string | null;
  survey_fee: number | null;
  accent_color: string;
  show_materials: boolean;
  show_hours: boolean;
  show_client_signature: boolean;
  show_comments: boolean;
  has_logo: boolean;
}

interface InvitedRow {
  name: string;
  email: string;
  password: string | null;
}

export default function OnboardingPage() {
  const { t } = useT();
  const { toast } = useUI();
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  // 1. lépés: cégadatok
  const [ws, setWs] = useState<WsSettings | null>(null);
  const [company, setCompany] = useState({ name: "", address: "", accent: "#1e40af" });
  const [logo, setLogo] = useState<string | null>(null);
  useEffect(() => {
    api.get<WsSettings>("/api/settings/worksheet")
      .then((s) => {
        setWs(s);
        setCompany({
          name: s.company_name ?? "",
          address: s.company_address ?? "",
          accent: s.accent_color,
        });
      })
      .catch(() => {});
  }, []);

  function pickLogo(file: File | null) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setLogo(String(reader.result));
    reader.readAsDataURL(file);
  }

  async function saveCompany() {
    if (!ws) return;
    setBusy(true);
    try {
      await api.put("/api/settings/worksheet", {
        company_name: company.name || null,
        company_address: company.address || null,
        footer_text: ws.footer_text,
        customer_footer_text: ws.customer_footer_text,
        intake_footer_text: ws.intake_footer_text,
        survey_fee: ws.survey_fee,
        accent_color: company.accent,
        show_materials: ws.show_materials,
        show_hours: ws.show_hours,
        show_client_signature: ws.show_client_signature,
        show_comments: ws.show_comments,
        logo: logo,
      });
      setStep(1);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  // 2. lépés: első telephely
  const [siteName, setSiteName] = useState("");
  async function saveSite() {
    if (!siteName.trim()) { setStep(2); return; }
    setBusy(true);
    try {
      await api.post("/api/warehouses", { name: siteName.trim(), kind: "site" });
      setStep(2);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  // 3. lépés: kollégák
  const [emp, setEmp] = useState({ last: "", first: "", email: "", role: "employee" });
  const [invited, setInvited] = useState<InvitedRow[]>([]);
  async function addColleague() {
    if (!emp.last.trim() || !emp.first.trim() || !emp.email.trim()) return;
    setBusy(true);
    try {
      const res = await api.post<{ generated_password?: string }>("/api/employees", {
        email: emp.email.trim(),
        last_name: emp.last.trim(),
        first_name: emp.first.trim(),
        role: emp.role,
        hire_date: new Date().toISOString().slice(0, 10),
      });
      setInvited((rows) => [
        ...rows,
        { name: `${emp.last.trim()} ${emp.first.trim()}`, email: emp.email.trim(),
          password: res.generated_password ?? null },
      ]);
      setEmp({ last: "", first: "", email: "", role: "employee" });
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  // 4. lépés: Billingó-számlázás (ha a modul él ezen a példányon)
  const [bill, setBill] = useState({ api_key: "", block_id: "" });
  const [billAvailable, setBillAvailable] = useState(true);
  const [billSaved, setBillSaved] = useState(false);
  useEffect(() => {
    api.get<{ has_api_key: boolean }>("/api/settings/billingo")
      .then((b) => setBillSaved(b.has_api_key))
      .catch(() => setBillAvailable(false));
  }, []);
  async function saveBilling() {
    if (!bill.api_key.trim()) { setStep(4); return; }
    setBusy(true);
    try {
      await api.put("/api/settings/billingo", {
        enabled: true, provider: "billingo", api_key: bill.api_key.trim(),
        block_id: bill.block_id ? Number(bill.block_id) : null, test_mode: false,
      });
      try { await api.post("/api/settings/billingo/test"); toast(t("onboard.billingOk"), "success"); }
      catch { toast(t("onboard.billingTestFailed"), "error"); }
      setBillSaved(true);
      setStep(4);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  // 5. lépés: mintaadatok (generált minta-partnerek + gépek, egy kattintással törölhetők)
  const [demoDone, setDemoDone] = useState<{ partners: number; assets: number } | null>(null);
  async function createDemo() {
    setBusy(true);
    try {
      const r = await api.post<{ partners: number; assets: number }>("/api/settings/demo-data");
      setDemoDone(r);
      toast(t("onboard.demoCreated"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  const steps = [t("onboard.step1"), t("onboard.step2"), t("onboard.step3"), t("onboard.stepBilling"), t("onboard.stepDemo"), t("onboard.step4")];

  // Animált bevezető: a menedzser-figura köszön, elmondja, mi jön, aztán
  // lépcsőzetesen beúszik a varázsló. Munkamenetenként egyszer (ujratöltésnél nem).
  const [intro, setIntro] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    try { return sessionStorage.getItem("iwfm-onb-intro") !== "done"; } catch { return true; }
  });
  const [introPhase, setIntroPhase] = useState(0); // 0 = köszönés, 1 = info
  const introText = introPhase === 0 ? t("onboard.intro1") : t("onboard.intro2");
  const introTyped = useTypewriter(intro ? introText : "", 20);
  useEffect(() => {
    if (!intro || !introTyped.done || introPhase !== 0) return;
    const id = window.setTimeout(() => setIntroPhase(1), 900);
    return () => window.clearTimeout(id);
  }, [intro, introTyped.done, introPhase]);
  function finishIntro() {
    try { sessionStorage.setItem("iwfm-onb-intro", "done"); } catch {}
    setIntro(false);
  }
  const stepTips = [t("onboard.tip1"), t("onboard.tip2"), t("onboard.tip3"), t("onboard.tipBilling"), t("onboard.tipDemo"), t("onboard.tipDone")];

  if (intro) {
    return (
      <AppShell>
        <div className="mx-auto flex min-h-[70vh] max-w-2xl flex-col items-center justify-center text-center">
          <div className="flex flex-col items-center gap-2 sm:flex-row sm:items-end sm:gap-4">
            <ManagerFigure size={210} wave />
            <div className="onb-bubble relative mb-10 max-w-md rounded-2xl border border-indigo-100 bg-white px-5 py-4 text-left text-base text-slate-700 shadow-lg">
              <span className="absolute -left-2 bottom-8 hidden h-4 w-4 rotate-45 border-b border-l border-indigo-100 bg-white sm:block" />
              <p className="min-h-[3.5rem]">
                {introTyped.shown}
                {!introTyped.done && <span className="onb-caret">▍</span>}
              </p>
            </div>
          </div>
          <div className="onb-rise mt-4 flex flex-wrap items-center justify-center gap-3" style={{ "--d": "300ms" } as React.CSSProperties}>
            <button
              type="button"
              onClick={finishIntro}
              className="rounded-xl bg-indigo-600 px-6 py-3 text-sm font-semibold text-white shadow-md hover:bg-indigo-700"
            >
              {t("onboard.introStart")}
            </button>
            <button type="button" onClick={finishIntro} className="text-sm font-medium text-slate-500 hover:text-slate-700">
              {t("onboard.introSkip")}
            </button>
          </div>
          <ol className="onb-rise mt-8 flex flex-wrap justify-center gap-2" style={{ "--d": "600ms" } as React.CSSProperties}>
            {steps.map((label, i) => (
              <li key={label} className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-500">{i + 1}. {label}</li>
            ))}
          </ol>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-2xl">
        <div className="onb-rise flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold">{t("onboard.title")}</h1>
            <p className="mt-1 text-sm text-slate-500">{t("onboard.subtitle")}</p>
          </div>
        </div>

        {/* lépés-jelző */}
        <ol className="onb-rise mt-6 flex gap-2" style={{ "--d": "120ms" } as React.CSSProperties}>
          {steps.map((label, i) => (
            <li key={label} className={`flex-1 rounded-full px-3 py-1.5 text-center text-xs font-semibold ${
              i === step ? "bg-indigo-600 text-white"
              : i < step ? "bg-emerald-100 text-emerald-700"
              : "bg-slate-100 text-slate-400"
            }`}>
              {i < step ? "✓ " : `${i + 1}. `}{label}
            </li>
          ))}
        </ol>

        {/* a menedzser a varázsló mellett, lépésenként más tippel */}
        <div className="onb-rise mt-4" style={{ "--d": "220ms" } as React.CSSProperties}>
          <OnboardingGuide key={step} text={stepTips[step] ?? ""} size={84} />
        </div>

        <div className="onb-rise mt-2 space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm" style={{ "--d": "320ms" } as React.CSSProperties}>
          {step === 0 && (
            <>
              <h2 className="font-semibold">{t("onboard.companyTitle")}</h2>
              <p className="text-sm text-slate-500">{t("onboard.companyHint")}</p>
              <label className="block text-sm">
                {t("onboard.companyName")} *
                <input value={company.name} onChange={(e) => setCompany({ ...company, name: e.target.value })} className={inputCls} />
              </label>
              <label className="block text-sm">
                {t("onboard.companyAddress")}
                <input value={company.address} onChange={(e) => setCompany({ ...company, address: e.target.value })} className={inputCls} />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-sm">
                  {t("onboard.logo")} {ws?.has_logo && !logo && <span className="text-emerald-600">✓</span>}
                  <input type="file" accept="image/png,image/jpeg" onChange={(e) => pickLogo(e.target.files?.[0] ?? null)} className={inputCls} />
                </label>
                <label className="block text-sm">
                  {t("onboard.accent")}
                  <input type="color" value={company.accent} onChange={(e) => setCompany({ ...company, accent: e.target.value })} className="mt-1 h-10 w-full cursor-pointer rounded-lg border border-slate-300" />
                </label>
              </div>
              <div className="flex justify-between pt-2">
                <button type="button" onClick={() => setStep(1)} className="text-sm font-medium text-slate-500 hover:text-slate-700">{t("onboard.skip")}</button>
                <button type="button" onClick={saveCompany} disabled={busy || !company.name.trim()} className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                  {t("onboard.next")}
                </button>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <h2 className="font-semibold">{t("onboard.siteTitle")}</h2>
              <p className="text-sm text-slate-500">{t("onboard.siteHint")}</p>
              <label className="block text-sm">
                {t("onboard.siteName")}
                <input value={siteName} onChange={(e) => setSiteName(e.target.value)} placeholder={t("onboard.sitePh")} className={inputCls} />
              </label>
              <div className="flex justify-between pt-2">
                <button type="button" onClick={() => setStep(2)} className="text-sm font-medium text-slate-500 hover:text-slate-700">{t("onboard.skip")}</button>
                <button type="button" onClick={saveSite} disabled={busy} className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                  {t("onboard.next")}
                </button>
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <h2 className="font-semibold">{t("onboard.teamTitle")}</h2>
              <p className="text-sm text-slate-500">{t("onboard.teamHint")}</p>
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-sm">
                  {t("onboard.lastName")}
                  <input value={emp.last} onChange={(e) => setEmp({ ...emp, last: e.target.value })} className={inputCls} />
                </label>
                <label className="block text-sm">
                  {t("onboard.firstName")}
                  <input value={emp.first} onChange={(e) => setEmp({ ...emp, first: e.target.value })} className={inputCls} />
                </label>
                <label className="block text-sm">
                  {t("onboard.email")}
                  <input type="email" value={emp.email} onChange={(e) => setEmp({ ...emp, email: e.target.value })} className={inputCls} />
                </label>
                <label className="block text-sm">
                  {t("onboard.role")}
                  <select value={emp.role} onChange={(e) => setEmp({ ...emp, role: e.target.value })} className={inputCls}>
                    <option value="employee">{t("onboard.roleEmployee")}</option>
                    <option value="manager">{t("onboard.roleManager")}</option>
                    <option value="admin">{t("onboard.roleAdmin")}</option>
                  </select>
                </label>
              </div>
              <button type="button" onClick={addColleague} disabled={busy || !emp.email.trim()} className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">
                + {t("onboard.add")}
              </button>
              {invited.length > 0 && (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm">
                  <p className="mb-2 font-medium text-emerald-800">{t("onboard.passwordsOnce")}</p>
                  <ul className="space-y-1">
                    {invited.map((r) => (
                      <li key={r.email} className="flex flex-wrap justify-between gap-2">
                        <span>{r.name} · {r.email}</span>
                        {r.password && <code className="rounded bg-white px-2 font-mono">{r.password}</code>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="flex justify-between pt-2">
                <button type="button" onClick={() => setStep(3)} className="text-sm font-medium text-slate-500 hover:text-slate-700">{t("onboard.skip")}</button>
                <button type="button" onClick={() => setStep(3)} className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700">
                  {t("onboard.next")}
                </button>
              </div>
            </>
          )}

          {step === 3 && (
            <>
              <h2 className="font-semibold">{t("onboard.billingTitle")}</h2>
              <p className="text-sm text-slate-500">{t("onboard.billingHint")}</p>
              {!billAvailable ? (
                <p className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">{t("onboard.billingUnavailable")}</p>
              ) : billSaved ? (
                <p className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">✓ {t("onboard.billingAlready")}</p>
              ) : (
                <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
                  <label className="block text-sm">
                    {t("onboard.billingKey")}
                    <input type="password" autoComplete="off" value={bill.api_key} onChange={(e) => setBill({ ...bill, api_key: e.target.value })} className={inputCls} />
                  </label>
                  <label className="block text-sm">
                    {t("onboard.billingBlock")}
                    <input type="number" min={1} value={bill.block_id} onChange={(e) => setBill({ ...bill, block_id: e.target.value })} className={inputCls} />
                  </label>
                </div>
              )}
              <div className="flex justify-between pt-2">
                <button type="button" onClick={() => setStep(4)} className="text-sm font-medium text-slate-500 hover:text-slate-700">{t("onboard.skip")}</button>
                <button type="button" onClick={billSaved || !billAvailable ? () => setStep(4) : saveBilling} disabled={busy} className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                  {t("onboard.next")}
                </button>
              </div>
            </>
          )}

          {step === 4 && (
            <>
              <h2 className="font-semibold">{t("onboard.demoTitle")}</h2>
              <p className="text-sm text-slate-500">{t("onboard.demoHint")}</p>
              {demoDone ? (
                <p className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
                  ✓ {t("onboard.demoDone", { partners: demoDone.partners, assets: demoDone.assets })}
                </p>
              ) : (
                <button type="button" onClick={createDemo} disabled={busy} className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">
                  🧪 {t("onboard.demoCreate")}
                </button>
              )}
              <p className="text-xs text-slate-400">{t("onboard.demoRemoveHint")}</p>
              <div className="flex justify-between pt-2">
                <button type="button" onClick={() => setStep(5)} className="text-sm font-medium text-slate-500 hover:text-slate-700">{t("onboard.skip")}</button>
                <button type="button" onClick={() => setStep(5)} className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700">
                  {t("onboard.next")}
                </button>
              </div>
            </>
          )}

          {step === 5 && (
            <>
              <h2 className="font-semibold">🎉 {t("onboard.doneTitle")}</h2>
              <p className="text-sm text-slate-500">{t("onboard.doneHint")}</p>
              <ul className="list-inside list-disc space-y-1 text-sm text-slate-600">
                <li>{t("onboard.doneTip1")}</li>
                <li>{t("onboard.doneTip2")}</li>
                <li>{t("onboard.doneTip3")}</li>
              </ul>
              <div className="flex justify-end pt-2">
                <button type="button" onClick={() => router.replace("/naptar")} className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700">
                  {t("onboard.finish")}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}
