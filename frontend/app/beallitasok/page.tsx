"use client";

/** Beállítások (admin): email-fiók az értesítésekhez + skillek kezelése. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, errorMessage } from "@/lib/api";

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

const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";

export default function BeallitasokPage() {
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
      setEmailMsg("✓ Mentve.");
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
      setEmailMsg(`✓ Teszt email elküldve: ${testTo}`);
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
    if (!confirm(`Törlöd a(z) „${skill.name}" skillt? A dolgozókról is lekerül.`)) return;
    try {
      await api.delete(`/api/settings/skills/${skill.id}`);
      load();
    } catch (err) {
      setSkillMsg(errorMessage(err));
    }
  }

  return (
    <AppShell>
      <h1 className="mb-4 text-xl font-bold">Beállítások</h1>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        {/* Email értesítések */}
        <form
          onSubmit={saveEmail}
          className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
        >
          <h2 className="font-semibold">Email-fiók (értesítések)</h2>
          <p className="text-xs text-slate-500">
            Ha bekapcsolod, a rendszer emailt küld a dolgozóknak az új beosztásról, a közölt
            beosztás módosulásáról és a távollét-kérelmük elbírálásáról.
          </p>
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={email.enabled}
              onChange={(e) => setEmail({ ...email, enabled: e.target.checked })}
              className="h-4 w-4"
            />
            Értesítések bekapcsolva
          </label>
          <div className="grid grid-cols-3 gap-3">
            <label className="col-span-2 block text-sm">
              SMTP szerver
              <input
                value={email.host}
                onChange={(e) => setEmail({ ...email, host: e.target.value })}
                placeholder="pl. smtp.gmail.com"
                className={inputCls}
              />
            </label>
            <label className="block text-sm">
              Port
              <input
                type="number"
                value={email.port}
                onChange={(e) => setEmail({ ...email, port: Number(e.target.value) })}
                className={inputCls}
              />
            </label>
          </div>
          <label className="block text-sm">
            Felhasználónév
            <input
              value={email.username}
              onChange={(e) => setEmail({ ...email, username: e.target.value })}
              placeholder="pl. ertesites@cegem.hu"
              className={inputCls}
            />
          </label>
          <label className="block text-sm">
            Jelszó{" "}
            {hasPassword && <span className="text-slate-400">(mentve — üresen hagyva nem változik)</span>}
            <input
              type="password"
              value={email.password}
              onChange={(e) => setEmail({ ...email, password: e.target.value })}
              placeholder={hasPassword ? "••••••••" : "SMTP / alkalmazás-jelszó"}
              className={inputCls}
            />
          </label>
          <label className="block text-sm">
            Feladó cím
            <input
              type="email"
              value={email.from_address}
              onChange={(e) => setEmail({ ...email, from_address: e.target.value })}
              placeholder="pl. ertesites@cegem.hu"
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
            TLS titkosítás (STARTTLS — 587-es porthoz)
          </label>
          <p className="text-xs text-slate-400">
            Gmail esetén: smtp.gmail.com, 587-es port, és a Google-fiókban generált
            <em> alkalmazásjelszó</em> kell (nem a sima jelszó).
          </p>
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
              Mentés
            </button>
            <input
              type="email"
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              placeholder="teszt címzett"
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={sendTest}
              disabled={busy || !testTo}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50"
            >
              Teszt küldése
            </button>
          </div>
        </form>

        {/* Skillek */}
        <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="font-semibold">Skillek / képesítések</h2>
          <p className="text-xs text-slate-500">
            Hozz létre skilleket (pl. targoncavezető, pénztár, elsősegély), majd a Dolgozók
            oldalon rendeld hozzájuk. Később a beosztás-tervezésben is használható lesz.
          </p>
          <form onSubmit={addSkill} className="flex gap-2">
            <input
              value={newSkill}
              onChange={(e) => setNewSkill(e.target.value)}
              placeholder="Új skill neve"
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <button className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
              + Hozzáad
            </button>
          </form>
          {skillMsg && <p className="text-sm text-red-600">{skillMsg}</p>}
          {skills.length === 0 ? (
            <p className="text-sm text-slate-400">Még nincs skill létrehozva.</p>
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
                    title="Törlés"
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </AppShell>
  );
}
