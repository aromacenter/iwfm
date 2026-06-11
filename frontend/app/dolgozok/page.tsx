"use client";

/** Dolgozói törzsadat: lista, felvétel, szerkesztés, érzékeny adat felfedése. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { AuthUser, EmployeeOut } from "@/lib/types";

interface RevealOut {
  tax_id: string | null;
  taj: string | null;
  bank_account: string | null;
  wage_amount: string | null;
}

interface Skill {
  id: number;
  name: string;
}

const EMPTY_FORM = {
  email: "",
  last_name: "",
  first_name: "",
  birth_name: "",
  mother_name: "",
  birth_place: "",
  birth_date: "",
  citizenship: "magyar",
  phone: "",
  address: "",
  residence_address: "",
  hire_date: "",
  job_title: "",
  feor_code: "",
  employment_type: "full_time",
  contract_type: "indefinite",
  weekly_hours: 40,
  wage_type: "monthly",
  annual_leave_days: 20,
  tax_id: "",
  taj: "",
  bank_account: "",
  wage_amount: "",
  initial_password: "",
};

type FormState = typeof EMPTY_FORM;

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-sm">
      <span className="text-slate-600">{label}</span>
      {children}
    </label>
  );
}

const inputCls = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";

export default function DolgozokPage() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [employees, setEmployees] = useState<EmployeeOut[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<EmployeeOut | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<Record<string, RevealOut>>({});
  const [allSkills, setAllSkills] = useState<Skill[]>([]);
  const [skillIds, setSkillIds] = useState<number[]>([]);

  const load = useCallback(() => {
    api.get<EmployeeOut[]>("/api/employees").then(setEmployees).catch(() => {});
  }, []);

  useEffect(() => {
    api.get<AuthUser>("/api/auth/me").then(setMe).catch(() => {});
    api.get<Skill[]>("/api/settings/skills").then(setAllSkills).catch(() => {});
    load();
  }, [load]);

  const isAdmin = me?.role === "admin";

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    // gépelésre tűnjön el a mező hibajelzése
    if (typeof key === "string" && fieldErrors[key]) {
      setFieldErrors((fe) => {
        const next = { ...fe };
        delete next[key];
        return next;
      });
    }
  }

  /** Hibás mezőhöz piros keret. */
  function fieldCls(key: string): string {
    return fieldErrors[key]
      ? "mt-1 w-full rounded-lg border-2 border-red-500 bg-red-50 px-3 py-2 text-sm"
      : inputCls;
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setSkillIds([]);
    setError(null);
    setShowForm(true);
  }

  function openEdit(emp: EmployeeOut) {
    setEditing(emp);
    setForm({
      ...EMPTY_FORM,
      email: emp.email ?? "",
      last_name: emp.last_name,
      first_name: emp.first_name,
      birth_name: emp.birth_name ?? "",
      mother_name: emp.mother_name ?? "",
      birth_place: emp.birth_place ?? "",
      birth_date: emp.birth_date ?? "",
      citizenship: emp.citizenship,
      phone: emp.phone ?? "",
      address: emp.address ?? "",
      residence_address: emp.residence_address ?? "",
      hire_date: emp.hire_date,
      job_title: emp.job_title ?? "",
      feor_code: emp.feor_code ?? "",
      employment_type: emp.employment_type,
      contract_type: emp.contract_type,
      weekly_hours: emp.weekly_hours,
      wage_type: emp.wage_type,
      annual_leave_days: emp.annual_leave_days,
    });
    setSkillIds((emp.skills ?? []).map((s) => s.id));
    setError(null);
    setShowForm(true);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      const clean = (v: string) => (v.trim() === "" ? undefined : v.trim());
      if (editing) {
        const body: Record<string, unknown> = {
          last_name: form.last_name,
          first_name: form.first_name,
          birth_name: clean(form.birth_name) ?? null,
          mother_name: clean(form.mother_name) ?? null,
          birth_place: clean(form.birth_place) ?? null,
          birth_date: clean(form.birth_date) ?? null,
          citizenship: form.citizenship,
          phone: clean(form.phone) ?? null,
          address: clean(form.address) ?? null,
          residence_address: clean(form.residence_address) ?? null,
          hire_date: form.hire_date,
          job_title: clean(form.job_title) ?? null,
          feor_code: clean(form.feor_code) ?? null,
          employment_type: form.employment_type,
          contract_type: form.contract_type,
          weekly_hours: form.weekly_hours,
          wage_type: form.wage_type,
          annual_leave_days: form.annual_leave_days,
          skill_ids: skillIds,
        };
        // érzékeny mezők csak akkor, ha kitöltötték
        for (const k of ["tax_id", "taj", "bank_account", "wage_amount"] as const) {
          if (form[k].trim() !== "") body[k] = form[k].trim();
        }
        await api.patch(`/api/employees/${editing.id}`, body);
      } else {
        const created = await api.post<EmployeeOut & { generated_password?: string }>("/api/employees", {
          ...form,
          skill_ids: skillIds,
          birth_name: clean(form.birth_name),
          mother_name: clean(form.mother_name),
          birth_place: clean(form.birth_place),
          birth_date: clean(form.birth_date),
          phone: clean(form.phone),
          address: clean(form.address),
          residence_address: clean(form.residence_address),
          job_title: clean(form.job_title),
          feor_code: clean(form.feor_code),
          tax_id: clean(form.tax_id),
          taj: clean(form.taj),
          bank_account: clean(form.bank_account),
          wage_amount: clean(form.wage_amount),
          initial_password: clean(form.initial_password),
        });
        const info = [
          created.employee_code ? `Törzsszám (blokkoláshoz): ${created.employee_code}` : null,
          created.generated_password
            ? `Belépési jelszó (csak most jelenik meg): ${created.generated_password}`
            : null,
        ].filter(Boolean);
        if (info.length > 0) {
          alert(`Dolgozó létrehozva — add át neki:\n\n${info.join("\n")}`);
        }
      }
      setShowForm(false);
      load();
    } catch (err) {
      if (err instanceof ApiError && err.code === "employee.invalid_ids") {
        const detail = err.detail as { fields?: Record<string, string> };
        setFieldErrors(detail.fields ?? {});
        setError("Javítsd a pirossal jelölt mezőket.");
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function reveal(empId: string) {
    try {
      const data = await api.get<RevealOut>(`/api/employees/${empId}/reveal`);
      setRevealed((r) => ({ ...r, [empId]: data }));
    } catch (err) {
      alert(errorMessage(err));
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">Dolgozók</h1>
        {isAdmin && (
          <button
            onClick={openCreate}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            + Új dolgozó
          </button>
        )}
      </div>

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <th className="px-4 py-3">Név</th>
              <th className="px-4 py-3">Törzsszám</th>
              <th className="px-4 py-3">Munkakör</th>
              <th className="px-4 py-3">Email</th>
              <th className="px-4 py-3">Belépés</th>
              <th className="px-4 py-3">Heti óra</th>
              <th className="px-4 py-3">Adóazonosító</th>
              <th className="px-4 py-3">TAJ</th>
              <th className="px-4 py-3">Bankszámla</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {employees.map((emp) => {
              const rev = revealed[emp.id];
              return (
                <tr key={emp.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3">
                    <div className="font-medium">
                      {emp.last_name} {emp.first_name}
                      {emp.status === "inactive" && (
                        <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs">inaktív</span>
                      )}
                    </div>
                    {(emp.skills ?? []).length > 0 && (
                      <div className="mt-1 flex max-w-56 flex-wrap gap-1">
                        {emp.skills.map((s) => (
                          <span
                            key={s.id}
                            className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700"
                          >
                            {s.name}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-sm font-semibold text-indigo-700">
                    {emp.employee_code ?? "—"}
                  </td>
                  <td className="px-4 py-3">{emp.job_title ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-500">{emp.email ?? "—"}</td>
                  <td className="px-4 py-3">{emp.hire_date}</td>
                  <td className="px-4 py-3">{emp.weekly_hours}h</td>
                  <td className="px-4 py-3 font-mono text-xs">{rev?.tax_id ?? emp.tax_id_masked ?? "—"}</td>
                  <td className="px-4 py-3 font-mono text-xs">{rev?.taj ?? emp.taj_masked ?? "—"}</td>
                  <td className="px-4 py-3 font-mono text-xs">{rev?.bank_account ?? emp.bank_account_masked ?? "—"}</td>
                  <td className="px-4 py-3 text-right">
                    {isAdmin && (
                      <div className="flex justify-end gap-2">
                        {!rev && (emp.tax_id_masked || emp.taj_masked) && (
                          <button
                            onClick={() => reveal(emp.id)}
                            className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                            title="Érzékeny adatok megjelenítése (auditálva)"
                          >
                            Felfedés
                          </button>
                        )}
                        <button
                          onClick={() => openEdit(emp)}
                          className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100"
                        >
                          Szerkesztés
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
            {employees.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-10 text-center text-slate-400">
                  Még nincs dolgozó felvéve.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showForm && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form
            onSubmit={submit}
            className="my-8 w-full max-w-2xl space-y-4 rounded-2xl bg-white p-6 shadow-xl"
          >
            <h2 className="text-lg font-semibold">
              {editing ? `${editing.last_name} ${editing.first_name} szerkesztése` : "Új dolgozó"}
            </h2>

            <fieldset className="grid grid-cols-2 gap-3">
              <legend className="mb-1 text-sm font-semibold text-slate-700">Személyes adatok</legend>
              <Field label="Vezetéknév *">
                <input required value={form.last_name} onChange={(e) => set("last_name", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Keresztnév *">
                <input required value={form.first_name} onChange={(e) => set("first_name", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Születési név">
                <input value={form.birth_name} onChange={(e) => set("birth_name", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Anyja neve">
                <input value={form.mother_name} onChange={(e) => set("mother_name", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Születési hely">
                <input value={form.birth_place} onChange={(e) => set("birth_place", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Születési idő">
                <input type="date" value={form.birth_date} onChange={(e) => set("birth_date", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Állampolgárság">
                <input value={form.citizenship} onChange={(e) => set("citizenship", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Telefonszám">
                <input value={form.phone} onChange={(e) => set("phone", e.target.value)} placeholder="+36 30 123 4567" className={inputCls} />
              </Field>
              <Field label="Állandó lakcím">
                <input value={form.address} onChange={(e) => set("address", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Tartózkodási cím">
                <input value={form.residence_address} onChange={(e) => set("residence_address", e.target.value)} className={inputCls} />
              </Field>
            </fieldset>

            <fieldset className="grid grid-cols-2 gap-3">
              <legend className="mb-1 text-sm font-semibold text-slate-700">
                Azonosítók (titkosítva tárolva)
              </legend>
              <Field label={editing ? "Adóazonosító jel (csak ha módosul)" : "Adóazonosító jel"}>
                <input value={form.tax_id} onChange={(e) => set("tax_id", e.target.value)} placeholder="8xxxxxxxxx" className={fieldCls("tax_id")} />
                {fieldErrors.tax_id && <p className="mt-1 text-xs text-red-600">{fieldErrors.tax_id}</p>}
              </Field>
              <Field label={editing ? "TAJ szám (csak ha módosul)" : "TAJ szám"}>
                <input value={form.taj} onChange={(e) => set("taj", e.target.value)} placeholder="xxx xxx xxx" className={fieldCls("taj")} />
                {fieldErrors.taj && <p className="mt-1 text-xs text-red-600">{fieldErrors.taj}</p>}
              </Field>
              <Field label={editing ? "Bankszámlaszám (csak ha módosul)" : "Bankszámlaszám"}>
                <input value={form.bank_account} onChange={(e) => set("bank_account", e.target.value)} placeholder="xxxxxxxx-xxxxxxxx" className={fieldCls("bank_account")} />
                {fieldErrors.bank_account && <p className="mt-1 text-xs text-red-600">{fieldErrors.bank_account}</p>}
              </Field>
              <Field label="Bér">
                <input value={form.wage_amount} onChange={(e) => set("wage_amount", e.target.value)} placeholder="pl. 650000 HUF/hó" className={inputCls} />
              </Field>
            </fieldset>

            <fieldset className="grid grid-cols-2 gap-3">
              <legend className="mb-1 text-sm font-semibold text-slate-700">Munkaviszony</legend>
              <Field label="Belépés dátuma *">
                <input required type="date" value={form.hire_date} onChange={(e) => set("hire_date", e.target.value)} className={inputCls} />
              </Field>
              <Field label="Munkakör">
                <input value={form.job_title} onChange={(e) => set("job_title", e.target.value)} className={inputCls} />
              </Field>
              <Field label="FEOR-08 kód">
                <input value={form.feor_code} onChange={(e) => set("feor_code", e.target.value)} placeholder="pl. 9223" className={inputCls} />
              </Field>
              <Field label="Heti óraszám">
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={form.weekly_hours}
                  onChange={(e) => set("weekly_hours", Number(e.target.value))}
                  className={inputCls}
                />
              </Field>
              <Field label="Foglalkoztatás">
                <select value={form.employment_type} onChange={(e) => set("employment_type", e.target.value)} className={inputCls}>
                  <option value="full_time">Teljes munkaidő</option>
                  <option value="part_time">Részmunkaidő</option>
                </select>
              </Field>
              <Field label="Szerződés">
                <select value={form.contract_type} onChange={(e) => set("contract_type", e.target.value)} className={inputCls}>
                  <option value="indefinite">Határozatlan idejű</option>
                  <option value="fixed_term">Határozott idejű</option>
                </select>
              </Field>
              <Field label="Bérezés típusa">
                <select value={form.wage_type} onChange={(e) => set("wage_type", e.target.value)} className={inputCls}>
                  <option value="monthly">Havibér</option>
                  <option value="hourly">Órabér</option>
                </select>
              </Field>
              <Field label="Éves szabadság (nap)">
                <input
                  type="number"
                  min={0}
                  max={60}
                  value={form.annual_leave_days}
                  onChange={(e) => set("annual_leave_days", Number(e.target.value))}
                  className={inputCls}
                />
              </Field>
            </fieldset>

            <fieldset>
              <legend className="mb-1 text-sm font-semibold text-slate-700">
                Skillek / képesítések
              </legend>
              {allSkills.length === 0 ? (
                <p className="text-xs text-slate-400">
                  Még nincs skill — a Beállítások menüben hozhatsz létre.
                </p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {allSkills.map((s) => {
                    const active = skillIds.includes(s.id);
                    return (
                      <button
                        type="button"
                        key={s.id}
                        onClick={() =>
                          setSkillIds((ids) =>
                            active ? ids.filter((i) => i !== s.id) : [...ids, s.id]
                          )
                        }
                        className={`rounded-full px-3 py-1 text-sm transition-colors ${
                          active
                            ? "bg-indigo-600 text-white"
                            : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                        }`}
                      >
                        {active ? "✓ " : ""}
                        {s.name}
                      </button>
                    );
                  })}
                </div>
              )}
            </fieldset>

            {!editing && (
              <fieldset className="grid grid-cols-2 gap-3">
                <legend className="mb-1 text-sm font-semibold text-slate-700">Belépési fiók</legend>
                <Field label="Email (bejelentkezéshez) *">
                  <input required type="email" value={form.email} onChange={(e) => set("email", e.target.value)} className={inputCls} />
                </Field>
                <Field label="Kezdeti jelszó (üresen: generált)">
                  <input value={form.initial_password} onChange={(e) => set("initial_password", e.target.value)} minLength={form.initial_password ? 10 : undefined} className={inputCls} />
                </Field>
              </fieldset>
            )}

            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                Mégsem
              </button>
              <button
                disabled={busy}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busy ? "Mentés…" : "Mentés"}
              </button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
