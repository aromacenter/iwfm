"use client";

/** Multifunkciós import/export: adattípus-választás → Excel/CSV feltöltés →
 *  oszlop-hozzárendelés (melyik oszlop melyik mezőbe) → import, hibariporttal.
 *  Export: teljes lista xlsx-ben. */

import { useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface FieldDef {
  key: string;
  required: boolean;
}

interface EntityDef {
  entity: string;
  fields: FieldDef[];
}

interface PreviewCol {
  index: number;
  letter: string;
  header: string;
}

interface Preview {
  columns: PreviewCol[];
  sample_rows: string[][];
  total_rows: number;
}

interface ImportResult {
  created: number;
  skipped: number;
  errors: { row: number; error: string }[];
  total: number;
}

export default function ImportExportPage() {
  const { t } = useT();
  const { toast } = useUI();
  const [entities, setEntities] = useState<EntityDef[]>([]);
  const [exportables, setExportables] = useState<string[]>([]);
  const [mode, setMode] = useState<"import" | "export">("import");
  const [entity, setEntity] = useState("partners");
  const [fileData, setFileData] = useState<string | null>(null);
  const [fileName, setFileName] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({}); // field → col index (string)
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<{ import: EntityDef[]; export: string[] }>("/api/import-export/entities")
      .then((r) => {
        setEntities(r.import);
        setExportables(r.export);
      })
      .catch(() => {});
  }, []);

  const fields = useMemo(
    () => entities.find((e) => e.entity === entity)?.fields ?? [],
    [entities, entity],
  );

  // Fejléc-egyezés alapú automatikus hozzárendelés
  function autoMap(cols: PreviewCol[], fieldDefs: FieldDef[]) {
    const next: Record<string, string> = {};
    for (const f of fieldDefs) {
      const label = t(`impex.fields.${f.key}`).toLowerCase();
      const hit = cols.find((c) => {
        const h = c.header.toLowerCase().trim();
        return h && (h === label || h === f.key || label.includes(h) || h.includes(label));
      });
      if (hit) next[f.key] = String(hit.index);
    }
    return next;
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setResult(null);
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = () => reject(new Error("read"));
        reader.readAsDataURL(file);
      });
      const p = await api.post<Preview>("/api/import-export/preview", { file: dataUrl });
      setFileData(dataUrl);
      setFileName(file.name);
      setPreview(p);
      setMapping(autoMap(p.columns, fields));
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function runImport() {
    if (!fileData || !preview) return;
    setBusy(true);
    setResult(null);
    try {
      const mappingNum: Record<string, number> = {};
      for (const [k, v] of Object.entries(mapping)) {
        if (v !== "") mappingNum[k] = Number(v);
      }
      const res = await api.post<ImportResult>("/api/import-export/import", {
        entity,
        file: fileData,
        mapping: mappingNum,
        has_header: true,
      });
      setResult(res);
      toast(t("impex.done", { created: res.created, skipped: res.skipped }), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(false);
    }
  }

  async function runExport(target: string) {
    try {
      await downloadFile(`/api/import-export/export/${target}`, `iwfm_${target}.xlsx`);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  function resetFile() {
    setFileData(null);
    setFileName("");
    setPreview(null);
    setMapping({});
    setResult(null);
  }

  const requiredUnmapped = fields.filter((f) => f.required && !mapping[f.key]);

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("impex.title")}</h1>
        <div className="flex overflow-hidden rounded-lg border border-slate-300">
          {(["import", "export"] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setResult(null); }}
              className={`px-4 py-2 text-sm font-medium ${mode === m ? "bg-indigo-600 text-white" : "bg-white text-slate-600 hover:bg-slate-100"}`}
            >
              {t(`impex.${m}`)}
            </button>
          ))}
        </div>
      </div>

      {mode === "export" ? (
        <div className="max-w-lg space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <p className="text-sm text-slate-500">{t("impex.exportHint")}</p>
          {exportables.map((e) => (
            <div key={e} className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3">
              <span className="text-sm font-medium">{t(`impex.entities.${e}`)}</span>
              <button
                onClick={() => runExport(e)}
                className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
              >
                {t("impex.download")}
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-4">
          {/* 1. lépés: mit importálunk */}
          <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <span className="text-sm font-medium">{t("impex.whatToImport")}</span>
            <select
              value={entity}
              onChange={(e) => { setEntity(e.target.value); resetFile(); }}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              {entities.map((en) => (
                <option key={en.entity} value={en.entity}>{t(`impex.entities.${en.entity}`)}</option>
              ))}
            </select>
            <label className="cursor-pointer rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
              {busy ? t("common.loading") : fileName || t("impex.chooseFile")}
              <input
                type="file"
                accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
                onChange={onFile}
                disabled={busy}
                className="hidden"
              />
            </label>
            {preview && (
              <span className="text-sm text-slate-500">
                {t("impex.rowsDetected", { count: preview.total_rows - 1 })}
              </span>
            )}
          </div>

          {/* 2. lépés: oszlop-hozzárendelés */}
          {preview && (
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="mb-1 font-semibold">{t("impex.mappingTitle")}</h2>
              <p className="mb-3 text-xs text-slate-500">{t("impex.mappingHint")}</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {fields.map((f) => (
                  <label key={f.key} className="block text-sm">
                    {t(`impex.fields.${f.key}`)}
                    {f.required && <span className="text-red-500"> *</span>}
                    <select
                      value={mapping[f.key] ?? ""}
                      onChange={(e) => setMapping({ ...mapping, [f.key]: e.target.value })}
                      className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2"
                    >
                      <option value="">{t("impex.skipColumn")}</option>
                      {preview.columns.map((c) => (
                        <option key={c.index} value={String(c.index)}>
                          {c.letter}{c.header ? ` — ${c.header}` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>

              {/* minta */}
              <h3 className="mb-1 mt-4 text-sm font-semibold text-slate-600">{t("impex.sample")}</h3>
              <div className="overflow-x-auto rounded-xl border border-slate-200">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-200 bg-slate-50 text-left text-slate-500">
                      {preview.columns.map((c) => (
                        <th key={c.index} className="whitespace-nowrap px-3 py-2">
                          {c.letter}{c.header ? ` — ${c.header}` : ""}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample_rows.map((row, i) => (
                      <tr key={i} className="border-b border-slate-100 last:border-0">
                        {preview.columns.map((c) => (
                          <td key={c.index} className="whitespace-nowrap px-3 py-1.5">{row[c.index] ?? ""}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="mt-4 flex flex-wrap items-center gap-3">
                <button
                  onClick={runImport}
                  disabled={busy || requiredUnmapped.length > 0}
                  className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  {busy ? t("common.saving") : t("impex.runImport")}
                </button>
                {requiredUnmapped.length > 0 && (
                  <span className="text-sm text-amber-700">
                    {t("impex.mapRequired", {
                      fields: requiredUnmapped.map((f) => t(`impex.fields.${f.key}`)).join(", "),
                    })}
                  </span>
                )}
                <button onClick={resetFile} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">
                  {t("impex.otherFile")}
                </button>
              </div>
            </div>
          )}

          {/* 3. lépés: eredmény */}
          {result && (
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="mb-2 font-semibold">{t("impex.resultTitle")}</h2>
              <div className="flex flex-wrap gap-4 text-sm">
                <span className="rounded-lg bg-emerald-50 px-3 py-1.5 font-medium text-emerald-700">
                  {t("impex.created", { count: result.created })}
                </span>
                <span className="rounded-lg bg-slate-100 px-3 py-1.5 text-slate-600">
                  {t("impex.skipped", { count: result.skipped })}
                </span>
                <span className={`rounded-lg px-3 py-1.5 ${result.errors.length ? "bg-red-50 font-medium text-red-700" : "bg-slate-100 text-slate-600"}`}>
                  {t("impex.failed", { count: result.errors.length })}
                </span>
              </div>
              {result.errors.length > 0 && (
                <ul className="mt-3 max-h-48 space-y-1 overflow-y-auto text-xs text-red-700">
                  {result.errors.map((e, i) => (
                    <li key={i}>{t("impex.rowError", { row: e.row, error: e.error })}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </AppShell>
  );
}
