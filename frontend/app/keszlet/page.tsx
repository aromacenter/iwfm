"use client";

/** Készlet-nyilvántartás: eszközök (gépek) egyedi vonalkóddal, partner-
 * társítás (kihelyezés/visszavétel), vonalkód-olvasó és mozgástörténet. */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Partner {
  id: string;
  name: string;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  address: string | null;
  notes: string | null;
  is_active: boolean;
  asset_count: number;
}

interface Movement {
  id: string;
  action: string;
  partner_name: string | null;
  detail: string | null;
  created_at: string;
}

interface Asset {
  id: string;
  barcode: string;
  name: string;
  category: string | null;
  model: string | null;
  serial_number: string | null;
  status: "in_stock" | "deployed" | "maintenance" | "retired";
  partner_id: string | null;
  partner_name: string | null;
  deployed_at: string | null;
  notes: string | null;
  movements?: Movement[];
}

const STATUS_COLORS: Record<string, string> = {
  in_stock: "bg-slate-100 text-slate-700",
  deployed: "bg-emerald-100 text-emerald-800",
  maintenance: "bg-amber-100 text-amber-800",
  retired: "bg-red-100 text-red-700",
};

const EMPTY_ASSET = { id: "", barcode: "", name: "", category: "", model: "", serial_number: "", notes: "", status: "in_stock" };

export default function KeszletPage() {
  const { t, lang } = useT();
  const [assets, setAssets] = useState<Asset[]>([]);
  const [partners, setPartners] = useState<Partner[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [partnerFilter, setPartnerFilter] = useState("");
  const [scanMsg, setScanMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const [assetForm, setAssetForm] = useState<typeof EMPTY_ASSET | null>(null);
  const [deployFor, setDeployFor] = useState<Asset | null>(null);
  const [deploy, setDeploy] = useState({ partner_id: "", note: "" });
  const [historyFor, setHistoryFor] = useState<Asset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scanRef = useRef<HTMLInputElement>(null);

  const fmt = (dt: string) =>
    new Date(dt).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "short", timeStyle: "short" });

  const loadAssets = useCallback(() => {
    const params = new URLSearchParams();
    if (search) params.set("q", search);
    if (statusFilter) params.set("status", statusFilter);
    if (partnerFilter) params.set("partner_id", partnerFilter);
    api.get<Asset[]>(`/api/assets?${params}`).then(setAssets).catch(() => {});
  }, [search, statusFilter, partnerFilter]);

  const loadPartners = useCallback(() => {
    api.get<Partner[]>("/api/partners").then(setPartners).catch(() => {});
  }, []);

  useEffect(loadPartners, [loadPartners]);
  useEffect(loadAssets, [loadAssets]);

  // Vonalkód-olvasó: a scanner beírja a kódot + Enter → eszköz kiemelése/keresése.
  async function onScan(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    const code = scanRef.current?.value.trim();
    if (!code) return;
    e.preventDefault();
    try {
      const a = await api.get<Asset>(`/api/assets/by-barcode/${encodeURIComponent(code)}`);
      setScanMsg({ text: t("inv.scannedFound", { name: a.name, barcode: a.barcode }), ok: true });
      setSearch(code);
    } catch (err) {
      if (err instanceof ApiError && err.code === "asset.barcode_not_found") {
        setScanMsg({ text: t("inv.scannedMissing", { barcode: code }), ok: false });
        // ismeretlen kód → új eszköz felvétele előtöltött vonalkóddal
        setAssetForm({ ...EMPTY_ASSET, barcode: code });
      } else {
        setScanMsg({ text: errorMessage(err), ok: false });
      }
    }
    if (scanRef.current) scanRef.current.value = "";
    setTimeout(() => setScanMsg(null), 5000);
  }

  async function generateBarcode() {
    try {
      const res = await api.get<{ barcode: string }>("/api/assets/generate-barcode");
      setAssetForm((f) => (f ? { ...f, barcode: res.barcode } : f));
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  async function saveAsset(e: React.FormEvent) {
    e.preventDefault();
    if (!assetForm) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        barcode: assetForm.barcode,
        name: assetForm.name,
        category: assetForm.category || null,
        model: assetForm.model || null,
        serial_number: assetForm.serial_number || null,
        notes: assetForm.notes || null,
      };
      if (assetForm.id) await api.patch(`/api/assets/${assetForm.id}`, body);
      else await api.post("/api/assets", body);
      setAssetForm(null);
      loadAssets();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function changeStatus(asset: Asset, status: string) {
    try {
      await api.patch(`/api/assets/${asset.id}`, { status });
      loadAssets();
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  async function doDeploy(e: React.FormEvent) {
    e.preventDefault();
    if (!deployFor || !deploy.partner_id) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/assets/${deployFor.id}/deploy`, {
        partner_id: deploy.partner_id,
        note: deploy.note || null,
      });
      setDeployFor(null);
      setDeploy({ partner_id: "", note: "" });
      loadAssets();
      loadPartners();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function returnAsset(asset: Asset) {
    try {
      await api.post(`/api/assets/${asset.id}/return`, {});
      loadAssets();
      loadPartners();
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  async function openHistory(asset: Asset) {
    try {
      const full = await api.get<Asset>(`/api/assets/${asset.id}`);
      setHistoryFor(full);
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("inv.title")}</h1>
        <input
          ref={scanRef}
          onKeyDown={onScan}
          placeholder={t("inv.scanHint")}
          className="rounded-lg border-2 border-indigo-300 bg-indigo-50 px-3 py-2 text-sm placeholder:text-indigo-400"
          autoFocus
        />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("inv.search")}
          className="min-w-48 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
        />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">{t("inv.allStatuses")}</option>
          {(["in_stock", "deployed", "maintenance", "retired"] as const).map((s) => (
            <option key={s} value={s}>{t(`inv.statuses.${s}`)}</option>
          ))}
        </select>
        <select value={partnerFilter} onChange={(e) => setPartnerFilter(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
          <option value="">{t("inv.allPartners")}</option>
          {partners.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <Link href="/partnerek" className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm hover:bg-slate-100">
          {t("inv.partners")}
        </Link>
        <button onClick={() => { setError(null); setAssetForm({ ...EMPTY_ASSET }); }} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
          {t("inv.newAsset")}
        </button>
      </div>

      {scanMsg && (
        <p className={`mb-3 rounded-lg px-3 py-2 text-sm ${scanMsg.ok ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
          {scanMsg.text}
        </p>
      )}

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">{t("inv.barcode")}</th>
              <th className="px-4 py-3">{t("inv.name")}</th>
              <th className="px-4 py-3">{t("inv.category")}</th>
              <th className="px-4 py-3">{t("inv.serial")}</th>
              <th className="px-4 py-3">{t("inv.status")}</th>
              <th className="px-4 py-3">{t("inv.partner")}</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {assets.map((a) => (
              <tr key={a.id} className="border-b border-slate-100 last:border-0">
                <td className="px-4 py-3 font-mono text-xs font-semibold text-indigo-700">{a.barcode}</td>
                <td className="px-4 py-3">
                  <div className="font-medium">{a.name}</div>
                  {a.model && <div className="text-xs text-slate-400">{a.model}</div>}
                </td>
                <td className="px-4 py-3 text-slate-500">{a.category ?? "—"}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-500">{a.serial_number ?? "—"}</td>
                <td className="px-4 py-3">
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[a.status]}`}>
                    {t(`inv.statuses.${a.status}`)}
                  </span>
                </td>
                <td className="px-4 py-3">{a.partner_name ?? <span className="text-slate-300">—</span>}</td>
                <td className="px-4 py-3 text-right">
                  <div className="flex justify-end gap-2">
                    {a.status === "deployed" ? (
                      <button onClick={() => returnAsset(a)} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">
                        {t("inv.returnBtn")}
                      </button>
                    ) : a.status !== "retired" ? (
                      <button onClick={() => { setError(null); setDeploy({ partner_id: "", note: "" }); setDeployFor(a); }} className="rounded bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700">
                        {t("inv.deploy")}
                      </button>
                    ) : null}
                    <button onClick={() => openHistory(a)} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">
                      {t("inv.history")}
                    </button>
                    <button onClick={() => { setError(null); setAssetForm({ id: a.id, barcode: a.barcode, name: a.name, category: a.category ?? "", model: a.model ?? "", serial_number: a.serial_number ?? "", notes: a.notes ?? "", status: a.status }); }} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">
                      {t("common.edit")}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {assets.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-slate-400">{t("inv.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Eszköz űrlap */}
      {assetForm && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form onSubmit={saveAsset} className="my-8 w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{assetForm.id ? t("inv.editAssetTitle") : t("inv.newAssetTitle")}</h2>
            <label className="block text-sm">
              {t("inv.barcode")} *
              <div className="mt-1 flex gap-2">
                <input required value={assetForm.barcode} onChange={(e) => setAssetForm({ ...assetForm, barcode: e.target.value })} className="flex-1 rounded-lg border border-slate-300 px-3 py-2 font-mono" />
                {!assetForm.id && (
                  <button type="button" onClick={generateBarcode} className="rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100">
                    {t("inv.generate")}
                  </button>
                )}
              </div>
            </label>
            <label className="block text-sm">
              {t("inv.name")} *
              <input required value={assetForm.name} onChange={(e) => setAssetForm({ ...assetForm, name: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                {t("inv.category")}
                <input value={assetForm.category} onChange={(e) => setAssetForm({ ...assetForm, category: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
              <label className="block text-sm">
                {t("inv.model")}
                <input value={assetForm.model} onChange={(e) => setAssetForm({ ...assetForm, model: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            <label className="block text-sm">
              {t("inv.serial")}
              <input value={assetForm.serial_number} onChange={(e) => setAssetForm({ ...assetForm, serial_number: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono" />
            </label>
            <label className="block text-sm">
              {t("inv.notes")}
              <textarea value={assetForm.notes} onChange={(e) => setAssetForm({ ...assetForm, notes: e.target.value })} rows={2} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setAssetForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{busy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}

      {/* Kihelyezés */}
      {deployFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <form onSubmit={doDeploy} className="w-full max-w-sm space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("inv.deployTitle")}</h2>
            <p className="text-sm text-slate-500">{deployFor.name} · <span className="font-mono">{deployFor.barcode}</span></p>
            <label className="block text-sm">
              {t("inv.deployTo")}
              <select required value={deploy.partner_id} onChange={(e) => setDeploy({ ...deploy, partner_id: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2">
                <option value="">{t("inv.choosePartner")}</option>
                {partners.filter((p) => p.is_active).map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              {t("inv.deployNote")}
              <input value={deploy.note} onChange={(e) => setDeploy({ ...deploy, note: e.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
            </label>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setDeployFor(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50">{t("inv.deploy")}</button>
            </div>
          </form>
        </div>
      )}

      {/* Mozgástörténet */}
      {historyFor && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <div className="my-8 w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">{t("inv.historyTitle", { name: historyFor.name })}</h2>
            {(historyFor.movements ?? []).length === 0 ? (
              <p className="text-sm text-slate-400">{t("inv.noHistory")}</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {historyFor.movements!.map((m) => (
                  <li key={m.id} className="flex items-start justify-between gap-3 border-b border-slate-100 pb-2">
                    <div>
                      <span className="font-medium">{t(`inv.moves.${m.action}`)}</span>
                      {m.partner_name && <span className="text-slate-500"> → {m.partner_name}</span>}
                      {m.detail && <div className="text-xs text-slate-400">{m.detail}</div>}
                    </div>
                    <span className="whitespace-nowrap text-xs text-slate-400">{fmt(m.created_at)}</span>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex justify-end">
              <button onClick={() => setHistoryFor(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.close")}</button>
            </div>
          </div>
        </div>
      )}

    </AppShell>
  );
}
