"use client";

/** Gépcserék (152. kör, x-presso 56276b1d): az összes eddigi gépcsere egy
 * helyen — kereshető, szűrhető (dátum, átadás módja), a jegyzőkönyv letölthető
 * és elküldhető, a korábbi cseréknél az átadás módja utólag jelölhető. Új csere
 * innen is indítható: a kiválasztott kihelyezett géppel a Gépek oldal
 * csere-ablaka nyílik meg (ott él a teljes csere-logika). */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import CameraScanner, { cameraScanSupported } from "@/components/CameraScanner";
import SearchSelect from "@/components/SearchSelect";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface SwapRow {
  id: string;
  serial: string;
  kind: string;
  asset_barcode: string | null;
  asset_name: string | null;
  asset_serial: string | null;
  replacement_barcode: string | null;
  replacement_name: string | null;
  replacement_serial: string | null;
  partner_name: string | null;
  location: string | null;
  on_site: boolean;
  signer_name: string | null;
  signed_at: string | null;
  created_by_name: string | null;
  created_at: string;
  emailed_at: string | null;
  emailed_to: string | null;
  note: string | null;
  delivery_mode: "personal" | "courier" | null;
  delivery_ref: string | null;
}

interface DeployedAsset {
  id: string;
  barcode: string;
  name: string;
  serial_number: string | null;
  manufacturer: string | null;
  partner_name: string | null;
}

export default function GepcserekPage() {
  const { t, lang } = useT();
  const { toast, prompt } = useUI();
  const router = useRouter();

  const [rows, setRows] = useState<SwapRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [mode, setMode] = useState("");
  const [picker, setPicker] = useState(false);
  const [deployed, setDeployed] = useState<DeployedAsset[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  // 153. kör (56276b1d újranyitás): a telefon kamerájával is kiválasztható a cserélendő gép
  const [cameraOpen, setCameraOpen] = useState(false);
  function onScan(raw: string) {
    setCameraOpen(false);
    // vonalkód vagy a gép QR-címkéjének linkje (a végén a kód / token)
    const code = raw.trim();
    const tail = code.split(/[/?=#]/).filter(Boolean).pop() ?? code;
    const hit = deployed.find((a) => a.barcode === code || a.barcode === tail || code.endsWith(`/${a.barcode}`));
    if (hit) {
      router.push(`/gepek?swap=${encodeURIComponent(hit.id)}`);
    } else {
      toast(t("swaps.scanNotDeployed", { code: tail }), "error");
    }
  }

  const load = useCallback(() => {
    const params = new URLSearchParams({ kind: "swap" });
    if (search.trim()) params.set("q", search.trim());
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (mode) params.set("delivery_mode", mode);
    api.get<SwapRow[]>(`/api/asset-protocols?${params}`)
      .then(setRows)
      .catch((err) => toast(errorMessage(err), "error"))
      .finally(() => setLoading(false));
  }, [search, dateFrom, dateTo, mode, toast]);

  // gépelés közben ne kérdezzük le minden leütésre
  useEffect(() => {
    const id = setTimeout(load, 250);
    return () => clearTimeout(id);
  }, [load]);

  function openPicker() {
    setPicker(true);
    api.get<DeployedAsset[]>("/api/assets?status=deployed")
      .then(setDeployed)
      .catch((err) => toast(errorMessage(err), "error"));
  }

  const pickerItems = useMemo(
    () => deployed.map((a) => ({
      id: a.id,
      label: `${a.name} · ${a.barcode}`,
      sublabel: a.partner_name,
      badge: a.serial_number,
      keywords: [a.manufacturer, a.serial_number, a.partner_name].filter(Boolean).join(" "),
    })),
    [deployed],
  );

  async function setDelivery(row: SwapRow, next: string) {
    let ref: string | null = row.delivery_ref;
    if (next === "courier") {
      const answer = await prompt(t("swaps.courierPrompt"), { type: "text", initial: row.delivery_ref ?? "", placeholder: "GLS · 12345678" });
      if (answer === null) return; // megszakítva
      ref = answer.trim() || null;
    } else {
      ref = null;
    }
    setBusyId(row.id);
    try {
      const updated = await api.patch<SwapRow>(`/api/asset-protocols/${row.id}`, {
        delivery_mode: next || null, delivery_ref: ref,
      });
      setRows((rs) => rs.map((r) => (r.id === row.id ? updated : r)));
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function send(row: SwapRow) {
    setBusyId(row.id);
    try {
      const updated = await api.post<SwapRow>(`/api/asset-protocols/${row.id}/send`, {});
      setRows((rs) => rs.map((r) => (r.id === row.id ? updated : r)));
      toast(t("swaps.sent", { to: updated.emailed_to ?? "" }), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusyId(null);
    }
  }

  const fmt = (iso: string) =>
    new Date(iso).toLocaleString(lang === "hu" ? "hu-HU" : "en-GB", { dateStyle: "medium", timeStyle: "short" });
  const hasFilter = Boolean(search.trim() || dateFrom || dateTo || mode);

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">🔁 {t("swaps.title")}</h1>
          <p className="text-sm text-slate-500">{t("swaps.subtitle")}</p>
        </div>
        <button onClick={openPicker} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
          + {t("swaps.new")}
        </button>
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-3">
        <label className="block min-w-[220px] flex-1 text-xs text-slate-500">
          {t("swaps.search")}
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("swaps.searchPh")} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
        <label className="block text-xs text-slate-500">
          {t("swaps.from")}
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
        <label className="block text-xs text-slate-500">
          {t("swaps.to")}
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm" />
        </label>
        <label className="block text-xs text-slate-500">
          {t("swaps.mode")}
          <select value={mode} onChange={(e) => setMode(e.target.value)} className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm">
            <option value="">{t("swaps.modeAll")}</option>
            <option value="personal">{t("swaps.modePersonal")}</option>
            <option value="courier">{t("swaps.modeCourier")}</option>
            <option value="unset">{t("swaps.modeUnset")}</option>
          </select>
        </label>
        {hasFilter && (
          <button onClick={() => { setSearch(""); setDateFrom(""); setDateTo(""); setMode(""); }} className="rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100">
            {t("swaps.clear")}
          </button>
        )}
      </div>

      <p className="mb-2 text-sm text-slate-500">{t("swaps.count", { count: rows.length })}</p>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
        <table className="w-full min-w-[980px] text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">{t("swaps.colDate")}</th>
              <th className="px-3 py-2">{t("swaps.colSerial")}</th>
              <th className="px-3 py-2">{t("swaps.colPartner")}</th>
              <th className="px-3 py-2">{t("swaps.colOld")}</th>
              <th className="px-3 py-2">{t("swaps.colNew")}</th>
              <th className="px-3 py-2">{t("swaps.mode")}</th>
              <th className="px-3 py-2">{t("swaps.colBy")}</th>
              <th className="px-3 py-2 text-right">{t("swaps.colProtocol")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-slate-100 align-top">
                <td className="whitespace-nowrap px-3 py-2">{fmt(r.created_at)}</td>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{r.serial}</td>
                <td className="px-3 py-2">
                  <div className="font-medium">{r.partner_name ?? "—"}</div>
                  {r.note && <div className="text-xs text-slate-500">{r.note}</div>}
                </td>
                <td className="px-3 py-2">
                  <div>{r.asset_name ?? "—"}</div>
                  <div className="font-mono text-xs text-slate-500">{r.asset_barcode}{r.asset_serial ? ` · ${r.asset_serial}` : ""}</div>
                </td>
                <td className="px-3 py-2">
                  <div>{r.replacement_name ?? "—"}</div>
                  <div className="font-mono text-xs text-slate-500">{r.replacement_barcode}{r.replacement_serial ? ` · ${r.replacement_serial}` : ""}</div>
                </td>
                <td className="px-3 py-2">
                  <select
                    value={r.delivery_mode ?? ""}
                    disabled={busyId === r.id}
                    onChange={(e) => void setDelivery(r, e.target.value)}
                    title={t("swaps.modeEditHint")}
                    className={`rounded-lg border px-2 py-1 text-xs ${r.delivery_mode ? "border-slate-300" : "border-amber-300 bg-amber-50 text-amber-800"}`}
                  >
                    <option value="">{t("swaps.modeUnset")}</option>
                    <option value="personal">🤝 {t("swaps.modePersonal")}</option>
                    <option value="courier">🚚 {t("swaps.modeCourier")}</option>
                  </select>
                  {r.delivery_ref && <div className="mt-0.5 text-xs text-slate-500">{r.delivery_ref}</div>}
                </td>
                <td className="px-3 py-2">
                  <div>{r.created_by_name ?? "—"}</div>
                  <div className="text-xs text-slate-500">
                    {r.signed_at ? `✍️ ${t("swaps.signedBy", { name: r.signer_name ?? "—" })}` : t("swaps.unsigned")}
                  </div>
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right">
                  <button
                    onClick={() => void downloadFile(`/api/asset-protocols/${r.id}/pdf`, `${r.serial}.pdf`).catch((err) => toast(errorMessage(err), "error"))}
                    title={t("swaps.pdf")}
                    className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                  >
                    📄 PDF
                  </button>{" "}
                  <button
                    onClick={() => void send(r)}
                    disabled={busyId === r.id}
                    title={r.emailed_at ? t("swaps.sentAt", { to: r.emailed_to ?? "", at: fmt(r.emailed_at) }) : t("swaps.send")}
                    className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100 disabled:opacity-50"
                  >
                    {r.emailed_at ? "✉️✓" : "✉️"}
                  </button>
                </td>
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-slate-500">
                  {hasFilter ? t("swaps.emptyFiltered") : t("swaps.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {picker && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">
          <div className="relative w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <button type="button" onClick={() => setPicker(false)} aria-label={t("common.close")} className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">
              ✕
            </button>
            <h2 className="text-lg font-semibold">{t("swaps.new")}</h2>
            <p className="text-sm text-slate-500">{t("swaps.pickHint")}</p>
            <SearchSelect
              items={pickerItems}
              value=""
              onChange={(id) => { if (id) router.push(`/gepek?swap=${encodeURIComponent(id)}`); }}
              placeholder={t("swaps.pickPh")}
            />
            {cameraScanSupported() && (
              <button type="button" onClick={() => setCameraOpen(true)} className="w-full rounded-lg border border-indigo-300 bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-800 hover:bg-indigo-100">
                📷 {t("swaps.scan")}
              </button>
            )}
            {cameraOpen && <CameraScanner onDetect={onScan} onClose={() => setCameraOpen(false)} />}
            {deployed.length === 0 && <p className="text-sm text-slate-400">{t("swaps.pickEmpty")}</p>}
          </div>
        </div>
      )}
    </AppShell>
  );
}
