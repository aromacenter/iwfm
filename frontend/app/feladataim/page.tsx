"use client";

/** Feladataim (dolgozói, mobilra optimalizálva): kiosztott feladatok,
 * komment, státuszgombok, munkalap kitöltése aláírással. */

import { useCallback, useEffect, useRef, useState } from "react";
import AppShell from "@/components/AppShell";
import PhotoLightbox from "@/components/PhotoLightbox";
import SignatureCanvas from "@/components/SignatureCanvas";
import SearchSelect from "@/components/SearchSelect";
import SuggestInput from "@/components/SuggestInput";
import Link from "next/link";
import { api, ApiError, errorMessage, shareOrDownloadFile } from "@/lib/api";
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
  ticket_images: string[]; // szervizjegyből jött feladat csatolt képei (id-k)
  intake_id: string | null;
  intake_photos: string[]; // az átvételkor készült fotók id-i
  worksheet_photos: string[]; // a munkalaphoz csatolt saját fotók id-i
  completed_at: string | null;
  completed_by_name: string | null;
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

// Beépített alkatrész (alkatrész-raktár, B fázis) — a /api/parts/use könyveli.
interface UsedPart {
  movement_id: string;
  part_id: string;
  sku: string;
  name: string;
  unit: string;
  quantity: number;
  price_net: number | null;
  warehouse_name: string;
  serial_no?: string | null;
}
interface MyPartsStock { part_id: string; sku: string; name: string; unit: string; quantity: number }
// Munkaidő-mérés (worktime modul): a dolgozó csak az állapotot látja, számokat nem.
interface TimerState { state: "running" | "paused" | "stopped" | "none"; started_at: string | null; pause_count: number; mode?: "work" | "travel" | "wait"; correction_status?: string }
interface Kit { id: string; sku: string; name: string; items: { sku: string; name: string; quantity: number; unit: string }[] }
interface PartsQueueItem { task_id: string; part_id: string; sku: string; quantity: number; serial_no: string | null; queued_at: string }

interface WorkRow {
  name: string;
  fee: string; // KSZ-en a szerviz nettó díja (cost_net), sima ML-en a mi árunk (price_net)
}

interface WorksheetForm {
  work_description: string;
  technician_name: string; // alvállalkozó cégnél: a cégen belüli szerelő
  public_note: string; // az ügyfélnek szánt megjegyzés (munkalap + ajánlat)
  problem_text: string; // hibaleírás (tudástár)
  solution_text: string; // megoldás (tudástár)
  print_solution: boolean; // a megoldás az ügyfél-példányra is
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
  total_loss: boolean; // gazdasági totálkár — az ajánlaton csak 2 opció lesz
}

const EMPTY_WS: WorksheetForm = {
  work_description: "",
  technician_name: "",
  public_note: "",
  problem_text: "",
  solution_text: "",
  print_solution: false,
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
  total_loss: false,
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

  // Telegram-linkbol erkezes (?task=): a konkret feladathoz gorgetunk.
  useEffect(() => {
    const tid = new URLSearchParams(window.location.search).get("task");
    if (!tid || tasks.length === 0) return;
    const el = document.getElementById(`task-${tid}`);
    if (el) {
      el.scrollIntoView({ block: "start", behavior: "smooth" });
      el.classList.add("ring-2", "ring-indigo-400");
      setTimeout(() => el.classList.remove("ring-2", "ring-indigo-400"), 4000);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasks.length]);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [noEmployee, setNoEmployee] = useState(false);
  const [wsTask, setWsTask] = useState<TaskOut | null>(null);
  // Fotónéző nagyítással (2aeefcbd)
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [ws, setWs] = useState<WorksheetForm>(EMPTY_WS);
  // Beépített alkatrészek: a saját autós raktár készlete + a munkalap sorai.
  // null = nincs parts modul / nincs autós raktár → a blokk nem jelenik meg.
  const [myParts, setMyParts] = useState<MyPartsStock[] | null>(null);
  const [usedParts, setUsedParts] = useState<UsedPart[]>([]);
  const [partPick, setPartPick] = useState<{ part_id: string; quantity: string; serial_no: string }>({ part_id: "", quantity: "1", serial_no: "" });
  const [kits, setKits] = useState<Kit[]>([]);
  const [kitPick, setKitPick] = useState("");
  const [pendingParts, setPendingParts] = useState(0);
  const [partsBusy, setPartsBusy] = useState(false);
  const [timer, setTimer] = useState<TimerState | null>(null);
  const [wsError, setWsError] = useState<string | null>(null);
  const [wsBusy, setWsBusy] = useState(false);
  const [wsPhotoBusy, setWsPhotoBusy] = useState(false);
  // Ügyfél-árajánlat állapota (a szervizes árakat nem lát, csak státuszt)
  const [wsQuote, setWsQuote] = useState<{ status: string; selected: string | null }>({ status: "none", selected: null });
  // A cég szerelő-névsora a Szerelő mező súgójához (d8bde9e6)
  const [myTechs, setMyTechs] = useState<string[]>([]);
  useEffect(() => {
    api.get<string[]>("/api/tasks/my-technicians").then(setMyTechs).catch(() => {});
  }, []);

  // OFFLINE-előszinkron (841171f6): a nyitott feladatok munkalapjait a
  // háttérben egyszer lekérjük — a service worker eltárolja, így térerő
  // nélkül is megnyílnak a helyszínen.
  const prefetched = useRef(false);
  useEffect(() => {
    if (prefetched.current || tasks.length === 0) return;
    prefetched.current = true;
    for (const task of tasks.filter((x) => x.status !== "done").slice(0, 25)) {
      fetch(`/api/me/tasks/${task.id}/worksheet`, { credentials: "include" }).catch(() => {});
    }
  }, [tasks]);

  // Feladat kiadása a cég szerelőjének (87994b7c)
  async function assignTechnician(taskId: string, name: string) {
    try {
      await api.post(`/api/me/tasks/${taskId}/technician`, { name: name || null });
      toast(name ? t("myTasks.techAssigned", { name }) : t("myTasks.techCleared"), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }
  const { t } = useT();
  const { toast, prompt, confirm } = useUI();

  // Offline munkalap-várólista (841171f6): hálózat nélkül mentett munkalapok
  // helyben tárolódnak, és online-ra érve magától beküldődnek.
  interface WsQueueItem { task_id: string; title: string; body: unknown; queued_at: string }
  const WS_QUEUE_KEY = "iwfm-ws-queue";
  function readWsQueue(): WsQueueItem[] {
    try { return JSON.parse(localStorage.getItem(WS_QUEUE_KEY) ?? "[]") as WsQueueItem[]; }
    catch { return []; }
  }
  function writeWsQueue(q: WsQueueItem[]) {
    try { localStorage.setItem(WS_QUEUE_KEY, JSON.stringify(q)); } catch { /* tárhely-hiba */ }
  }
  const [pendingWs, setPendingWs] = useState(0);
  const flushWsQueue = useCallback(async () => {
    let q = readWsQueue();
    setPendingWs(q.length);
    if (q.length === 0) return;
    let sent = 0;
    while (q.length > 0) {
      const item = q[0];
      try {
        await api.put(`/api/me/tasks/${item.task_id}/worksheet`, item.body);
        sent += 1;
        q = q.slice(1);
        writeWsQueue(q);
      } catch (err) {
        if (err instanceof ApiError) {
          // a szerver elutasította — eldobjuk, jelezzük (ne ragadjon be a sor)
          toast(t("offline.wsDropped", { title: item.title, reason: errorMessage(err) }), "error");
          q = q.slice(1);
          writeWsQueue(q);
        } else {
          break; // még mindig nincs hálózat — később újra
        }
      }
    }
    setPendingWs(q.length);
    if (sent > 0) {
      toast(t("offline.wsFlushed", { count: sent }), "success");
      load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toast, t]);
  useEffect(() => {
    flushWsQueue();
    const onOnline = () => flushWsQueue();
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [flushWsQueue]);


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
    setUsedParts([]);
    setPartPick({ part_id: "", quantity: "1", serial_no: "" });
    api.get<Kit[]>("/api/parts/kits").then(setKits).catch(() => setKits([]));
    // Munkaidő-mérés: a megnyitás indítja (idempotens); modul nélkül 403 → nincs sáv
    setTimer(null);
    api.post<TimerState>(`/api/worktime/${task.id}/start`, {}).then(setTimer).catch(() => setTimer(null));
    // saját autós alkatrész-készlet (parts modul nélkül / autó nélkül: nincs blokk)
    api.get<{ warehouse_id: string | null; stock: MyPartsStock[] }>("/api/parts/my-warehouse")
      .then((r) => setMyParts(r.warehouse_id ? r.stock : null))
      .catch(() => setMyParts(null));
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
          total_loss: boolean;
          quote_status: string;
          quote_selected_name: string | null;
          public_note: string | null;
          used_parts?: UsedPart[];
        }>(`/api/me/tasks/${task.id}/worksheet`);
        setWsQuote({ status: existing.quote_status, selected: existing.quote_selected_name });
        setUsedParts(existing.used_parts ?? []);
        setWs({
          work_description: existing.work_description,
          technician_name: (existing as { technician_name?: string | null }).technician_name ?? "",
          public_note: existing.public_note ?? "",
          problem_text: (existing as { problem_text?: string | null }).problem_text ?? "",
          solution_text: (existing as { solution_text?: string | null }).solution_text ?? "",
          print_solution: Boolean((existing as { print_solution?: boolean }).print_solution),
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
          total_loss: existing.total_loss,
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

  async function uploadWsPhotos(task: TaskOut, files: FileList | null) {
    if (!files || files.length === 0) return;
    const toDataUrl = (f: File) =>
      new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result));
        r.onerror = reject;
        r.readAsDataURL(f);
      });
    try {
      const photos = await Promise.all(Array.from(files).slice(0, 8).map(toDataUrl));
      await api.post(`/api/me/tasks/${task.id}/worksheet/photos`, { photos });
      toast(t("myTasks.wsPhotosUploaded", { count: photos.length }), "success");
      load();
    } catch (err) {
      toast(errorMessage(err), "error");
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
    const wsBody = {
        work_description: ws.work_description,
        technician_name: ws.technician_name.trim() || null,
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
        public_note: ws.public_note.trim() || null,
        problem_text: ws.problem_text.trim() || null,
        solution_text: ws.solution_text.trim() || null,
        print_solution: ws.print_solution,
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
        total_loss: ws.total_loss,
    };
    try {
      await api.put(`/api/me/tasks/${wsTask.id}/worksheet`, wsBody);
      closeWorksheet();
      load();
    } catch (err) {
      if (!(err instanceof ApiError)) {
        // Nincs hálózat (841171f6): a munkalap a helyi várólistára kerül,
        // és online-ra érve magától beküldődik — a munka nem vész el.
        const q = readWsQueue();
        // feladatonként a LEGUTOLSÓ mentés él (a korábbi sorban lévőt lecseréli)
        const next = q.filter((x) => x.task_id !== wsTask.id);
        next.push({
          task_id: wsTask.id,
          title: wsTask.title,
          body: wsBody,
          queued_at: new Date().toISOString(),
        });
        writeWsQueue(next);
        setPendingWs(next.length);
        closeWorksheet();
        toast(t("offline.queued"), "info");
      } else {
        setWsError(errorMessage(err));
      }
    } finally {
      setWsBusy(false);
    }
  }

  // ── munkaidő-mérés: bezárás/mentés zár, Szünet/Folytatás ──
  function closeWorksheet() {
    if (wsTask && timer && (timer.state === "running" || timer.state === "paused")) {
      void api.post(`/api/worktime/${wsTask.id}/stop`, {}).catch(() => {});
    }
    setTimer(null);
    setWsTask(null);
  }

  async function toggleTimer() {
    if (!wsTask || !timer) return;
    try {
      setTimer(await api.post<TimerState>(`/api/worktime/${wsTask.id}/${timer.state === "paused" ? "resume" : "pause"}`, {}));
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // ── beépített alkatrészek (saját autós raktárból, azonnali könyvelés) ──
  // Offline beépítés-várólista: hálózat nélkül a beépítés helyben sorban áll,
  // online-ra érve beküldődik (a szerver-oldali szabályok ugyanúgy élnek).
  const PARTS_QUEUE_KEY = "iwfm-parts-queue";
  function readPartsQueue(): PartsQueueItem[] {
    try { return JSON.parse(localStorage.getItem(PARTS_QUEUE_KEY) ?? "[]") as PartsQueueItem[]; } catch { return []; }
  }
  function writePartsQueue(q: PartsQueueItem[]) {
    try { localStorage.setItem(PARTS_QUEUE_KEY, JSON.stringify(q)); } catch { /* tárhely-hiba */ }
  }
  const flushPartsQueue = useCallback(async () => {
    let q = readPartsQueue();
    setPendingParts(q.length);
    if (q.length === 0) return;
    let sent = 0;
    while (q.length > 0) {
      const item = q[0];
      try {
        await api.post("/api/parts/use", { task_id: item.task_id, part_id: item.part_id, quantity: item.quantity, serial_no: item.serial_no });
        sent += 1; q = q.slice(1); writePartsQueue(q);
      } catch (err) {
        if (err instanceof ApiError) {
          toast(t("myTasks.wsPartsDropped", { sku: item.sku, reason: errorMessage(err) }), "error");
          q = q.slice(1); writePartsQueue(q);
        } else {
          break;
        }
      }
    }
    setPendingParts(q.length);
    if (sent > 0) toast(t("myTasks.wsPartsFlushed", { count: sent }), "success");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toast, t]);
  useEffect(() => {
    void Promise.resolve().then(() => flushPartsQueue());
    const onOnline = () => flushPartsQueue();
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [flushPartsQueue]);

  async function installPart() {
    if (!wsTask || !partPick.part_id) return;
    setPartsBusy(true);
    const body = { task_id: wsTask.id, part_id: partPick.part_id, quantity: Number(partPick.quantity.replace(",", ".")) || 0, serial_no: partPick.serial_no.trim() || null };
    try {
      const rows = await api.post<UsedPart[]>("/api/parts/use", body);
      setUsedParts(rows);
      setPartPick({ part_id: "", quantity: "1", serial_no: "" });
      const r = await api.get<{ warehouse_id: string | null; stock: MyPartsStock[] }>("/api/parts/my-warehouse");
      setMyParts(r.warehouse_id ? r.stock : null);
    } catch (err) {
      if (!(err instanceof ApiError)) {
        const sku = myParts?.find((s) => s.part_id === body.part_id)?.sku ?? "?";
        const q = readPartsQueue();
        q.push({ ...body, sku, queued_at: new Date().toISOString() });
        writePartsQueue(q);
        setPendingParts(q.length);
        setPartPick({ part_id: "", quantity: "1", serial_no: "" });
        toast(t("myTasks.wsPartsQueued"), "info");
      } else {
        toast(errorMessage(err), "error");
      }
    } finally {
      setPartsBusy(false);
    }
  }

  async function installKit() {
    if (!wsTask || !kitPick) return;
    setPartsBusy(true);
    try {
      setUsedParts(await api.post<UsedPart[]>("/api/parts/use-kit", { task_id: wsTask.id, kit_id: kitPick }));
      setKitPick("");
      const r = await api.get<{ warehouse_id: string | null; stock: MyPartsStock[] }>("/api/parts/my-warehouse");
      setMyParts(r.warehouse_id ? r.stock : null);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setPartsBusy(false);
    }
  }

  async function setTimerMode(mode: "work" | "travel" | "wait") {
    if (!wsTask || !timer) return;
    try { setTimer(await api.post<TimerState>(`/api/worktime/${wsTask.id}/mode:${mode}`, {})); }
    catch (err) { toast(errorMessage(err), "error"); }
  }

  async function requestCorrection() {
    if (!wsTask) return;
    const raw = await prompt(t("myTasks.timerCorrectionPrompt"));
    if (!raw) return;
    const [minsRaw, ...rest] = raw.split(";");
    const mins = Number(String(minsRaw).replace(",", "."));
    const note = rest.join(";").trim();
    if (!Number.isFinite(mins) || mins === 0 || note.length < 3) { toast(t("myTasks.timerCorrectionPrompt"), "error"); return; }
    try {
      setTimer(await api.post<TimerState>(`/api/worktime/correction/${wsTask.id}/request`, { seconds: Math.round(mins * 60), note }));
      toast(t("myTasks.timerCorrectionSent"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  async function uninstallPart(u: UsedPart) {
    if (!(await confirm(t("myTasks.wsPartsRemove")))) return;
    setPartsBusy(true);
    try {
      setUsedParts(await api.delete<UsedPart[]>(`/api/parts/use/${u.movement_id}`));
      const r = await api.get<{ warehouse_id: string | null; stock: MyPartsStock[] }>("/api/parts/my-warehouse");
      setMyParts(r.warehouse_id ? r.stock : null);
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setPartsBusy(false);
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
        {pendingWs > 0 && (
          <button
            onClick={() => void flushWsQueue()}
            title={t("offline.retryHint")}
            className="w-full rounded-2xl border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-medium text-amber-900"
          >
            📴 {t("offline.wsPending", { count: pendingWs })}
          </button>
        )}
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
          <section key={task.id} id={`task-${task.id}`} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">{task.title}</h2>
              <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[task.status]}`}>
                {t(`tasks.statuses.${task.status}`)}
              </span>
            </div>
            <p className="text-xs text-slate-400">{t("myTasks.due", { date: task.due_date })}</p>
            {/* Cégen belüli kiadás (87994b7c): melyik szerelő viszi a munkát */}
            {myTechs.length > 0 && task.status !== "done" && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-sm">
                <span className="font-medium text-violet-900">👷 {t("myTasks.assignTech")}:</span>
                <select
                  value={(task as { technician_name?: string | null }).technician_name ?? ""}
                  onChange={(e) => void assignTechnician(task.id, e.target.value)}
                  className="rounded-lg border border-violet-300 bg-white px-2 py-1 text-sm"
                >
                  <option value="">{t("myTasks.assignTechNone")}</option>
                  {myTechs.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </div>
            )}
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
            {(task.ticket_images ?? []).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {task.ticket_images.map((imgId) => {
                  const src = `/api/me/tasks/${task.id}/ticket-image/${imgId}`;
                  return (
                    <button
                      key={imgId}
                      onClick={() => setLightbox(src)}
                      className="overflow-hidden rounded-xl border border-slate-200 shadow-sm transition hover:ring-2 hover:ring-indigo-300"
                      title={t("myTasks.ticketImage")}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={src} alt="" className="h-20 w-20 object-cover" loading="lazy" />
                    </button>
                  );
                })}
              </div>
            )}

            {/* Átvételi fotók: a szerelő látja, milyen állapotban vettük át a gépet */}
            {task.intake_id && (task.intake_photos ?? []).length > 0 && (
              <div className="mt-2">
                <p className="mb-1 text-xs font-semibold uppercase text-slate-500">📥 {t("myTasks.intakePhotos")}</p>
                <div className="flex flex-wrap gap-2">
                  {task.intake_photos.map((pid) => {
                    const src = `/api/intakes/${task.intake_id}/photos/${pid}`;
                    return (
                      <button
                        key={pid}
                        onClick={() => setLightbox(src)}
                        className="overflow-hidden rounded-xl border border-slate-200 shadow-sm transition hover:ring-2 hover:ring-indigo-300"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={src} alt="" className="h-20 w-20 object-cover" loading="lazy" />
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            {/* A munkalaphoz csatolt saját fotók + új fotó készítése */}
            {task.worksheet_serial && (
              <div className="mt-2">
                <div className="flex flex-wrap items-center gap-2">
                  {(task.worksheet_photos ?? []).map((pid) => {
                    const src = `/api/me/tasks/${task.id}/worksheet/photos/${pid}`;
                    return (
                      <button
                        key={pid}
                        onClick={() => setLightbox(src)}
                        className="overflow-hidden rounded-xl border border-slate-200 shadow-sm transition hover:ring-2 hover:ring-indigo-300"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={src} alt="" className="h-20 w-20 object-cover" loading="lazy" />
                      </button>
                    );
                  })}
                  <label className="flex h-20 w-20 cursor-pointer items-center justify-center rounded-xl border-2 border-dashed border-slate-300 text-2xl text-slate-400 hover:border-indigo-400 hover:text-indigo-500">
                    📷
                    <input
                      type="file"
                      accept="image/*"
                      capture="environment"
                      multiple
                      className="hidden"
                      onChange={(e) => void uploadWsPhotos(task, e.target.files)}
                    />
                  </label>
                </div>
                <p className="mt-0.5 text-xs text-slate-400">{t("myTasks.wsPhotosHint")}</p>
              </div>
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
              {task.worksheet_serial && (
                <button
                  onClick={async () => {
                    try {
                      await api.post(`/api/me/tasks/${task.id}/worksheet/print`, {});
                      toast(t("myTasks.officeQueued"), "success");
                    } catch (err) {
                      toast(errorMessage(err), "error");
                    }
                  }}
                  className="w-full rounded-xl border border-indigo-300 bg-indigo-50 px-4 py-2 text-sm font-medium text-indigo-800 hover:bg-indigo-100"
                >
                  🖨️ {t("myTasks.officePrint")}
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
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <div className="relative my-6 w-full max-w-lg space-y-3 rounded-2xl bg-white p-5 shadow-xl">
            {/* ✕ bezárás — a mellé-kattintás szándékosan NEM zár (user-kérés) */}
            <button type="button" onClick={() => closeWorksheet()} aria-label="Bezárás" className="absolute right-2.5 top-2.5 z-10 rounded-lg px-2 py-0.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">✕</button>
            <h2 className="text-lg font-semibold">{t("myTasks.wsTitle", { title: wsTask.title })}</h2>
            {wsTask.worksheet_serial && (
              <p className="text-xs text-slate-500">{t("myTasks.wsSerial", { serial: wsTask.worksheet_serial })}</p>
            )}
            {timer && timer.state !== "none" && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl bg-slate-900 px-3 py-2 text-xs text-white">
                <span className={`h-2.5 w-2.5 rounded-full ${timer.state === "paused" ? "bg-amber-400" : timer.state === "running" ? "animate-pulse bg-emerald-400" : "bg-slate-500"}`} />
                <span className="font-medium">{timer.state === "paused" ? t("myTasks.timerPaused") : t("myTasks.timerRunning")}</span>
                <span className="text-slate-400">· {t("myTasks.timerHint")}</span>
                {timer.state !== "stopped" && (
                  <span className="ml-auto flex flex-wrap items-center gap-1">
                    {(["travel", "work", "wait"] as const).map((m) => (
                      <button key={m} type="button" onClick={() => void setTimerMode(m)} className={`rounded-lg px-2 py-1 ${(timer.mode ?? "work") === m ? "bg-indigo-500 text-white" : "bg-white/10 hover:bg-white/20"}`}>{t(`myTasks.mode.${m}`)}</button>
                    ))}
                    <button type="button" onClick={() => void toggleTimer()} className="rounded-lg bg-white/10 px-2.5 py-1 font-medium hover:bg-white/20">
                      {timer.state === "paused" ? t("myTasks.timerResume") : t("myTasks.timerPause")}
                    </button>
                  </span>
                )}
                {timer.state === "stopped" && (
                  timer.correction_status === "pending"
                    ? <span className="ml-auto text-amber-300">{t("myTasks.timerCorrectionPending")}</span>
                    : <button type="button" onClick={() => void requestCorrection()} className="ml-auto rounded-lg bg-white/10 px-2.5 py-1 font-medium hover:bg-white/20">{t("myTasks.timerCorrection")}</button>
                )}
              </div>
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
            {/* KSZ-en az ajánlat-fázisban a tételes munka felesleges: az ügyfél a
                konstrukciók közül választ (6bdaa987) — elfogadás után jön vissza. */}
            {!(wsTask?.worksheet_external && wsQuote.status !== "accepted") && (
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
            )}
            {wsTask?.worksheet_external && wsQuote.status !== "accepted" && (
              <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">{t("myTasks.wsQuoteOnlyOptions")}</p>
            )}

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

            {/* Gazdasági totálkár: az ajánlaton az ügyfél csak a bevizsgálási
                díj vagy a tulajdonjog-lemondás közül választhat */}
            <label className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm">
              <input
                type="checkbox"
                checked={ws.total_loss}
                onChange={(e) => setWs({ ...ws, total_loss: e.target.checked })}
                className="mt-0.5 h-4 w-4"
              />
              <span>
                ⚠️ {t("myTasks.wsTotalLoss")}
                <span className="block text-xs text-amber-800">{t("myTasks.wsTotalLossHint")}</span>
              </span>
            </label>

            <label className="block text-sm">
              👷 {t("myTasks.wsTechnician")}
              {/* A cég névsorából súg (d8bde9e6); új név beírható — mentéskor
                  automatikusan felkerül a listára. */}
              <SuggestInput
                value={ws.technician_name}
                onChange={(v) => setWs({ ...ws, technician_name: v })}
                options={myTechs}
                placeholder={t("myTasks.wsTechnicianPh")}
              />
              <span className="mt-0.5 block text-xs text-slate-500">{t("myTasks.wsTechnicianHint")}</span>
            </label>
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
              {t("myTasks.wsPublicNote")}
              <textarea
                value={ws.public_note}
                onChange={(e) => setWs({ ...ws, public_note: e.target.value })}
                rows={3}
                className="mt-1 w-full rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2"
                placeholder={t("myTasks.wsPublicNotePh")}
              />
              <span className="mt-0.5 block text-xs text-emerald-700">{t("myTasks.wsPublicNoteHint")}</span>
            </label>
            {/* Tudástár-mezők (687f2352): hibaleírás + megoldás — a megoldásból KB-bejegyzés lesz */}
            <div className="rounded-xl border border-sky-200 bg-slate-50 p-3">
              <p className="mb-2 text-xs font-medium text-sky-800">📚 {t("myTasks.wsKbTitle")}</p>
              <label className="block text-sm">
                {t("myTasks.wsProblem")}
                <textarea
                  value={ws.problem_text}
                  onChange={(e) => setWs({ ...ws, problem_text: e.target.value })}
                  rows={2}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                  placeholder={t("myTasks.wsProblemPh")}
                />
              </label>
              <label className="mt-2 block text-sm">
                {t("myTasks.wsSolution")}
                <textarea
                  value={ws.solution_text}
                  onChange={(e) => setWs({ ...ws, solution_text: e.target.value })}
                  rows={3}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                  placeholder={t("myTasks.wsSolutionPh")}
                />
              </label>
              <label className="mt-2 flex items-center gap-2 text-xs text-slate-600">
                <input type="checkbox" checked={ws.print_solution} onChange={(e) => setWs({ ...ws, print_solution: e.target.checked })} className="h-4 w-4" />
                {t("myTasks.wsPrintSolution")}
              </label>
              <p className="mt-1 text-xs text-slate-400">{t("myTasks.wsKbHint")}</p>
            </div>
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

            {/* 🔩 Beépített alkatrészek — csak saját autós raktárból, azonnali
                use-mozgás; a munkalap PDF-jén tételként, árral (B fázis) */}
            {myParts !== null && wsTask?.worksheet_serial && (
              <div className="rounded-xl border border-indigo-200 bg-slate-50 p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-800">{t("myTasks.wsParts")}</span>
                  <span className="text-[11px] text-slate-500">{t("myTasks.wsPartsHint")}</span>
                </div>
                {usedParts.length === 0 ? (
                  <p className="mb-2 text-xs text-slate-500">{t("myTasks.wsPartsEmpty")}</p>
                ) : (
                  <table className="mb-2 w-full text-sm">
                    <tbody>
                      {usedParts.map((u) => (
                        <tr key={u.movement_id} className="border-b border-slate-200">
                          <td className="py-1 pr-2"><span className="font-mono text-[11px] text-slate-400">{u.sku}</span> {u.name}{u.serial_no && <span className="ml-1 text-[11px] text-slate-400">· {u.serial_no}</span>}</td>
                          <td className="py-1 pr-2 text-right tabular-nums">{u.quantity} {u.unit}</td>
                          <td className="py-1 pr-2 text-right tabular-nums text-slate-500">{u.price_net != null ? `${u.price_net.toLocaleString("hu-HU")} Ft` : ""}</td>
                          <td className="py-1 text-right">
                            <button type="button" disabled={partsBusy} onClick={() => void uninstallPart(u)} className="px-1 text-slate-400 hover:text-red-600" title={t("myTasks.wsPartsRemove")}>🗑</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <div className="flex flex-wrap items-end gap-1.5">
                  <div className="min-w-[12rem] flex-1 text-sm">
                    <SearchSelect
                      items={myParts.map((s) => ({ id: s.part_id, label: s.name, badge: s.sku, sublabel: t("myTasks.wsPartsOnVehicle", { qty: s.quantity, unit: s.unit }) }))}
                      value={partPick.part_id}
                      onChange={(id) => setPartPick({ ...partPick, part_id: id })}
                      placeholder={t("myTasks.wsPartsPick")}
                    />
                  </div>
                  <input
                    type="number" min={0} step="any"
                    value={partPick.quantity}
                    onChange={(e) => setPartPick({ ...partPick, quantity: e.target.value })}
                    className="w-16 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <input
                    value={partPick.serial_no}
                    onChange={(e) => setPartPick({ ...partPick, serial_no: e.target.value })}
                    placeholder={t("myTasks.wsPartsSerial")}
                    className="w-32 rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    disabled={partsBusy || !partPick.part_id}
                    onClick={() => void installPart()}
                    className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {t("myTasks.wsPartsAdd")}
                  </button>
                </div>
                {kits.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <select value={kitPick} onChange={(e) => setKitPick(e.target.value)} className="min-w-[12rem] flex-1 rounded-lg border border-slate-300 px-2 py-1.5 text-sm">
                      <option value="">{t("myTasks.wsKit")}</option>
                      {kits.map((k) => <option key={k.id} value={k.id}>{k.name} ({k.items.map((i) => `${i.sku}×${i.quantity}`).join(", ")})</option>)}
                    </select>
                    <button type="button" disabled={partsBusy || !kitPick} onClick={() => void installKit()} className="rounded-lg border border-emerald-600 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-50 disabled:opacity-50">{t("myTasks.wsKitAdd")}</button>
                  </div>
                )}
                {pendingParts > 0 && <p className="mt-1 text-[11px] text-amber-700">📴 {t("myTasks.wsPartsPending", { count: pendingParts })}</p>}
              </div>
            )}

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
                onClick={() => closeWorksheet()}
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
      <PhotoLightbox src={lightbox} onClose={() => setLightbox(null)} />
    </AppShell>
  );
}
