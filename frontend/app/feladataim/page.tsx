"use client";

/** Feladataim (dolgozói, mobilra optimalizálva): kiosztott feladatok,
 * komment, státuszgombok, munkalap kitöltése aláírással. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import SignatureCanvas from "@/components/SignatureCanvas";
import Link from "next/link";
import { api, errorMessage, shareOrDownloadFile } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { usePerms } from "@/lib/perms";
import { useUI } from "@/lib/ui";

interface MyTicket {
  id: string;
  ticket_no: string;
  kind: string;
  status: string;
  priority: string;
  title: string;
  asset_label: string | null;
  partner_label: string | null;
}

interface CommentOut {
  id: string;
  author_name: string | null;
  text: string;
  created_at: string;
}

interface TaskOut {
  id: string;
  title: string;
  description: string | null;
  due_date: string;
  required_skill: { id: number; name: string } | null;
  status: "open" | "done" | "needs_more_work";
  comments: CommentOut[];
  worksheet_serial: string | null;
  worksheet_completed: boolean;
  worksheet_external: boolean;
  asset: {
    name: string;
    barcode: string | null;
    serial_number: string | null;
    category: string | null;
    partner_name: string | null;
    counter: number | null;
    maintenance_fee: number | null;
  } | null;
}

interface MaterialRow {
  name: string;
  qty: string;
  unit: string;
  // Külső szervizes (KSZ) munkalapon: a szerviz nettó költsége (belső) és
  // a mi ügyfél-árunk (az ügyfél -1-es példányára kerül).
  cost_net?: string;
  price_net?: string;
}

interface WorkRow {
  name: string;
  fee: string; // KSZ-en a szerviz nettó díja (cost_net), sima ML-en a mi árunk (price_net)
}

interface WorksheetForm {
  work_description: string;
  works: WorkRow[];
  repairs: WorkRow[]; // javítási konstrukciók (alternatív ajánlatok árral)
  hours_spent: string;
  materials: MaterialRow[];
  client_name: string;
  client_location: string;
  employee_signature: string | null;
  client_signature: string | null;
  client_signer_name: string;
  maintenance_fee: string;
  fee_discount: boolean;
}

const EMPTY_WS: WorksheetForm = {
  work_description: "",
  works: [],
  repairs: [],
  hours_spent: "",
  materials: [],
  client_name: "",
  client_location: "",
  employee_signature: null,
  client_signature: null,
  client_signer_name: "",
  maintenance_fee: "",
  fee_discount: false,
};

const STATUS_COLORS: Record<string, string> = {
  open: "bg-sky-100 text-sky-800",
  done: "bg-emerald-100 text-emerald-800",
  needs_more_work: "bg-amber-100 text-amber-800",
};

export default function FeladataimPage() {
  const [tasks, setTasks] = useState<TaskOut[]>([]);
  // státusz-szűrő: alapból a nyitottak, hogy a hosszú múlt ne lassítson
  const [statusFilter, setStatusFilter] = useState<"open" | "done" | "all">("open");
  const [comments, setComments] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [noEmployee, setNoEmployee] = useState(false);
  const [wsTask, setWsTask] = useState<TaskOut | null>(null);
  const [ws, setWs] = useState<WorksheetForm>(EMPTY_WS);
  const [wsError, setWsError] = useState<string | null>(null);
  const [wsBusy, setWsBusy] = useState(false);
  const [wsPhotoBusy, setWsPhotoBusy] = useState(false);
  // Ügyfél-árajánlat állapota (a szervizes árakat nem lát, csak státuszt)
  const [wsQuote, setWsQuote] = useState<{ status: string; selected: string | null }>({ status: "none", selected: null });
  const { t } = useT();
  const { toast, prompt } = useUI();

  // Elmentett aláírás-minta: egy kattintással beszúrható a dolgozói aláíráshoz
  const [sigSample, setSigSample] = useState<string | null>(null);
  useEffect(() => {
    api.get<{ signature: string | null }>("/api/auth/me/signature")
      .then((r) => setSigSample(r.signature))
      .catch(() => {});
  }, []);
  async function saveSigSample(dataUrl: string) {
    try {
      await api.put("/api/auth/me/signature", { signature: dataUrl });
      setSigSample(dataUrl);
      toast(t("myTasks.sigSampleSaved"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // Szervizjegyeim: a bejelentkezett szervizesre kiosztott nyitott jegyek —
  // hogy a kiosztás a "feladatai közt" is látsszon, ne csak a Szerviz oldalon.
  const canService = usePerms().can("service");
  const [myTickets, setMyTickets] = useState<MyTicket[]>([]);

  const load = useCallback(() => {
    api
      .get<TaskOut[]>("/api/me/tasks")
      .then(setTasks)
      .catch((err) => {
        if (err?.code === "employee.none_for_user") setNoEmployee(true);
      });
    if (canService) {
      api
        .get<{ id: string }>("/api/auth/me")
        .then((me) =>
          api.get<MyTicket[]>(`/api/service?assigned_to=${me.id}`).then((rows) =>
            setMyTickets(rows.filter((tk) => tk.status === "open" || tk.status === "in_progress")),
          ),
        )
        .catch(() => {});
    }
  }, [canService]);
  useEffect(load, [load]);

  async function startTicket(tk: MyTicket) {
    try {
      await api.patch(`/api/service/${tk.id}`, { status: "in_progress" });
      toast(t("myTasks.ticketStarted"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function sendComment(task: TaskOut) {
    const text = (comments[task.id] ?? "").trim();
    if (!text) return;
    setBusy(task.id);
    try {
      await api.post(`/api/me/tasks/${task.id}/comment`, { text });
      setComments((c) => ({ ...c, [task.id]: "" }));
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(null);
    }
  }

  async function setStatus(task: TaskOut, status: "done" | "needs_more_work") {
    setBusy(task.id);
    try {
      const comment = (comments[task.id] ?? "").trim();
      await api.post(`/api/me/tasks/${task.id}/status`, {
        status,
        comment: comment || null,
      });
      setComments((c) => ({ ...c, [task.id]: "" }));
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBusy(null);
    }
  }

  // A már mentett aláírás-képek (megjelenítésre) + "új aláírás" kapcsolók.
  const [wsSaved, setWsSaved] = useState<{ emp: string | null; client: string | null }>({ emp: null, client: null });
  const [redoSig, setRedoSig] = useState<{ emp: boolean; client: boolean }>({ emp: false, client: false });

  async function openWorksheet(task: TaskOut) {
    setWsError(null);
    setWs(EMPTY_WS);
    setWsQuote({ status: "none", selected: null });
    setWsSaved({ emp: null, client: null });
    setRedoSig({ emp: false, client: false });
    setWsTask(task);
    if (task.worksheet_serial) {
      try {
        const existing = await api.get<{
          work_description: string;
          works: { name: string; cost_net: number | null; price_net: number | null }[];
          repair_options: { name: string; cost_net: number | null; price_net: number | null }[];
          materials: (Omit<MaterialRow, "cost_net" | "price_net"> & {
            cost_net: number | null;
            price_net: number | null;
          })[];
          hours_spent: number | null;
          client_name: string | null;
          client_location: string | null;
          employee_signature: string | null;
          client_signature: string | null;
          client_signer_name: string | null;
          maintenance_fee: number | null;
          fee_discount: boolean;
          quote_status: string;
          quote_selected_name: string | null;
        }>(`/api/me/tasks/${task.id}/worksheet`);
        setWsQuote({ status: existing.quote_status, selected: existing.quote_selected_name });
        setWs({
          work_description: existing.work_description,
          works: (existing.works ?? []).map((w) => {
            const fee = task.worksheet_external ? w.cost_net : w.price_net;
            return { name: w.name, fee: fee != null ? String(fee) : "" };
          }),
          repairs: (existing.repair_options ?? []).map((w) => {
            const fee = task.worksheet_external ? w.cost_net : w.price_net;
            return { name: w.name, fee: fee != null ? String(fee) : "" };
          }),
          hours_spent: existing.hours_spent != null ? String(existing.hours_spent) : "",
          materials: (existing.materials ?? []).map((m) => ({
            ...m,
            cost_net: m.cost_net != null ? String(m.cost_net) : "",
            price_net: m.price_net != null ? String(m.price_net) : "",
          })),
          client_name: existing.client_name ?? "",
          client_location: existing.client_location ?? "",
          employee_signature: null,
          client_signature: null,
          client_signer_name: existing.client_signer_name ?? "",
          maintenance_fee: existing.maintenance_fee != null ? String(existing.maintenance_fee) : "",
          fee_discount: existing.fee_discount,
        });
        setWsSaved({
          emp: existing.employee_signature ?? null,
          client: existing.client_signature ?? null,
        });
      } catch {
        /* friss űrlap marad */
      }
    }
  }

  async function shareWorksheetFor(task: TaskOut) {
    try {
      const result = await shareOrDownloadFile(
        `/api/me/tasks/${task.id}/worksheet/pdf`,
        `${task.worksheet_serial ?? "munkalap"}.pdf`,
      );
      if (result === "downloaded") toast(t("myTasks.wsDownloaded"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function shareWorksheet() {
    if (!wsTask) return;
    try {
      const result = await shareOrDownloadFile(
        `/api/me/tasks/${wsTask.id}/worksheet/pdf`,
        `${wsTask.worksheet_serial ?? "munkalap"}.pdf`,
      );
      if (result === "downloaded") toast(t("myTasks.wsDownloaded"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function emailWorksheet() {
    if (!wsTask) return;
    const to = await prompt(t("tasks.emailPrompt"), { type: "email", placeholder: "ugyfel@example.com" });
    if (!to) return;
    try {
      await api.post(`/api/me/tasks/${wsTask.id}/worksheet/email`, { to });
      toast(t("tasks.emailSent", { to }), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function fillFromPhoto(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // ugyanaz a fájl újra kiválasztható legyen
    if (!file || !wsTask) return;
    setWsPhotoBusy(true);
    setWsError(null);
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = () => reject(new Error("read"));
        reader.readAsDataURL(file);
      });
      const result = await api.post<{
        work_description: string | null;
        materials: MaterialRow[];
        hours_spent: number | null;
      }>(`/api/me/tasks/${wsTask.id}/worksheet/from-photo`, { image: dataUrl });
      setWs((prev) => ({
        ...prev,
        work_description: result.work_description || prev.work_description,
        hours_spent: result.hours_spent != null ? String(result.hours_spent) : prev.hours_spent,
        materials: [...prev.materials, ...(result.materials ?? [])],
      }));
      toast(t("myTasks.wsPhotoFilled"), "success");
    } catch (err) {
      setWsError(errorMessage(err));
    } finally {
      setWsPhotoBusy(false);
    }
  }

  async function saveWorksheet() {
    if (!wsTask) return;
    const workRows = ws.works.filter((w) => w.name.trim());
    if (!ws.work_description.trim() && workRows.length === 0) {
      setWsError(t("myTasks.wsWorkRequired"));
      return;
    }
    // Új ügyfél-aláíráshoz kötelező az aláíró begépelt neve
    if (ws.client_signature && !ws.client_signer_name.trim()) {
      setWsError(t("myTasks.wsSignerNameRequired"));
      return;
    }
    setWsBusy(true);
    setWsError(null);
    try {
      await api.put(`/api/me/tasks/${wsTask.id}/worksheet`, {
        work_description: ws.work_description,
        works: workRows.map((w) => {
          const fee = w.fee ? Number(w.fee) : null;
          // KSZ: a beírt díj a szerviz BELSŐ költsége; sima ML: a mi árunk.
          return wsTask.worksheet_external
            ? { name: w.name, cost_net: fee, price_net: null }
            : { name: w.name, cost_net: null, price_net: fee };
        }),
        repair_options: ws.repairs
          .filter((w) => w.name.trim())
          .map((w) => {
            const fee = w.fee ? Number(w.fee) : null;
            return wsTask.worksheet_external
              ? { name: w.name, cost_net: fee, price_net: null }
              : { name: w.name, cost_net: null, price_net: fee };
          }),
        hours_spent: ws.hours_spent ? Number(ws.hours_spent) : null,
        materials: ws.materials
          .filter((m) => m.name.trim())
          .map((m) => ({
            name: m.name, qty: m.qty, unit: m.unit,
            cost_net: m.cost_net ? Number(m.cost_net) : null,
            price_net: m.price_net ? Number(m.price_net) : null,
          })),
        client_name: ws.client_name || null,
        client_location: ws.client_location || null,
        employee_signature: ws.employee_signature,
        client_signature: ws.client_signature,
        client_signer_name: ws.client_signer_name.trim() || null,
        // KSZ-en a díj a képviselőé — a dolgozói mentés nem küld díjat.
        maintenance_fee: wsTask.worksheet_external
          ? null
          : ws.maintenance_fee
            ? Number(ws.maintenance_fee)
            : null,
        fee_discount: wsTask.worksheet_external ? null : ws.fee_discount,
      });
      setWsTask(null);
      load();
    } catch (err) {
      setWsError(errorMessage(err));
    } finally {
      setWsBusy(false);
    }
  }

  if (noEmployee) {
    return (
      <AppShell>
        <p className="rounded-2xl border border-amber-200 bg-amber-50 p-6 text-amber-800">
          {t("mySched.noEmployeeCard")}
        </p>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-lg space-y-4">
        <h1 className="text-xl font-bold">{t("myTasks.title")}</h1>

        {/* Státusz-szűrő: nyitott (alap) / befejezett / mind */}
        <div className="flex rounded-xl border border-slate-200 bg-white p-1 text-sm font-medium">
          {(["open", "done", "all"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setStatusFilter(f)}
              className={`flex-1 rounded-lg px-3 py-1.5 transition-colors ${
                statusFilter === f
                  ? "bg-indigo-600 text-white shadow"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {t(`myTasks.filter.${f}`)}
            </button>
          ))}
        </div>

        {/* Rám osztott nyitott szervizjegyek */}
        {myTickets.length > 0 && (
          <section className="rounded-2xl border border-amber-200 bg-amber-50 p-4 shadow-sm">
            <h2 className="mb-2 font-semibold text-amber-900">
              🔧 {t("myTasks.myTickets")}{" "}
              <span className="ml-1 rounded-full bg-amber-200 px-2 py-0.5 text-sm font-bold text-amber-900">
                {myTickets.length}
              </span>
            </h2>
            <ul className="space-y-2">
              {myTickets.map((tk) => (
                <li key={tk.id} className="rounded-xl bg-white p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-slate-400">{tk.ticket_no}</span>
                    <span className="font-medium">{tk.title}</span>
                    {tk.priority === "high" && (
                      <span className="rounded bg-rose-100 px-1.5 py-0.5 text-[11px] font-semibold text-rose-700">
                        {t("service.priorities.high")}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-500">
                    {[tk.asset_label, tk.partner_label].filter(Boolean).join(" · ") || "—"}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {tk.status === "open" ? (
                      <button
                        onClick={() => startTicket(tk)}
                        className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700"
                      >
                        ▶️ {t("service.start")}
                      </button>
                    ) : (
                      <span className="rounded bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800">
                        {t("service.statuses.in_progress")}
                      </span>
                    )}
                    <Link
                      href="/szerviz"
                      className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-100"
                    >
                      {t("myTasks.openInService")}
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        {tasks.filter(
          (task) =>
            statusFilter === "all" ||
            (statusFilter === "done"
              ? task.status === "done"
              : task.status !== "done"),
        ).length === 0 && (
          <p className="rounded-2xl border border-slate-200 bg-white px-4 py-10 text-center text-slate-400">
            {t("myTasks.empty")}
          </p>
        )}
        {tasks.filter(
          (task) =>
            statusFilter === "all" ||
            (statusFilter === "done"
              ? task.status === "done"
              : task.status !== "done"),
        ).map((task) => (
          <section key={task.id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">{task.title}</h2>
              <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[task.status]}`}>
                {t(`tasks.statuses.${task.status}`)}
              </span>
            </div>
            <p className="text-xs text-slate-400">{t("myTasks.due", { date: task.due_date })}</p>
            {task.asset && (
              <div className="mt-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
                <p className="font-medium text-slate-700">☕ {task.asset.name}</p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {[
                    task.asset.serial_number && `${t("myTasks.assetSerial")}: ${task.asset.serial_number}`,
                    task.asset.barcode && `${t("myTasks.assetBarcode")}: ${task.asset.barcode}`,
                    task.asset.counter != null && `${t("myTasks.assetCounter")}: ${task.asset.counter}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                {task.asset.partner_name && (
                  <p className="text-xs text-slate-500">📍 {task.asset.partner_name}</p>
                )}
              </div>
            )}
            {task.description && (
              <p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">{task.description}</p>
            )}

            {task.comments.length > 0 && (
              <ul className="mt-3 space-y-1 border-t border-slate-100 pt-2 text-sm">
                {task.comments.map((c) => (
                  <li key={c.id}>
                    <span className="font-medium">{c.author_name ?? "?"}:</span>{" "}
                    <span className="text-slate-600">{c.text}</span>
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-3 space-y-2">
              <button
                onClick={() => openWorksheet(task)}
                className={`w-full rounded-xl border px-4 py-2.5 text-sm font-medium ${
                  task.worksheet_completed
                    ? "border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100"
                    : "border-indigo-300 bg-indigo-50 text-indigo-800 hover:bg-indigo-100"
                }`}
              >
                {task.worksheet_completed
                  ? t("myTasks.worksheetDone", { serial: task.worksheet_serial ?? "" })
                  : task.worksheet_serial
                    ? t("myTasks.fillWorksheetSerial", { serial: task.worksheet_serial })
                    : t("myTasks.fillWorksheet")}
              </button>
              {task.worksheet_serial && (
                <button
                  onClick={() => shareWorksheetFor(task)}
                  className="w-full rounded-xl border border-emerald-300 bg-emerald-50 px-4 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100"
                >
                  📤 {t("myTasks.wsShareCard", { serial: task.worksheet_serial })}
                </button>
              )}
              <div className="flex gap-2">
                <input
                  value={comments[task.id] ?? ""}
                  onChange={(e) => setComments((c) => ({ ...c, [task.id]: e.target.value }))}
                  placeholder={t("myTasks.commentPlaceholder")}
                  className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <button
                  onClick={() => sendComment(task)}
                  disabled={busy === task.id || !(comments[task.id] ?? "").trim()}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-40"
                >
                  {t("common.send")}
                </button>
              </div>
              {task.status !== "done" && (
                <div className="flex gap-2">
                  <button
                    onClick={() => setStatus(task, "done")}
                    disabled={busy === task.id}
                    className="flex-1 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {t("myTasks.done")}
                  </button>
                  <button
                    onClick={() => setStatus(task, "needs_more_work")}
                    disabled={busy === task.id}
                    className="flex-1 rounded-xl bg-amber-500 px-4 py-2.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
                  >
                    {t("myTasks.needsMoreWork")}
                  </button>
                </div>
              )}
            </div>
          </section>
        ))}
      </div>

      {wsTask && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setWsTask(null); }} className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <div className="my-6 w-full max-w-lg space-y-3 rounded-2xl bg-white p-5 shadow-xl">
            <h2 className="text-lg font-semibold">{t("myTasks.wsTitle", { title: wsTask.title })}</h2>
            {wsTask.worksheet_serial && (
              <p className="text-xs text-slate-500">{t("myTasks.wsSerial", { serial: wsTask.worksheet_serial })}</p>
            )}
            {wsQuote.status === "accepted" && (
              <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800">
                🟢 {t("myTasks.quoteAccepted", { option: wsQuote.selected ?? "" })}
              </p>
            )}
            {wsQuote.status === "sent" && (
              <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                ⏳ {t("myTasks.quotePending")}
              </p>
            )}
            {wsQuote.status === "declined" && (
              <p className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-medium text-rose-800">
                🔴 {t("myTasks.quoteDeclined")}
              </p>
            )}
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-indigo-300 px-3 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50">
              📷 {wsPhotoBusy ? t("myTasks.wsPhotoBusy") : t("myTasks.wsPhotoFill")}
              <input
                type="file"
                accept="image/*"
                capture="environment"
                onChange={fillFromPhoto}
                disabled={wsPhotoBusy}
                className="hidden"
              />
            </label>
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm text-slate-600">{t("myTasks.wsWorks")}</span>
                <button
                  type="button"
                  onClick={() => setWs({ ...ws, works: [...ws.works, { name: "", fee: "" }] })}
                  className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
                >
                  {t("myTasks.wsAddWork")}
                </button>
              </div>
              {ws.works.map((w, i) => (
                <div key={i} className="mb-1 flex gap-1">
                  <input
                    value={w.name}
                    onChange={(e) => {
                      const next = [...ws.works];
                      next[i] = { ...w, name: e.target.value };
                      setWs({ ...ws, works: next });
                    }}
                    placeholder={t("myTasks.wsWorkName")}
                    className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    type="number"
                    min={0}
                    value={w.fee}
                    onChange={(e) => {
                      const next = [...ws.works];
                      next[i] = { ...w, fee: e.target.value };
                      setWs({ ...ws, works: next });
                    }}
                    placeholder={t("myTasks.wsWorkFee")}
                    title={t("myTasks.wsWorkFee")}
                    className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => setWs({ ...ws, works: ws.works.filter((_, idx) => idx !== i) })}
                    className="px-1 text-slate-400 hover:text-red-600"
                  >
                    ✕
                  </button>
                </div>
              ))}
              {wsTask?.worksheet_external && ws.works.length > 0 && (
                <p className="text-xs text-slate-400">{t("myTasks.wsWorksInternalHint")}</p>
              )}
            </div>

            {/* Javítási konstrukciók: alternatív ajánlatok árral */}
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm text-slate-600">{t("myTasks.wsRepairs")}</span>
                <button
                  type="button"
                  onClick={() => setWs({ ...ws, repairs: [...ws.repairs, { name: "", fee: "" }] })}
                  className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
                >
                  {t("myTasks.wsAddRepair")}
                </button>
              </div>
              {ws.repairs.map((w, i) => (
                <div key={i} className="mb-1 flex gap-1">
                  <input
                    value={w.name}
                    onChange={(e) => {
                      const next = [...ws.repairs];
                      next[i] = { ...w, name: e.target.value };
                      setWs({ ...ws, repairs: next });
                    }}
                    placeholder={t("myTasks.wsRepairName")}
                    className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    type="number"
                    min={0}
                    value={w.fee}
                    onChange={(e) => {
                      const next = [...ws.repairs];
                      next[i] = { ...w, fee: e.target.value };
                      setWs({ ...ws, repairs: next });
                    }}
                    placeholder={t("myTasks.wsWorkFee")}
                    title={t("myTasks.wsWorkFee")}
                    className="w-28 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => setWs({ ...ws, repairs: ws.repairs.filter((_, idx) => idx !== i) })}
                    className="px-1 text-slate-400 hover:text-red-600"
                  >
                    ✕
                  </button>
                </div>
              ))}
              {ws.repairs.length > 0 && (
                <p className="text-xs text-slate-400">{t("myTasks.wsRepairsHint")}</p>
              )}
            </div>

            <label className="block text-sm">
              {t("myTasks.wsWork")}
              <textarea
                value={ws.work_description}
                onChange={(e) => setWs({ ...ws, work_description: e.target.value })}
                rows={4}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                placeholder={t("myTasks.wsWorkPlaceholder")}
              />
            </label>
            <label className="block text-sm">
              {t("myTasks.wsHours")}
              <input
                type="number"
                min={0}
                step={0.5}
                value={ws.hours_spent}
                onChange={(e) => setWs({ ...ws, hours_spent: e.target.value })}
                className="mt-1 w-32 rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>

            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm text-slate-600">{t("myTasks.wsMaterials")}</span>
                <button
                  type="button"
                  onClick={() =>
                    setWs({ ...ws, materials: [...ws.materials, { name: "", qty: "1", unit: t("myTasks.wsUnit") }] })
                  }
                  className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
                >
                  {t("myTasks.wsAddItem")}
                </button>
              </div>
              {ws.materials.map((m, i) => (
                <div key={i} className="mb-1 flex flex-wrap gap-1">
                  <input
                    value={m.name}
                    onChange={(e) => {
                      const next = [...ws.materials];
                      next[i] = { ...m, name: e.target.value };
                      setWs({ ...ws, materials: next });
                    }}
                    placeholder={t("myTasks.wsItemName")}
                    className="flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    value={m.qty}
                    onChange={(e) => {
                      const next = [...ws.materials];
                      next[i] = { ...m, qty: e.target.value };
                      setWs({ ...ws, materials: next });
                    }}
                    placeholder={t("myTasks.wsQty")}
                    className="w-16 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    value={m.unit}
                    onChange={(e) => {
                      const next = [...ws.materials];
                      next[i] = { ...m, unit: e.target.value };
                      setWs({ ...ws, materials: next });
                    }}
                    placeholder={t("myTasks.wsUnit")}
                    className="w-14 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() =>
                      setWs({ ...ws, materials: ws.materials.filter((_, idx) => idx !== i) })
                    }
                    className="px-1 text-slate-400 hover:text-red-600"
                  >
                    ✕
                  </button>
                  {wsTask?.worksheet_external && (
                    <div className="mt-1 flex w-full gap-1 pl-2">
                      <input
                        type="number" min={0}
                        value={m.cost_net ?? ""}
                        onChange={(e) => {
                          const next = [...ws.materials];
                          next[i] = { ...m, cost_net: e.target.value };
                          setWs({ ...ws, materials: next });
                        }}
                        placeholder={t("myTasks.wsCostNet")}
                        title={t("myTasks.wsCostNet")}
                        className="flex-1 rounded-lg border border-orange-200 bg-orange-50 px-2 py-1.5 text-sm"
                      />
                    </div>
                  )}
                </div>
              ))}
              {wsTask?.worksheet_external && ws.materials.length > 0 && (
                <p className="text-xs text-slate-400">{t("myTasks.wsExternalHint2")}</p>
              )}
              {/* A karbantartási díj a MI ügyfél-árunk — a szervizes nem
                  látja és nem állíthatja; a képviselő kezeli az
                  ár-szerkesztőben. */}
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <label className="block text-sm">
                {t("myTasks.wsClient")}
                <input
                  value={ws.client_name}
                  onChange={(e) => setWs({ ...ws, client_name: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              <label className="block text-sm">
                {t("myTasks.wsLocation")}
                <input
                  value={ws.client_location}
                  onChange={(e) => setWs({ ...ws, client_location: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            </div>

            {wsSaved.emp && !redoSig.emp ? (
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-sm font-medium">{t("myTasks.wsEmpSigSaved")}</span>
                  <button
                    type="button"
                    onClick={() => setRedoSig((r) => ({ ...r, emp: true }))}
                    className="text-xs text-indigo-600 hover:underline"
                  >
                    {t("myTasks.wsSignAgain")}
                  </button>
                </div>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={wsSaved.emp} alt="" className="h-24 w-full rounded-lg border border-emerald-200 bg-white object-contain" />
              </div>
            ) : (
              <SignatureCanvas
                label={wsTask.worksheet_completed ? t("myTasks.wsEmpSigKeep") : t("myTasks.wsEmpSig")}
                onChange={(d) => setWs((w) => ({ ...w, employee_signature: d }))}
                preset={sigSample}
                onSavePreset={saveSigSample}
              />
            )}
            {wsSaved.client && !redoSig.client ? (
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-sm font-medium">{t("myTasks.wsClientSigSaved")}</span>
                  <button
                    type="button"
                    onClick={() => setRedoSig((r) => ({ ...r, client: true }))}
                    className="text-xs text-indigo-600 hover:underline"
                  >
                    {t("myTasks.wsSignAgain")}
                  </button>
                </div>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={wsSaved.client} alt="" className="h-24 w-full rounded-lg border border-emerald-200 bg-white object-contain" />
              </div>
            ) : (
              <div className="space-y-2">
                <SignatureCanvas
                  label={wsTask.worksheet_completed ? t("myTasks.wsClientSigKeep") : t("myTasks.wsClientSig")}
                  onChange={(d) => setWs((w) => ({ ...w, client_signature: d }))}
                />
                {/* Az ügyfél-aláíráshoz kötelező a begépelt név */}
                <input
                  value={ws.client_signer_name}
                  onChange={(e) => setWs((w) => ({ ...w, client_signer_name: e.target.value }))}
                  placeholder={t("myTasks.wsSignerName")}
                  className={`w-full rounded-lg border px-3 py-2 text-sm ${
                    ws.client_signature && !ws.client_signer_name.trim()
                      ? "border-rose-400 bg-rose-50"
                      : "border-slate-300"
                  }`}
                />
              </div>
            )}

            {wsError && <p className="text-sm text-red-600">{wsError}</p>}
            <div className="flex flex-wrap justify-end gap-2 pt-1">
              {wsTask.worksheet_serial && (
                <>
                  <button
                    type="button"
                    onClick={shareWorksheet}
                    className="mr-auto rounded-lg border border-emerald-300 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-50"
                  >
                    {t("myTasks.wsShare")}
                  </button>
                  <button
                    type="button"
                    onClick={emailWorksheet}
                    className="rounded-lg border border-emerald-300 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-50"
                  >
                    {t("myTasks.wsEmail")}
                  </button>
                </>
              )}
              <button
                onClick={() => setWsTask(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={saveWorksheet}
                disabled={wsBusy}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {wsBusy ? t("common.saving") : t("myTasks.wsSave")}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
