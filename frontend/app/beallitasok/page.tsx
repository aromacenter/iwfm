"use client";

/** Beállítások (admin): email-fiók az értesítésekhez + skillek + AI integráció. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";

interface EmailSettings {
  enabled: boolean;
  host: string | null;
  port: number;
  username: string | null;
  has_password: boolean;
  from_address: string | null;
  use_tls: boolean;
}

interface Skill {
  id: number;
  name: string;
}

interface AISettings {
  active_provider: "none" | "anthropic" | "gemini";
  has_anthropic_key: boolean;
  anthropic_model: string;
  has_gemini_key: boolean;
  gemini_model: string;
  assign_prompt: string;
  assign_prompt_is_custom: boolean;
  default_assign_prompt: string;
}

interface WorksheetSettings {
  company_name: string | null;
  company_address: string | null;
  footer_text: string | null;
  accent_color: string;
  show_materials: boolean;
  show_hours: boolean;
  show_client_signature: boolean;
  show_comments: boolean;
  has_logo: boolean;
}

const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";

const SETTINGS_TABS = [
  ["worksheet", "🧾"],
  ["billing", "💳"],
  ["email", "✉️"],
  ["notifications", "🔔"],
  ["ai", "✨"],
  ["support", "💬"],
  ["skills", "🎓"],
  ["permissions", "🔐"],
  ["data", "🗄️"],
] as const;

interface EmailTpl {
  id: string;
  name: string;
  subject: string;
  body: string;
}

/** Ismert Anthropic modellek a legördülőhöz — egyéni név továbbra is megadható. */
const ANTHROPIC_MODELS: { id: string; label: string; tagKey?: string }[] = [
  { id: "claude-opus-4-8", label: "Claude Opus 4.8", tagKey: "settings.modelTagDefault" },
  { id: "claude-opus-5", label: "Claude Opus 5", tagKey: "settings.modelTagSmart" },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5", tagKey: "settings.modelTagFast" },
  { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5", tagKey: "settings.modelTagCheapest" },
];

export default function BeallitasokPage() {
  const { t } = useT();
  const { confirm, prompt, toast } = useUI();

  // --- Fülek: a beállítások logikus alcsoportokban ---
  const [tab, setTabState] = useState<(typeof SETTINGS_TABS)[number][0]>(() => {
    if (typeof window !== "undefined") {
      const saved = sessionStorage.getItem("iwfm-settings-tab");
      if (saved && SETTINGS_TABS.some(([k]) => k === saved)) {
        return saved as (typeof SETTINGS_TABS)[number][0];
      }
    }
    return "worksheet";
  });
  const setTab = (k: (typeof SETTINGS_TABS)[number][0]) => {
    setTabState(k);
    try { sessionStorage.setItem("iwfm-settings-tab", k); } catch {}
  };
  const cardCls = (own: string, base: string) =>
    `${tab === own ? "" : "hidden "}${base}`;

  // --- E-mail sablonok (automatizálásokhoz) ---
  const [templates, setTemplates] = useState<EmailTpl[]>([]);
  const [tplForm, setTplForm] = useState<{ id: string; name: string; subject: string; body: string } | null>(null);
  const [tplBusy, setTplBusy] = useState(false);
  const [tplMsg, setTplMsg] = useState<string | null>(null);

  const loadTemplates = () => {
    api.get<EmailTpl[]>("/api/automation/templates").then(setTemplates).catch(() => {});
  };
  useEffect(loadTemplates, []);

  async function saveTemplate(e: React.FormEvent) {
    e.preventDefault();
    if (!tplForm) return;
    setTplBusy(true);
    setTplMsg(null);
    try {
      const body = { name: tplForm.name, subject: tplForm.subject, body: tplForm.body };
      if (tplForm.id) await api.patch(`/api/automation/templates/${tplForm.id}`, body);
      else await api.post("/api/automation/templates", body);
      setTplForm(null);
      loadTemplates();
    } catch (err) {
      setTplMsg(errorMessage(err));
    } finally {
      setTplBusy(false);
    }
  }

  async function deleteTemplate(tpl: EmailTpl) {
    if (!(await confirm(t("settings.tplDeleteConfirm", { name: tpl.name })))) return;
    try {
      await api.delete(`/api/automation/templates/${tpl.id}`);
      loadTemplates();
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  // --- AI sablon-generálás: leírásból tölti ki a név/tárgy/szöveg mezőket ---
  const [tplAiDesc, setTplAiDesc] = useState("");
  const [tplAiTrigger, setTplAiTrigger] = useState("");
  const [tplAiBusy, setTplAiBusy] = useState(false);
  const [autoTriggers, setAutoTriggers] = useState<Record<string, string[]>>({});
  useEffect(() => {
    api.get<{ triggers: Record<string, string[]> }>("/api/automation/triggers")
      .then((d) => setAutoTriggers(d.triggers || {}))
      .catch(() => {});
  }, []);

  async function generateTemplateAi() {
    if (!tplForm || tplAiDesc.trim().length < 3) return;
    setTplAiBusy(true);
    setTplMsg(null);
    try {
      const res = await api.post<{ name: string; subject: string; body: string }>(
        "/api/automation/templates/generate",
        { description: tplAiDesc.trim(), trigger: tplAiTrigger || null },
      );
      setTplForm((f) => (f ? { ...f, name: f.name || res.name, subject: res.subject, body: res.body } : f));
    } catch (err) {
      setTplMsg(errorMessage(err));
    } finally {
      setTplAiBusy(false);
    }
  }

  // --- Email ---
  const [email, setEmail] = useState({
    enabled: false,
    host: "",
    port: 587,
    username: "",
    password: "",
    from_address: "",
    use_tls: true,
  });
  const [hasPassword, setHasPassword] = useState(false);
  const [emailMsg, setEmailMsg] = useState<string | null>(null);
  const [testTo, setTestTo] = useState("");
  const [busy, setBusy] = useState(false);

  // --- Skillek ---
  const [skills, setSkills] = useState<Skill[]>([]);
  const [newSkill, setNewSkill] = useState("");
  const [skillMsg, setSkillMsg] = useState<string | null>(null);

  // --- AI ---
  const [ai, setAi] = useState({
    active_provider: "none" as "none" | "anthropic" | "gemini",
    anthropic_key: "",
    anthropic_model: "claude-opus-4-8",
    gemini_key: "",
    gemini_model: "gemini-3.5-flash",
  });
  const [aiHasKeys, setAiHasKeys] = useState({ anthropic: false, gemini: false });
  const [aiMsg, setAiMsg] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  // Anthropic modell: legördülő az ismert modellekkel; egyéni név is megadható
  const [anthroCustom, setAnthroCustom] = useState(false);
  const anthroKnown = ANTHROPIC_MODELS.some((m) => m.id === ai.anthropic_model);
  const [assignPrompt, setAssignPrompt] = useState("");
  const [promptIsCustom, setPromptIsCustom] = useState(false);
  const [defaultPrompt, setDefaultPrompt] = useState("");

  // --- Jogosultságok (szerepkör × funkció mátrix) ---
  const [permRoles, setPermRoles] = useState<string[]>([]);
  const [permFeatures, setPermFeatures] = useState<string[]>([]);
  const [permMatrix, setPermMatrix] = useState<Record<string, string[]>>({});
  const [permMsg, setPermMsg] = useState<string | null>(null);
  const [permBusy, setPermBusy] = useState(false);

  // --- Billingó számlázó ---
  const [billingo, setBillingo] = useState({
    enabled: false, api_key: "", block_id: "", test_mode: true,
    pc_api_key: "", pc_block_id: "", pc_test_mode: true,
  });
  const [billingoHasKey, setBillingoHasKey] = useState(false);
  const [billingoPcHasKey, setBillingoPcHasKey] = useState(false);
  const [wipeCounts, setWipeCounts] = useState<Record<string, number> | null>(null);
  const [wipeBusy, setWipeBusy] = useState(false);
  const [billingoMsg, setBillingoMsg] = useState<string | null>(null);
  const [billingoBusy, setBillingoBusy] = useState(false);
  const [knowledgeBase, setKnowledgeBase] = useState("");
  const [autoKb, setAutoKb] = useState(true);
  const [kbBusy, setKbBusy] = useState(false);

  // --- Munkalap-PDF testreszabás ---
  const [ws, setWs] = useState({
    company_name: "",
    company_address: "",
    footer_text: "",
    accent_color: "#1e40af",
    show_materials: true,
    show_hours: true,
    show_client_signature: true,
    show_comments: true,
  });
  const [wsHasLogo, setWsHasLogo] = useState(false);
  const [wsLogo, setWsLogo] = useState<string | null>(null); // újonnan feltöltött data URL
  const [wsRemoveLogo, setWsRemoveLogo] = useState(false);
  const [wsLogoVer, setWsLogoVer] = useState(0); // cache-bust a logó-előnézethez
  const [wsMsg, setWsMsg] = useState<string | null>(null);
  const [wsBusy, setWsBusy] = useState(false);

  const load = useCallback(() => {
    api
      .get<EmailSettings>("/api/settings/email")
      .then((s) => {
        setEmail({
          enabled: s.enabled,
          host: s.host ?? "",
          port: s.port,
          username: s.username ?? "",
          password: "",
          from_address: s.from_address ?? "",
          use_tls: s.use_tls,
        });
        setHasPassword(s.has_password);
      })
      .catch(() => {});
    api.get<Skill[]>("/api/settings/skills").then(setSkills).catch(() => {});
    api
      .get<AISettings>("/api/settings/ai")
      .then((s) => {
        setAi((prev) => ({
          ...prev,
          active_provider: s.active_provider,
          anthropic_model: s.anthropic_model,
          gemini_model: s.gemini_model,
          anthropic_key: "",
          gemini_key: "",
        }));
        setAiHasKeys({ anthropic: s.has_anthropic_key, gemini: s.has_gemini_key });
        setAssignPrompt(s.assign_prompt);
        setPromptIsCustom(s.assign_prompt_is_custom);
        setDefaultPrompt(s.default_assign_prompt);
      })
      .catch(() => {});
    api
      .get<{ roles: string[]; features: string[]; matrix: Record<string, string[]> }>(
        "/api/settings/permissions",
      )
      .then((p) => {
        setPermRoles(p.roles);
        setPermFeatures(p.features);
        setPermMatrix(p.matrix);
      })
      .catch(() => {});
    api
      .get<{
        enabled: boolean; has_api_key: boolean; block_id: number | null; test_mode: boolean;
        pc_has_api_key: boolean; pc_block_id: number | null; pc_test_mode: boolean;
      }>(
        "/api/settings/billingo",
      )
      .then((s) => {
        setBillingo({
          enabled: s.enabled,
          api_key: "",
          block_id: s.block_id != null ? String(s.block_id) : "",
          test_mode: s.test_mode,
          pc_api_key: "",
          pc_block_id: s.pc_block_id != null ? String(s.pc_block_id) : "",
          pc_test_mode: s.pc_test_mode,
        });
        setBillingoHasKey(s.has_api_key);
        setBillingoPcHasKey(s.pc_has_api_key);
      })
      .catch(() => {});
    api
      .get<{ knowledge_base: string | null; auto_kb: boolean }>("/api/settings/support")
      .then((s) => {
        setKnowledgeBase(s.knowledge_base ?? "");
        setAutoKb(s.auto_kb);
      })
      .catch(() => {});
    api.get<Record<string, number>>("/api/admin/wipe/counts").then(setWipeCounts).catch(() => {});
    api
      .get<WorksheetSettings>("/api/settings/worksheet")
      .then((s) => {
        setWs({
          company_name: s.company_name ?? "",
          company_address: s.company_address ?? "",
          footer_text: s.footer_text ?? "",
          accent_color: s.accent_color,
          show_materials: s.show_materials,
          show_hours: s.show_hours,
          show_client_signature: s.show_client_signature,
          show_comments: s.show_comments,
        });
        setWsHasLogo(s.has_logo);
        setWsLogo(null);
        setWsRemoveLogo(false);
        setWsLogoVer((v) => v + 1);
      })
      .catch(() => {});
  }, []);
  useEffect(load, [load]);

  async function saveEmail(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setEmailMsg(null);
    try {
      await api.put("/api/settings/email", {
        ...email,
        host: email.host || null,
        username: email.username || null,
        password: email.password || null,
        from_address: email.from_address || null,
      });
      setEmailMsg(t("common.saved"));
      setEmail((s) => ({ ...s, password: "" }));
      load();
    } catch (err) {
      setEmailMsg(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    if (!testTo) return;
    setBusy(true);
    setEmailMsg(null);
    try {
      await api.post("/api/settings/email/test", { to: testTo });
      setEmailMsg(t("settings.testSent", { to: testTo }));
    } catch (err) {
      setEmailMsg(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function addSkill(e: React.FormEvent) {
    e.preventDefault();
    if (!newSkill.trim()) return;
    setSkillMsg(null);
    try {
      await api.post("/api/settings/skills", { name: newSkill.trim() });
      setNewSkill("");
      load();
    } catch (err) {
      setSkillMsg(errorMessage(err));
    }
  }

  async function removeSkill(skill: Skill) {
    if (!(await confirm(t("settings.deleteSkillConfirm", { name: skill.name })))) return;
    try {
      await api.delete(`/api/settings/skills/${skill.id}`);
      load();
    } catch (err) {
      setSkillMsg(errorMessage(err));
    }
  }

  async function saveAi() {
    setAiBusy(true);
    setAiMsg(null);
    try {
      await api.put("/api/settings/ai", {
        active_provider: ai.active_provider,
        anthropic_key: ai.anthropic_key || null,
        anthropic_model: ai.anthropic_model,
        gemini_key: ai.gemini_key || null,
        gemini_model: ai.gemini_model,
        assign_prompt: assignPrompt || null,
      });
      setAiMsg(t("common.saved"));
      load();
    } catch (err) {
      setAiMsg(errorMessage(err));
    } finally {
      setAiBusy(false);
    }
  }

  async function testAi(provider: "anthropic" | "gemini") {
    setAiBusy(true);
    setAiMsg(null);
    try {
      const res = await api.post<{ reply: string }>("/api/settings/ai/test", { provider });
      setAiMsg(
        t("settings.aiReplied", {
          provider: provider === "anthropic" ? "Claude" : "Gemini",
          reply: res.reply.trim(),
        })
      );
    } catch (err) {
      setAiMsg(errorMessage(err));
    } finally {
      setAiBusy(false);
    }
  }

  function togglePerm(role: string, feature: string) {
    setPermMatrix((m) => {
      const current = m[role] ?? [];
      const next = current.includes(feature)
        ? current.filter((f) => f !== feature)
        : [...current, feature];
      return { ...m, [role]: next };
    });
  }

  async function savePermissions() {
    setPermBusy(true);
    setPermMsg(null);
    try {
      await api.put("/api/settings/permissions", { matrix: permMatrix });
      setPermMsg(t("common.saved"));
    } catch (err) {
      setPermMsg(errorMessage(err));
    } finally {
      setPermBusy(false);
    }
  }

  async function saveKnowledgeBase(e: React.FormEvent) {
    e.preventDefault();
    setKbBusy(true);
    try {
      await api.put("/api/settings/support", { knowledge_base: knowledgeBase || null, auto_kb: autoKb });
      toast(t("settings.kbSaved"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setKbBusy(false);
    }
  }

  async function saveBillingo(e: React.FormEvent) {
    e.preventDefault();
    setBillingoBusy(true);
    setBillingoMsg(null);
    try {
      await api.put("/api/settings/billingo", {
        enabled: billingo.enabled,
        api_key: billingo.api_key || null,
        block_id: billingo.block_id ? Number(billingo.block_id) : null,
        test_mode: billingo.test_mode,
        pc_api_key: billingo.pc_api_key || null,
        pc_block_id: billingo.pc_block_id ? Number(billingo.pc_block_id) : null,
        pc_test_mode: billingo.pc_test_mode,
      });
      setBillingoMsg(t("common.saved"));
      setBillingo((s) => ({ ...s, api_key: "", pc_api_key: "" }));
      load();
    } catch (err) {
      setBillingoMsg(errorMessage(err));
    } finally {
      setBillingoBusy(false);
    }
  }

  const [notif, setNotif] = useState({
    daily_enabled: false, recipients: "", send_hour: "6",
    weekly_backup: false, auto_receipt: false,
    wa_enabled: false, wa_phone_id: "", wa_recipients: "", wa_token: "", wa_token_set: false,
    tg_enabled: false, tg_chat_ids: "", tg_token: "", tg_token_set: false,
  });
  const [notifMsg, setNotifMsg] = useState<string | null>(null);
  const [notifBusy, setNotifBusy] = useState(false);

  useEffect(() => {
    api
      .get<{
        daily_enabled: boolean; recipients: string | null; send_hour: number;
        weekly_backup: boolean; auto_receipt: boolean;
        wa_enabled: boolean; wa_phone_id: string | null; wa_recipients: string | null; wa_token_set: boolean;
        tg_enabled: boolean; tg_chat_ids: string | null; tg_token_set: boolean;
      }>("/api/settings/notifications")
      .then((s) => setNotif({
        daily_enabled: s.daily_enabled,
        recipients: s.recipients ?? "",
        send_hour: String(s.send_hour),
        weekly_backup: s.weekly_backup,
        auto_receipt: s.auto_receipt,
        wa_enabled: s.wa_enabled, wa_phone_id: s.wa_phone_id ?? "",
        wa_recipients: s.wa_recipients ?? "", wa_token: "", wa_token_set: s.wa_token_set,
        tg_enabled: s.tg_enabled, tg_chat_ids: s.tg_chat_ids ?? "",
        tg_token: "", tg_token_set: s.tg_token_set,
      }))
      .catch(() => {});
  }, []);

  async function saveNotifications(e: React.FormEvent) {
    e.preventDefault();
    setNotifBusy(true);
    setNotifMsg(null);
    try {
      await api.put("/api/settings/notifications", {
        daily_enabled: notif.daily_enabled,
        recipients: notif.recipients || null,
        send_hour: Number(notif.send_hour) || 6,
        weekly_backup: notif.weekly_backup,
        auto_receipt: notif.auto_receipt,
        wa_enabled: notif.wa_enabled,
        wa_phone_id: notif.wa_phone_id || null,
        wa_recipients: notif.wa_recipients || null,
        wa_token: notif.wa_token || null,
        tg_enabled: notif.tg_enabled,
        tg_chat_ids: notif.tg_chat_ids || null,
        tg_token: notif.tg_token || null,
      });
      setNotif((n) => ({
        ...n,
        wa_token: "", wa_token_set: n.wa_token_set || !!(n.wa_token && n.wa_token !== "-"),
        tg_token: "", tg_token_set: n.tg_token_set || !!(n.tg_token && n.tg_token !== "-"),
      }));
      setNotifMsg(t("common.saved"));
    } catch (err) {
      setNotifMsg(errorMessage(err));
    } finally {
      setNotifBusy(false);
    }
  }

  async function testWhatsApp() {
    setNotifBusy(true);
    setNotifMsg(null);
    try {
      const res = await api.post<{ to: string }>("/api/settings/notifications/whatsapp-test", {});
      setNotifMsg(t("notif.waTestSent", { to: res.to }));
    } catch (err) {
      setNotifMsg(errorMessage(err));
    } finally {
      setNotifBusy(false);
    }
  }

  async function testTelegram() {
    setNotifBusy(true);
    setNotifMsg(null);
    try {
      const res = await api.post<{ chat_id: string }>("/api/settings/notifications/telegram-test", {});
      setNotifMsg(t("notif.tgTestSent", { to: res.chat_id }));
    } catch (err) {
      setNotifMsg(errorMessage(err));
    } finally {
      setNotifBusy(false);
    }
  }

  async function testDigest() {
    setNotifBusy(true);
    setNotifMsg(null);
    try {
      const res = await api.post<{ sent: number }>("/api/settings/notifications/test");
      setNotifMsg(t("notif.testSent", { count: res.sent }));
    } catch (err) {
      setNotifMsg(errorMessage(err));
    } finally {
      setNotifBusy(false);
    }
  }

  const [backupBusy, setBackupBusy] = useState(false);

  async function downloadBackup() {
    setBackupBusy(true);
    try {
      const stamp = new Date().toISOString().slice(0, 10);
      await downloadFile("/api/admin/backup", `iwfm-mentes-${stamp}.json.gz`);
      toast(t("backup.done"), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setBackupBusy(false);
    }
  }

  const [syncBusy, setSyncBusy] = useState(false);

  interface SyncStatus {
    running: boolean;
    progress: string;
    report: Record<string, Record<string, unknown>> | null;
    error: string | null;
  }

  function renderSyncReport(report: Record<string, Record<string, unknown>>, dryRun: boolean) {
    const parts: string[] = [];
    for (const [company, rep] of Object.entries(report)) {
      const label = company === "pc" ? "Premium Caffe" : "X-Presso";
      if (rep.skipped) {
        parts.push(`${label}: ${rep.skipped}`);
      } else {
        const updated = (rep.updated ?? rep.would_update ?? 0) as number;
        parts.push(
          `${label}: ${rep.billingo_partners} vevő a Billingóban, ${rep.matched} párosítva, ${updated} ${dryRun ? "frissülne" : "frissítve"}`,
        );
        const samples = (rep.samples as string[]) ?? [];
        if (samples.length) parts.push("· " + samples.join("\n· "));
      }
    }
    return parts.join("\n");
  }

  // A szinkron háttérben fut (80+ Billingó-hívás) — indítás után pollozzuk.
  async function billingoPartnerSync(dryRun: boolean) {
    if (!dryRun && !(await confirm(t("settings.billingoSyncConfirm")))) return;
    setSyncBusy(true);
    setBillingoMsg(t("settings.billingoSyncStarted"));
    try {
      await api.post("/api/admin/billingo-sync", { dry_run: dryRun });
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2500));
        const st = await api.get<SyncStatus>("/api/admin/billingo-sync");
        if (st.running) {
          setBillingoMsg(`${t("settings.billingoSyncRunning")} ${st.progress}`);
          continue;
        }
        if (st.error) setBillingoMsg(`${t("settings.billingoSyncFailed")} ${st.error}`);
        else if (st.report) setBillingoMsg(renderSyncReport(st.report, dryRun));
        break;
      }
    } catch (err) {
      setBillingoMsg(errorMessage(err));
    } finally {
      setSyncBusy(false);
    }
  }

  async function wipe(entity: "assets" | "products" | "partners") {
    const count = wipeCounts?.[entity] ?? 0;
    const extra = entity === "partners"
      ? "\n" + t("danger.partnersWarning", { settlements: wipeCounts?.settlements ?? 0 })
      : "";
    if (!(await confirm(t("danger.confirm", { count }) + extra))) return;
    const word = await prompt(t("danger.typeWord"));
    if (word === null) return;
    if (word.trim().toUpperCase() !== "TORLES") {
      toast(t("danger.wrongWord"), "error");
      return;
    }
    setWipeBusy(true);
    try {
      const res = await api.post<{ deleted: number }>("/api/admin/wipe", {
        entity,
        confirm: "TORLES",
      });
      toast(t("danger.done", { count: res.deleted }), "success");
      api.get<Record<string, number>>("/api/admin/wipe/counts").then(setWipeCounts).catch(() => {});
    } catch (err) {
      toast(errorMessage(err), "error");
    } finally {
      setWipeBusy(false);
    }
  }

  async function testBillingo(company?: "pc") {
    setBillingoBusy(true);
    setBillingoMsg(null);
    try {
      const res = await api.post<{ ok: boolean; blocks: { id: number; name: string }[] }>(
        `/api/settings/billingo/test${company ? `?company=${company}` : ""}`,
      );
      setBillingoMsg(
        (company === "pc" ? "Premium Caffe: " : "X-Presso: ") +
        t("settings.billingoTestOk", {
          blocks: res.blocks.map((b) => `${b.name} (#${b.id})`).join(", ") || "—",
        }),
      );
    } catch (err) {
      setBillingoMsg(errorMessage(err));
    } finally {
      setBillingoBusy(false);
    }
  }

  function onLogoFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setWsLogo(reader.result as string);
      setWsRemoveLogo(false);
    };
    reader.readAsDataURL(file);
  }

  async function saveWorksheet(e: React.FormEvent) {
    e.preventDefault();
    setWsBusy(true);
    setWsMsg(null);
    try {
      await api.put("/api/settings/worksheet", {
        company_name: ws.company_name || null,
        company_address: ws.company_address || null,
        footer_text: ws.footer_text || null,
        accent_color: ws.accent_color,
        show_materials: ws.show_materials,
        show_hours: ws.show_hours,
        show_client_signature: ws.show_client_signature,
        show_comments: ws.show_comments,
        logo: wsLogo,
        remove_logo: wsRemoveLogo,
      });
      setWsMsg(t("common.saved"));
      load();
    } catch (err) {
      setWsMsg(errorMessage(err));
    } finally {
      setWsBusy(false);
    }
  }

  const wsToggles = [
    ["show_materials", "wsShowMaterials"],
    ["show_hours", "wsShowHours"],
    ["show_client_signature", "wsShowClientSig"],
    ["show_comments", "wsShowComments"],
  ] as const;

  return (
    <AppShell>
      <h1 className="mb-4 text-xl font-bold">{t("settings.title")}</h1>

      {/* Alfülek: a beállítások logikus csoportokban */}
      <div className="mb-5 flex flex-wrap gap-1.5">
        {SETTINGS_TABS.map(([k, icon]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
              tab === k
                ? "bg-indigo-600 text-white"
                : "border border-slate-300 bg-white text-slate-600 hover:bg-slate-100"
            }`}
          >
            {icon} {t(`settings.tabs.${k}`)}
          </button>
        ))}
      </div>

      {/* Jogosultságok — szerepkör × funkció mátrix */}
      {permRoles.length > 0 && (
        <div className={cardCls("permissions", "mb-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}>
          <h2 className="font-semibold">{t("settings.permTitle")}</h2>
          <p className="mb-3 text-xs text-slate-500">{t("settings.permHint")}</p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
                  <th className="px-3 py-2">{t("settings.permFeature")}</th>
                  <th className="px-3 py-2 text-center">{t("roles.admin")}</th>
                  {permRoles.map((r) => (
                    <th key={r} className="px-3 py-2 text-center">{t(`roles.${r}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {permFeatures.map((f) => (
                  <tr key={f} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-2">
                      {t(`features.${f}`)}
                      {f === "delete" && (
                        <span className="ml-2 rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-700">
                          {t("settings.permDeleteHint")}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <input type="checkbox" checked disabled className="h-4 w-4 opacity-50" />
                    </td>
                    {permRoles.map((r) => (
                      <td key={r} className="px-3 py-2 text-center">
                        <input
                          type="checkbox"
                          checked={(permMatrix[r] ?? []).includes(f)}
                          onChange={() => togglePerm(r, f)}
                          className="h-4 w-4"
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={savePermissions}
              disabled={permBusy}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {permBusy ? t("common.saving") : t("common.save")}
            </button>
            {permMsg && <span className="text-sm text-slate-600">{permMsg}</span>}
          </div>
        </div>
      )}

      <div className="flex max-w-3xl flex-col gap-4">
        {/* Munkalap-PDF testreszabás */}
        <form
          onSubmit={saveWorksheet}
          className={cardCls("worksheet", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}
        >
          <h2 className="font-semibold">{t("settings.wsTitle")}</h2>
          <p className="text-xs text-slate-500">{t("settings.wsHint")}</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              {t("settings.wsCompanyName")}
              <input value={ws.company_name} onChange={(e) => setWs({ ...ws, company_name: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("settings.wsCompanyAddress")}
              <input value={ws.company_address} onChange={(e) => setWs({ ...ws, company_address: e.target.value })} className={inputCls} />
            </label>
          </div>
          <label className="block text-sm">
            {t("settings.wsFooter")}
            <input value={ws.footer_text} onChange={(e) => setWs({ ...ws, footer_text: e.target.value })} className={inputCls} />
          </label>
          <div className="flex items-center gap-3">
            <span className="text-sm">{t("settings.wsAccent")}</span>
            <input type="color" value={ws.accent_color} onChange={(e) => setWs({ ...ws, accent_color: e.target.value })} className="h-8 w-12 rounded border border-slate-300" />
            <span className="font-mono text-xs text-slate-500">{ws.accent_color}</span>
          </div>
          <div className="space-y-1">
            <span className="block text-sm">{t("settings.wsLogo")}</span>
            <div className="flex items-center gap-3">
              {wsLogo ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={wsLogo} alt="logo" className="h-12 w-auto rounded border border-slate-200" />
              ) : wsHasLogo && !wsRemoveLogo ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={`/api/settings/worksheet/logo?v=${wsLogoVer}`} alt="logo" className="h-12 w-auto rounded border border-slate-200" />
              ) : (
                <span className="text-xs text-slate-400">{t("settings.wsNoLogo")}</span>
              )}
              <input type="file" accept="image/png,image/jpeg" onChange={onLogoFile} className="text-xs" />
              {(wsHasLogo || wsLogo) && !wsRemoveLogo && (
                <button type="button" onClick={() => { setWsLogo(null); setWsRemoveLogo(true); }} className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100">
                  {t("settings.wsRemoveLogo")}
                </button>
              )}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {wsToggles.map(([key, lbl]) => (
              <label key={key} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={ws[key]} onChange={(e) => setWs({ ...ws, [key]: e.target.checked })} className="h-4 w-4" />
                {t(`settings.${lbl}`)}
              </label>
            ))}
          </div>
          {wsMsg && <p className="text-sm text-slate-600">{wsMsg}</p>}
          <button disabled={wsBusy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            {wsBusy ? t("common.saving") : t("common.save")}
          </button>
        </form>

        {/* Billingó számlázó */}
        <form
          onSubmit={saveBillingo}
          className={cardCls("billing", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}
        >
          <h2 className="font-semibold">{t("settings.billingoTitle")}</h2>
          <p className="text-xs text-slate-500">{t("settings.billingoHint")}</p>
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={billingo.enabled}
              onChange={(e) => setBillingo({ ...billingo, enabled: e.target.checked })}
              className="h-4 w-4"
            />
            {t("settings.billingoEnabled")}
          </label>
          <fieldset className="space-y-3 rounded-xl border border-slate-200 p-3">
            <legend className="px-1 text-xs font-semibold uppercase text-slate-400">X-Presso Coffee Kft.</legend>
            <label className="block text-sm">
              {t("settings.billingoApiKey")} {billingoHasKey && <span className="text-emerald-600">✓</span>}
              <input
                type="password"
                value={billingo.api_key}
                onChange={(e) => setBillingo({ ...billingo, api_key: e.target.value })}
                placeholder={billingoHasKey ? "••••••••" : ""}
                className={inputCls}
              />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("settings.billingoBlock")}
                <input
                  type="number"
                  min={1}
                  value={billingo.block_id}
                  onChange={(e) => setBillingo({ ...billingo, block_id: e.target.value })}
                  className={inputCls}
                />
              </label>
              <label className="flex items-center gap-2 text-sm sm:mt-6">
                <input
                  type="checkbox"
                  checked={billingo.test_mode}
                  onChange={(e) => setBillingo({ ...billingo, test_mode: e.target.checked })}
                  className="h-4 w-4"
                />
                {t("settings.billingoTestMode")}
              </label>
            </div>
          </fieldset>
          <fieldset className="space-y-3 rounded-xl border border-slate-200 p-3">
            <legend className="px-1 text-xs font-semibold uppercase text-slate-400">Premium Caffe Kft.</legend>
            <label className="block text-sm">
              {t("settings.billingoApiKey")} {billingoPcHasKey && <span className="text-emerald-600">✓</span>}
              <input
                type="password"
                value={billingo.pc_api_key}
                onChange={(e) => setBillingo({ ...billingo, pc_api_key: e.target.value })}
                placeholder={billingoPcHasKey ? "••••••••" : ""}
                className={inputCls}
              />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("settings.billingoBlock")}
                <input
                  type="number"
                  min={1}
                  value={billingo.pc_block_id}
                  onChange={(e) => setBillingo({ ...billingo, pc_block_id: e.target.value })}
                  className={inputCls}
                />
              </label>
              <label className="flex items-center gap-2 text-sm sm:mt-6">
                <input
                  type="checkbox"
                  checked={billingo.pc_test_mode}
                  onChange={(e) => setBillingo({ ...billingo, pc_test_mode: e.target.checked })}
                  className="h-4 w-4"
                />
                {t("settings.billingoTestMode")}
              </label>
            </div>
          </fieldset>
          <p className="text-xs text-slate-500">{t("settings.billingoCompanyHint")}</p>
          {(!billingo.test_mode || !billingo.pc_test_mode) && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {t("settings.billingoLiveWarning")}
            </p>
          )}
          {billingoMsg && <p className="whitespace-pre-wrap text-sm text-slate-600">{billingoMsg}</p>}
          <div className="flex gap-2">
            <button disabled={billingoBusy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
              {billingoBusy ? t("common.saving") : t("common.save")}
            </button>
            <button type="button" onClick={() => testBillingo()} disabled={billingoBusy} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50">
              {t("settings.billingoTest")} (X-Presso)
            </button>
            <button type="button" onClick={() => testBillingo("pc")} disabled={billingoBusy} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50">
              {t("settings.billingoTest")} (Premium)
            </button>
            <button type="button" onClick={() => billingoPartnerSync(true)} disabled={syncBusy} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50">
              {syncBusy ? t("common.loading") : t("settings.billingoSyncDry")}
            </button>
            <button type="button" onClick={() => billingoPartnerSync(false)} disabled={syncBusy} className="rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-2 text-sm font-medium text-emerald-800 hover:bg-emerald-100 disabled:opacity-50">
              {t("settings.billingoSyncApply")}
            </button>
          </div>
        </form>

        {/* Ügyfél-támogatás tudásbázis (QR-oldal AI chat) */}
        <form
          onSubmit={saveKnowledgeBase}
          className={cardCls("support", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}
        >
          <h2 className="font-semibold">{t("settings.kbTitle")}</h2>
          <p className="text-xs text-slate-500">{t("settings.kbHint")}</p>
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={autoKb}
              onChange={(e) => setAutoKb(e.target.checked)}
              className="h-4 w-4"
            />
            {t("settings.kbAuto")}
          </label>
          <p className="text-xs text-slate-500">{t("settings.kbAutoHint")}</p>
          <textarea
            value={knowledgeBase}
            onChange={(e) => setKnowledgeBase(e.target.value)}
            rows={10}
            placeholder={t("settings.kbPlaceholder")}
            className={`${inputCls} font-mono text-xs`}
          />
          <button disabled={kbBusy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
            {kbBusy ? t("common.saving") : t("common.save")}
          </button>
        </form>

        {/* Email értesítések */}
        <form
          onSubmit={saveEmail}
          className={cardCls("email", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}
        >
          <h2 className="font-semibold">{t("settings.emailTitle")}</h2>
          <p className="text-xs text-slate-500">{t("settings.emailHint")}</p>
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={email.enabled}
              onChange={(e) => setEmail({ ...email, enabled: e.target.checked })}
              className="h-4 w-4"
            />
            {t("settings.emailEnabled")}
          </label>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="col-span-2 block text-sm">
              {t("settings.smtpHost")}
              <input
                value={email.host}
                onChange={(e) => setEmail({ ...email, host: e.target.value })}
                placeholder={t("settings.smtpHostPlaceholder")}
                className={inputCls}
              />
            </label>
            <label className="block text-sm">
              {t("settings.port")}
              <input
                type="number"
                value={email.port}
                onChange={(e) => setEmail({ ...email, port: Number(e.target.value) })}
                className={inputCls}
              />
            </label>
          </div>
          <label className="block text-sm">
            {t("settings.username")}
            <input
              value={email.username}
              onChange={(e) => setEmail({ ...email, username: e.target.value })}
              placeholder={t("settings.usernamePlaceholder")}
              className={inputCls}
            />
          </label>
          <label className="block text-sm">
            {t("settings.passwordLabel")}{" "}
            {hasPassword && <span className="text-slate-400">{t("settings.passwordSaved")}</span>}
            <input
              type="password"
              value={email.password}
              onChange={(e) => setEmail({ ...email, password: e.target.value })}
              placeholder={hasPassword ? "••••••••" : t("settings.passwordPlaceholder")}
              className={inputCls}
            />
          </label>
          <label className="block text-sm">
            {t("settings.fromAddress")}
            <input
              type="email"
              value={email.from_address}
              onChange={(e) => setEmail({ ...email, from_address: e.target.value })}
              placeholder={t("settings.usernamePlaceholder")}
              className={inputCls}
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={email.use_tls}
              onChange={(e) => setEmail({ ...email, use_tls: e.target.checked })}
              className="h-4 w-4"
            />
            {t("settings.tls")}
          </label>
          <p className="text-xs text-slate-400">{t("settings.gmailHint")}</p>
          {emailMsg && (
            <p className={`text-sm ${emailMsg.startsWith("✓") ? "text-emerald-600" : "text-red-600"}`}>
              {emailMsg}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button
              disabled={busy}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {t("common.save")}
            </button>
            <input
              type="email"
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              placeholder={t("settings.testRecipient")}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={sendTest}
              disabled={busy || !testTo}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50"
            >
              {t("settings.sendTest")}
            </button>
          </div>
        </form>

        {/* E-mail sablonok (automatizálásokhoz) */}
        <div className={cardCls("email", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}>
          <div className="flex items-center">
            <h2 className="font-semibold">{t("settings.tplTitle")}</h2>
            <button
              type="button"
              onClick={() => { setTplMsg(null); setTplForm({ id: "", name: "", subject: "", body: "" }); }}
              className="ml-auto rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
            >
              {t("settings.tplNew")}
            </button>
          </div>
          <p className="text-xs text-slate-500">{t("settings.tplHint")}</p>
          {templates.length === 0 ? (
            <p className="text-sm text-slate-400">{t("settings.tplEmpty")}</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {templates.map((tpl) => (
                <li key={tpl.id} className="flex items-center gap-3 py-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{tpl.name}</div>
                    <div className="truncate text-xs text-slate-400">{tpl.subject}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => { setTplMsg(null); setTplForm({ ...tpl }); }}
                    title={t("common.edit")}
                    className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                  >
                    ✏️
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteTemplate(tpl)}
                    title={t("common.delete")}
                    className="rounded border border-red-200 px-2 py-1 text-sm leading-none text-red-600 hover:bg-red-50"
                  >
                    🗑
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Skillek */}
        <div className={cardCls("skills", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}>
          <h2 className="font-semibold">{t("settings.skillsTitle")}</h2>
          <p className="text-xs text-slate-500">{t("settings.skillsHint")}</p>
          <form onSubmit={addSkill} className="flex gap-2">
            <input
              value={newSkill}
              onChange={(e) => setNewSkill(e.target.value)}
              placeholder={t("settings.newSkillPlaceholder")}
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <button className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
              {t("settings.addSkill")}
            </button>
          </form>
          {skillMsg && <p className="text-sm text-red-600">{skillMsg}</p>}
          {skills.length === 0 ? (
            <p className="text-sm text-slate-400">{t("settings.noSkills")}</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {skills.map((s) => (
                <li
                  key={s.id}
                  className="flex items-center gap-1 rounded-full bg-indigo-50 py-1 pl-3 pr-1 text-sm text-indigo-800"
                >
                  {s.name}
                  <button
                    onClick={() => removeSkill(s)}
                    className="rounded-full px-1.5 text-indigo-400 hover:bg-indigo-100 hover:text-red-600"
                    title={t("common.delete")}
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* AI integráció */}
        <div className={cardCls("ai", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}>
          <h2 className="font-semibold">{t("settings.aiTitle")}</h2>
          <p className="text-xs text-slate-500">{t("settings.aiHint")}</p>

          <div className="flex flex-wrap gap-4 text-sm">
            {([
              ["none", t("settings.aiNone")],
              ["anthropic", "Anthropic (Claude)"],
              ["gemini", "Google Gemini"],
            ] as const).map(([value, label]) => (
              <label key={value} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="ai-provider"
                  checked={ai.active_provider === value}
                  onChange={() => setAi({ ...ai, active_provider: value })}
                  className="h-4 w-4"
                />
                <span className={ai.active_provider === value ? "font-medium" : ""}>{label}</span>
              </label>
            ))}
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            {/* Anthropic */}
            <div className="space-y-2 rounded-xl border border-slate-200 p-4">
              <h3 className="text-sm font-semibold">Anthropic Claude</h3>
              <label className="block text-sm">
                {t("settings.apiKey")}{" "}
                {aiHasKeys.anthropic && <span className="text-slate-400">{t("settings.keySaved")}</span>}
                <input
                  type="password"
                  value={ai.anthropic_key}
                  onChange={(e) => setAi({ ...ai, anthropic_key: e.target.value })}
                  placeholder={aiHasKeys.anthropic ? "••••••••" : "sk-ant-…"}
                  className={inputCls}
                />
              </label>
              <label className="block text-sm">
                {t("settings.model")}
                <select
                  value={anthroCustom || !anthroKnown ? "__custom__" : ai.anthropic_model}
                  onChange={(e) => {
                    if (e.target.value === "__custom__") {
                      setAnthroCustom(true);
                    } else {
                      setAnthroCustom(false);
                      setAi({ ...ai, anthropic_model: e.target.value });
                    }
                  }}
                  className={inputCls}
                >
                  {ANTHROPIC_MODELS.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}{m.tagKey ? ` — ${t(m.tagKey)}` : ""}
                    </option>
                  ))}
                  <option value="__custom__">{t("settings.modelCustom")}</option>
                </select>
              </label>
              {(anthroCustom || !anthroKnown) && (
                <input
                  value={ai.anthropic_model}
                  onChange={(e) => setAi({ ...ai, anthropic_model: e.target.value })}
                  placeholder="claude-…"
                  className={inputCls}
                />
              )}
              <button
                type="button"
                onClick={() => testAi("anthropic")}
                disabled={aiBusy || !aiHasKeys.anthropic}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-40"
              >
                {t("settings.testConnection")}
              </button>
              <p className="text-xs text-slate-400">{t("settings.anthropicKeyHint")}</p>
            </div>

            {/* Gemini */}
            <div className="space-y-2 rounded-xl border border-slate-200 p-4">
              <h3 className="text-sm font-semibold">Google Gemini</h3>
              <label className="block text-sm">
                {t("settings.apiKey")}{" "}
                {aiHasKeys.gemini && <span className="text-slate-400">{t("settings.keySaved")}</span>}
                <input
                  type="password"
                  value={ai.gemini_key}
                  onChange={(e) => setAi({ ...ai, gemini_key: e.target.value })}
                  placeholder={aiHasKeys.gemini ? "••••••••" : "AIza…"}
                  className={inputCls}
                />
              </label>
              <label className="block text-sm">
                {t("settings.model")}
                <input
                  value={ai.gemini_model}
                  onChange={(e) => setAi({ ...ai, gemini_model: e.target.value })}
                  className={inputCls}
                />
              </label>
              <button
                type="button"
                onClick={() => testAi("gemini")}
                disabled={aiBusy || !aiHasKeys.gemini}
                className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-40"
              >
                {t("settings.testConnection")}
              </button>
              <p className="text-xs text-slate-400">{t("settings.geminiKeyHint")}</p>
            </div>
          </div>

          {/* Kiosztási prompt — szerkeszthető */}
          <div className="rounded-xl border border-slate-200 p-4">
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-sm font-semibold">
                {t("settings.promptTitle")}{" "}
                <span
                  className={`ml-1 rounded px-1.5 py-0.5 text-xs font-normal ${
                    promptIsCustom ? "bg-violet-100 text-violet-700" : "bg-slate-100 text-slate-500"
                  }`}
                >
                  {promptIsCustom ? t("settings.promptCustom") : t("settings.promptDefault")}
                </span>
              </h3>
              <button
                type="button"
                onClick={() => setAssignPrompt(defaultPrompt)}
                className="text-xs text-slate-500 hover:text-indigo-600"
              >
                {t("settings.promptReset")}
              </button>
            </div>
            <p className="mb-2 text-xs text-slate-500">{t("settings.promptHint")}</p>
            <textarea
              value={assignPrompt}
              onChange={(e) => setAssignPrompt(e.target.value)}
              rows={8}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs"
            />
          </div>

          {aiMsg && (
            <p className={`text-sm ${aiMsg.startsWith("✓") ? "text-emerald-600" : "text-red-600"}`}>
              {aiMsg}
            </p>
          )}
          <button
            type="button"
            onClick={saveAi}
            disabled={aiBusy}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {t("settings.saveAi")}
          </button>
        </div>

        {/* Értesítések (csak admin) */}
        <form
          onSubmit={saveNotifications}
          className={cardCls("notifications", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}
        >
          <h2 className="font-semibold">{t("notif.title")}</h2>
          <p className="text-xs text-slate-500">{t("notif.hint")}</p>
          <label className="block text-sm">
            {t("notif.recipients")}
            <input
              value={notif.recipients}
              onChange={(e) => setNotif({ ...notif, recipients: e.target.value })}
              placeholder="pl. vezeto@ceg.hu, uzletkoto@ceg.hu"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
            />
          </label>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={notif.daily_enabled} onChange={(e) => setNotif({ ...notif, daily_enabled: e.target.checked })} className="h-4 w-4" />
              {t("notif.daily")}
            </label>
            <label className="flex items-center gap-2">
              {t("notif.hour")}
              <input type="number" min={0} max={23} value={notif.send_hour} onChange={(e) => setNotif({ ...notif, send_hour: e.target.value })} className="w-16 rounded-lg border border-slate-300 px-2 py-1" />
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={notif.weekly_backup} onChange={(e) => setNotif({ ...notif, weekly_backup: e.target.checked })} className="h-4 w-4" />
              {t("notif.weeklyBackup")}
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={notif.auto_receipt} onChange={(e) => setNotif({ ...notif, auto_receipt: e.target.checked })} className="h-4 w-4" />
              {t("notif.autoReceipt")}
            </label>
          </div>
          {notifMsg && <p className="text-sm text-slate-600">{notifMsg}</p>}
          {/* WhatsApp bekötés (Meta Cloud API) — belső kommunikáció + dolgozói push */}
          <fieldset className="rounded-xl border border-emerald-200 bg-emerald-50 p-3">
            <legend className="px-1 text-xs font-semibold uppercase text-emerald-700">💬 {t("notif.waTitle")}</legend>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={notif.wa_enabled} onChange={(e) => setNotif({ ...notif, wa_enabled: e.target.checked })} className="h-4 w-4" />
              {t("notif.waEnabled")}
            </label>
            <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("notif.waToken")} {notif.wa_token_set && <span className="text-xs text-emerald-600">({t("notif.tokenSet")})</span>}
                <input type="password" value={notif.wa_token} onChange={(e) => setNotif({ ...notif, wa_token: e.target.value })} placeholder={notif.wa_token_set ? "••••••••" : "EAAG…"} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs" />
                <span className="mt-0.5 block text-xs text-slate-400">{t("notif.tokenHint")}</span>
              </label>
              <label className="block text-sm">
                {t("notif.waPhoneId")}
                <input value={notif.wa_phone_id} onChange={(e) => setNotif({ ...notif, wa_phone_id: e.target.value })} placeholder="1234567890" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs" />
              </label>
              <label className="block text-sm sm:col-span-2">
                {t("notif.waRecipients")}
                <input value={notif.wa_recipients} onChange={(e) => setNotif({ ...notif, wa_recipients: e.target.value })} placeholder="36301234567, 36209876543" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" />
              </label>
            </div>
            <p className="mt-1 text-xs text-emerald-700">{t("notif.waHint")}</p>
          </fieldset>

          {/* Telegram bekötés (Bot API) */}
          <fieldset className="rounded-xl border border-sky-200 bg-sky-50 p-3">
            <legend className="px-1 text-xs font-semibold uppercase text-sky-700">✈️ {t("notif.tgTitle")}</legend>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={notif.tg_enabled} onChange={(e) => setNotif({ ...notif, tg_enabled: e.target.checked })} className="h-4 w-4" />
              {t("notif.tgEnabled")}
            </label>
            <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                {t("notif.tgToken")} {notif.tg_token_set && <span className="text-xs text-sky-600">({t("notif.tokenSet")})</span>}
                <input type="password" value={notif.tg_token} onChange={(e) => setNotif({ ...notif, tg_token: e.target.value })} placeholder={notif.tg_token_set ? "••••••••" : "123456:ABC…"} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs" />
                <span className="mt-0.5 block text-xs text-slate-400">{t("notif.tokenHint")}</span>
              </label>
              <label className="block text-sm">
                {t("notif.tgChats")}
                <input value={notif.tg_chat_ids} onChange={(e) => setNotif({ ...notif, tg_chat_ids: e.target.value })} placeholder="-1001234567890, 123456789" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-xs" />
              </label>
            </div>
            <p className="mt-1 text-xs text-sky-700">{t("notif.tgHint")}</p>
          </fieldset>

          <div className="flex flex-wrap gap-2">
            <button disabled={notifBusy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
              {notifBusy ? t("common.saving") : t("common.save")}
            </button>
            <button type="button" onClick={testDigest} disabled={notifBusy} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50">
              {t("notif.testBtn")}
            </button>
            <button type="button" onClick={testWhatsApp} disabled={notifBusy} className="rounded-lg border border-emerald-300 px-4 py-2 text-sm text-emerald-700 hover:bg-emerald-50 disabled:opacity-50">
              💬 {t("notif.waTestBtn")}
            </button>
            <button type="button" onClick={testTelegram} disabled={notifBusy} className="rounded-lg border border-sky-300 px-4 py-2 text-sm text-sky-700 hover:bg-sky-50 disabled:opacity-50">
              ✈️ {t("notif.tgTestBtn")}
            </button>
          </div>
        </form>

        {/* Adatbázis-mentés (csak admin) */}
        <div className={cardCls("data", "space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm")}>
          <h2 className="font-semibold">{t("backup.title")}</h2>
          <p className="text-xs text-slate-500">{t("backup.hint")}</p>
          <button
            type="button"
            onClick={downloadBackup}
            disabled={backupBusy}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {backupBusy ? t("common.loading") : t("backup.download")}
          </button>
        </div>

        {/* Veszélyzóna — teljes törlés entitásonként (csak admin) */}
        <div className={cardCls("data", "space-y-3 rounded-2xl border-2 border-red-200 bg-white p-5 shadow-sm")}>
          <h2 className="font-semibold text-red-700">{t("danger.title")}</h2>
          <p className="text-xs text-slate-500">{t("danger.hint")}</p>
          <div className="flex flex-wrap gap-2">
            {(["assets", "products", "partners"] as const).map((entity) => (
              <button
                key={entity}
                type="button"
                onClick={() => wipe(entity)}
                disabled={wipeBusy}
                className="rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
              >
                {t(`danger.${entity}`, { count: wipeCounts?.[entity] ?? "…" })}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* E-mail sablon szerkesztő */}
      {tplForm && (
        <div
          onMouseDown={(e) => { if (e.target === e.currentTarget) setTplForm(null); }}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4"
        >
          <form onSubmit={saveTemplate} className="my-8 w-full max-w-lg space-y-3 rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold">
              {tplForm.id ? t("settings.tplEdit") : t("settings.tplNew")}
            </h2>

            {/* AI-generálás: leírás + esemény → kitölti az alábbi mezőket */}
            <div className="space-y-2 rounded-xl border border-violet-200 bg-violet-50/60 p-3">
              <div className="text-sm font-medium text-violet-800">✨ {t("settings.tplAiTitle")}</div>
              <textarea
                rows={2}
                value={tplAiDesc}
                onChange={(e) => setTplAiDesc(e.target.value)}
                placeholder={t("settings.tplAiPlaceholder")}
                className={inputCls}
              />
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={tplAiTrigger}
                  onChange={(e) => setTplAiTrigger(e.target.value)}
                  className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                >
                  <option value="">{t("settings.tplAiAnyTrigger")}</option>
                  {Object.keys(autoTriggers).map((k) => (
                    <option key={k} value={k}>{t(`auto.triggers.${k.replace(/\./g, "_")}`)}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={generateTemplateAi}
                  disabled={tplAiBusy || tplAiDesc.trim().length < 3}
                  className="rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50"
                >
                  {tplAiBusy ? t("settings.tplAiBusy") : t("settings.tplAiGenerate")}
                </button>
              </div>
              <p className="text-xs text-violet-700/70">{t("settings.tplAiHint")}</p>
            </div>

            <label className="block text-sm">
              {t("settings.tplName")} *
              <input required value={tplForm.name} onChange={(e) => setTplForm({ ...tplForm, name: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("settings.tplSubject")} *
              <input required value={tplForm.subject} onChange={(e) => setTplForm({ ...tplForm, subject: e.target.value })} className={inputCls} />
            </label>
            <label className="block text-sm">
              {t("settings.tplBody")} *
              <textarea required rows={8} value={tplForm.body} onChange={(e) => setTplForm({ ...tplForm, body: e.target.value })} className={inputCls} />
            </label>
            <p className="text-xs text-slate-400">{t("settings.tplVarsHint")}</p>
            {tplMsg && <p className="text-sm text-red-600">{tplMsg}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setTplForm(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100">{t("common.cancel")}</button>
              <button disabled={tplBusy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{tplBusy ? t("common.saving") : t("common.save")}</button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
