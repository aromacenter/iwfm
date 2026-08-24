"use client";

/** Átvétel: az ügyfél behozott gépének rögzítése (tartozékok, hibák, márka…)
 *  + nyomtatható átvételi elismervény (AT-ÉÉÉÉ-NNNN). A dátum automatikusan a
 *  rögzítés időpontja. Új (nálunk még nem járt, ügyfél-tulajdonú) gép helyben
 *  felvehető, opcionális kávérendelős QR-címkével. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import SearchSelect from "@/components/SearchSelect";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface AssetOption {
  id: string;
  barcode: string;
  name: string;
  partner_name: string | null;
  manufacturer: string | null;
  category: string | null;
  article_number: string | null;
  serial_number: string | null;
}

interface Intake {
  id: string;
  serial: string;
  asset_name: string | null;
  asset_manufacturer: string | null;
  asset_serial: string | null;
  asset_barcode: string | null;
  partner_name: string | null;
  client_name: string | null;
  client_phone: string | null;
  accessories: string | null;
  faults: string | null;
  note: string | null;
  received_by_name: string | null;
  received_at: string;
}

const EMPTY_FORM = {
  asset_id: "",
  client_name: "",
  client_phone: "",
  accessories: "",
  faults: "",
  note: "",
};

export default function AtvetelPage() {
  const { t } = useT();
  const { toast, confirm } = useUI();
  const canDelete = usePerms().can("delete");

  const [intakes, setIntakes] = useState<Intake[]>([]);
  const [assets, setAssets] = useState<AssetOption[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Új (ügyfél-tulajdonú) gép felvétele helyben
  const [newAssetMode, setNewAssetMode] = useState(false);
  const [newAsset, setNewAsset] = useState({ name: "", manufacturer: "", serial_number: "" });

  const load = useCallback(() => {
    api.get<Intake[]>("/api/intakes").then(setIntakes).catch(() => {});
    api.get<AssetOption[]>("/api/assets").then(setAssets).catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let assetId = form.asset_id;
      let createdAsset: { id: string; barcode: string } | null = null;
      if (newAssetMode) {
        if (!newAsset.name.trim()) {
          setError(t("intake.newAssetNameRequired"));
          setBusy(false);
          return;
        }
        const { barcode } = await api.get<{ barcode: string }>("/api/assets/generate-barcode");
        createdAsset = await api.post<{ id: string; barcode: string }>("/api/assets", {
          barcode,
          name: newAsset.name.trim(),
          manufacturer: newAsset.manufacturer.trim() || null,
          serial_number: newAsset.serial_number.trim() || null,
          customer_owned: true,
        });
        assetId = createdAsset.id;
      }
      if (!assetId) {
        setError(t("intake.assetRequired"));
        setBusy(false);
        return;
      }
      const created = await api.post<Intake>("/api/intakes", {
        asset_id: assetId,
        client_name: form.client_name || null,
        client_phone: form.client_phone || null,
        accessories: form.accessories || null,
        faults: form.faults || null,
        note: form.note || null,
      });
      setShowForm(false);
      setForm(EMPTY_FORM);
      setNewAssetMode(false);
      setNewAsset({ name: "", manufacturer: "", serial_number: "" });
      toast(t("intake.created", { serial: created.serial }), "success");
      load();
      // elismervény azonnali nyomtatása
      if (await confirm(t("intake.printConfirm", { serial: created.serial }))) {
        try {
          await downloadFile(`/api/intakes/${created.id}/pdf`, `${created.serial}.pdf`);
        } catch (err) {
          toast(errorMessage(err), "error");
        }
      }
      // új gépnél kávérendelős QR-címke felkínálása
      if (createdAsset && (await confirm(t("tasks.newAssetQrConfirm", { barcode: createdAsset.barcode })))) {
        try {
          await downloadFile(`/api/assets/${createdAsset.id}/qr-label`, `QR-${createdAsset.barcode}.pdf`);
        } catch (err) {
          toast(errorMessage(err), "error");
        }
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function removeIntake(row: Intake) {
    if (!(await confirm(t("intake.deleteConfirm", { serial: row.serial })))) return;
    try {
      await api.delete(`/api/intakes/${row.id}`);
      toast(t("intake.deletedOk"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">📥 {t("intake.title")}</h1>
          <p className="text-sm text-slate-500">{t("intake.subtitle")}</p>
        </div>
        <button
          onClick={() => setShowForm(true)}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t("intake.new")}
        </button>
      </div>

      <div className="space-y-3">
        {intakes.length === 0 && (
          <p className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500">
            {t("intake.empty")}
          </p>
        )}
        {intakes.map((row) => (
          <div key={row.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-semibold">
                  {row.serial}
                  <span className="ml-2 font-normal text-slate-600">
                    {row.asset_name}
                    {row.asset_manufacturer ? ` — ${row.asset_manufacturer}` : ""}
                  </span>
                </p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {new Date(row.received_at).toLocaleString("hu-HU")}
                  {row.client_name ? ` · ${row.client_name}` : row.partner_name ? ` · ${row.partner_name}` : ""}
                  {row.asset_serial ? ` · ${t("myTasks.assetSerial")}: ${row.asset_serial}` : ""}
                  {row.received_by_name ? ` · ${t("intake.receivedBy")}: ${row.received_by_name}` : ""}
                </p>
                {row.faults && (
                  <p className="mt-1 text-sm text-slate-600">⚠️ {row.faults}</p>
                )}
                {row.accessories && (
                  <p className="mt-0.5 text-xs text-slate-500">🧰 {row.accessories}</p>
                )}
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  onClick={async () => {
                    try {
                      await downloadFile(`/api/intakes/${row.id}/pdf`, `${row.serial}.pdf`);
                    } catch (err) {
                      toast(errorMessage(err), "error");
                    }
                  }}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100"
                >
                  🖨 {t("intake.print")}
                </button>
                {canDelete && (
                  <button
                    onClick={() => removeIntake(row)}
                    className="rounded-lg border border-rose-200 px-3 py-1.5 text-sm text-rose-600 hover:bg-rose-50"
                  >
                    {t("common.delete")}
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {showForm && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setShowForm(false); }} className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form onSubmit={submit} className="my-8 w-full max-w-md space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">📥 {t("intake.newTitle")}</h2>
            <p className="text-xs text-slate-500">{t("intake.newHint")}</p>

            <div className="block text-sm">
              {t("intake.machine")}
              {!newAssetMode && (
                <SearchSelect
                  items={assets.map((a) => ({
                    id: a.id,
                    label: a.manufacturer ? `${a.name} — ${a.manufacturer}` : a.name,
                    sublabel: [a.category, a.partner_name].filter(Boolean).join(" · ") || null,
                    badge: a.barcode,
                    keywords: [a.article_number, a.serial_number].filter(Boolean).join(" ") || null,
                  }))}
                  value={form.asset_id}
                  onChange={(id) => setForm({ ...form, asset_id: id })}
                  placeholder={t("service.machineSearchPh")}
                  className="mt-1 w-full"
                />
              )}
              <button
                type="button"
                onClick={() => { setNewAssetMode(!newAssetMode); setForm({ ...form, asset_id: "" }); }}
                className="mt-1 block text-sm font-medium text-indigo-600 hover:text-indigo-800"
              >
                {newAssetMode ? t("tasks.newAssetBack") : t("tasks.newAssetToggle")}
              </button>
              {newAssetMode && (
                <div className="mt-2 space-y-2 rounded-xl border border-sky-200 bg-sky-50 p-3">
                  <p className="text-xs text-sky-800">{t("tasks.newAssetHint")}</p>
                  <input
                    value={newAsset.name}
                    onChange={(e) => setNewAsset({ ...newAsset, name: e.target.value })}
                    placeholder={t("tasks.newAssetName")}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2"
                  />
                  <div className="flex gap-2">
                    <input
                      value={newAsset.manufacturer}
                      onChange={(e) => setNewAsset({ ...newAsset, manufacturer: e.target.value })}
                      placeholder={t("tasks.newAssetManufacturer")}
                      className="w-1/2 rounded-lg border border-slate-300 px-3 py-2"
                    />
                    <input
                      value={newAsset.serial_number}
                      onChange={(e) => setNewAsset({ ...newAsset, serial_number: e.target.value })}
                      placeholder={t("tasks.newAssetSerial")}
                      className="w-1/2 rounded-lg border border-slate-300 px-3 py-2"
                    />
                  </div>
                </div>
              )}
            </div>

            <div className="flex gap-2">
              <label className="block flex-1 text-sm">
                {t("intake.clientName")}
                <input
                  value={form.client_name}
                  onChange={(e) => setForm({ ...form, client_name: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              <label className="block w-40 text-sm">
                {t("intake.clientPhone")}
                <input
                  value={form.client_phone}
                  onChange={(e) => setForm({ ...form, client_phone: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            </div>

            <label className="block text-sm">
              {t("intake.accessories")}
              <textarea
                value={form.accessories}
                onChange={(e) => setForm({ ...form, accessories: e.target.value })}
                rows={2}
                placeholder={t("intake.accessoriesPh")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>

            <label className="block text-sm">
              {t("intake.faults")}
              <textarea
                value={form.faults}
                onChange={(e) => setForm({ ...form, faults: e.target.value })}
                rows={3}
                placeholder={t("intake.faultsPh")}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>

            <label className="block text-sm">
              {t("intake.note")}
              <textarea
                value={form.note}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
                rows={2}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>

            <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
              {t("intake.clauseInfo")}
            </p>

            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={() => setShowForm(false)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                💾 {t("intake.save")}
              </button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
