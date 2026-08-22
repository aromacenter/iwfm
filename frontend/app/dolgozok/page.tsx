"use client";

/** Dolgozói törzsadat: lista, felvétel, szerkesztés, érzékeny adat felfedése. */

import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import IconLegend from "@/components/IconLegend";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useUI } from "@/lib/ui";
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

const LOGIN_ROLES = ["employee", "szervizes", "uzletkoto", "manager", "admin"] as const;

const EMPTY_FORM = {
  email: "",
  role: "employee" as (typeof LOGIN_ROLES)[number],
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
  payroll_source: "attendance" as "attendance" | "schedule",
  tax_id: "",
  taj: "",
  bank_account: "",
  wage_amount: "",
  initial_password: "",
};

type FormState = typeof EMPTY_FORM;

type AvailDay = { on: boolean; from: string; to: string };

function emptyAvail(): Record<string, AvailDay> {
  const out: Record<string, AvailDay> = {};
  for (let i = 0; i < 7; i++) out[String(i)] = { on: false, from: "08:00", to: "16:00" };
  return out;
}

function availToApi(avail: Record<string, AvailDay>): Record<string, [string, string]> | null {
  const out: Record<string, [string, string]> = {};
  for (const [day, v] of Object.entries(avail)) {
    if (v.on && v.from && v.to) out[day] = [v.from, v.to];
  }
  return Object.keys(out).length > 0 ? out : null;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
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
  const [archive, setArchive] = useState(false);
  const [termDate, setTermDate] = useState("");
  // Alvállalkozó (számlás): csak név/cím/elérhetőség/adószám/bankszámla kell;
  // bármikor átváltható alkalmazottira és vissza.
  const [contractor, setContractor] = useState(false);
  const [companyTax, setCompanyTax] = useState("");
  const [avail, setAvail] = useState<Record<string, AvailDay>>(emptyAvail());
  const [showArchived, setShowArchived] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [revealed, setRevealed] = useState<Record<string, RevealOut>>({});
  const [allSkills, setAllSkills] = useState<Skill[]>([]);
  const [skillIds, setSkillIds] = useState<number[]>([]);
  const { t } = useT();
  const { toast, confirm } = useUI();

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
    if (typeof key === "string" && fieldErrors[key]) {
      setFieldErrors((fe) => {
        const next = { ...fe };
        delete next[key];
        return next;
      });
    }
  }

  function fieldCls(key: string): string {
    return fieldErrors[key]
      ? "mt-1 w-full rounded-lg border-2 border-red-500 bg-red-50 px-3 py-2 text-sm"
      : inputCls;
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setSkillIds([]);
    setArchive(false);
    setTermDate("");
    setContractor(false);
    setCompanyTax("");
    setAvail(emptyAvail());
    setError(null);
    setShowForm(true);
  }

  function openEdit(emp: EmployeeOut) {
    setEditing(emp);
    setForm({
      ...EMPTY_FORM,
      email: emp.email ?? "",
      role: (emp.role as (typeof LOGIN_ROLES)[number]) ?? "employee",
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
      payroll_source: emp.payroll_source ?? "attendance",
    });
    setSkillIds((emp.skills ?? []).map((s) => s.id));
    setArchive(emp.status === "inactive");
    setTermDate(emp.termination_date ?? "");
    setContractor(emp.is_contractor);
    setCompanyTax(emp.company_tax_number ?? "");
    const av = emptyAvail();
    for (const [day, iv] of Object.entries(emp.availability ?? {})) {
      if (iv && av[day]) av[day] = { on: true, from: iv[0], to: iv[1] };
    }
    setAvail(av);
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
          payroll_source: form.payroll_source,
          skill_ids: skillIds,
          role: form.role,
          availability: availToApi(avail),
          is_contractor: contractor,
          company_tax_number: contractor ? (companyTax.trim() || null) : null,
          // Munkaviszony megszüntetése: archivált (inactive) státusz + dátum;
          // visszavonva újra aktív, a dátum törlődik.
          status: archive ? "inactive" : "active",
          termination_date: archive
            ? (termDate || new Date().toISOString().slice(0, 10))
            : null,
        };
        for (const k of ["tax_id", "taj", "bank_account", "wage_amount"] as const) {
          if (form[k].trim() !== "") body[k] = form[k].trim();
        }
        await api.patch(`/api/employees/${editing.id}`, body);
      } else {
        const created = await api.post<EmployeeOut & { generated_password?: string }>("/api/employees", {
          ...form,
          // Alvállalkozónál a munkaügyi mezők nem látszanak — a kötelező
          // hire_date a felvétel napja lesz.
          hire_date: form.hire_date || new Date().toISOString().slice(0, 10),
          is_contractor: contractor,
          company_tax_number: contractor ? (companyTax.trim() || null) : null,
          availability: availToApi(avail),
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
          created.employee_code ? t("emp.createdCode", { code: created.employee_code }) : null,
          created.generated_password
            ? t("emp.createdPassword", { password: created.generated_password })
            : null,
        ].filter(Boolean);
        if (info.length > 0) {
          toast(`${t("emp.createdInfo")}\n\n${info.join("\n")}`, "success");
        }
      }
      setShowForm(false);
      load();
    } catch (err) {
      if (err instanceof ApiError && err.code === "employee.invalid_ids") {
        const detail = err.detail as { fields?: Record<string, string> };
        setFieldErrors(detail.fields ?? {});
        setError(t("emp.fixMarked"));
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
      toast(errorMessage(err), "error");
    }
  }

  async function resetPassword(empId: string) {
    if (!(await confirm(t("account.resetConfirm")))) return;
    try {
      const res = await api.post<{ generated_password: string }>(
        `/api/employees/${empId}/reset-password`,
      );
      toast(t("account.resetDone", { password: res.generated_password }), "success");
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">{t("emp.title")}</h1>
        <div className="mr-auto ml-3 flex gap-1.5">
          {([[false, t("emp.filterActive")], [true, t("emp.filterArchived")]] as const).map(([val, label]) => (
            <button
              key={String(val)}
              onClick={() => setShowArchived(val)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                showArchived === val
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {label} ({employees.filter((e) => (val ? e.status === "inactive" : e.status === "active")).length})
            </button>
          ))}
        </div>
        {isAdmin && (
          <button
            onClick={openCreate}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            {t("emp.new")}
          </button>
        )}
      </div>

      {isAdmin && (
        <IconLegend
          items={[
            { icon: "👁", label: t("emp.reveal") },
            { icon: "✏️", label: t("common.edit") },
            { icon: "🔑", label: t("account.resetPassword") },
          ]}
        />
      )}
      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase text-slate-500">
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("emp.name")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("emp.email")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3 text-right">{t("emp.weeklyHours")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3">{t("emp.sensitiveCol")}</th>
              <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {employees
              .filter((emp) => (showArchived ? emp.status === "inactive" : emp.status === "active"))
              .map((emp) => {
              const rev = revealed[emp.id];
              return (
                <tr key={emp.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-4 py-3">
                    <div className="font-medium">
                      {emp.last_name} {emp.first_name}
                      {emp.is_contractor && (
                        <span className="ml-2 rounded bg-indigo-100 px-1.5 py-0.5 text-xs text-indigo-700">
                          🧾 {t("emp.contractorBadge")}
                        </span>
                      )}
                      {emp.status === "inactive" && (
                        <span className="ml-2 rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-700">
                          {t("emp.archivedBadge")}
                          {emp.termination_date && <> · {emp.termination_date}</>}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-slate-400">
                      <span className="font-mono font-semibold text-indigo-700">{emp.employee_code ?? "—"}</span>
                      {emp.job_title && <> · {emp.job_title}</>}
                    </div>
                    {(emp.skills ?? []).length > 0 && (
                      <div className="group relative mt-0.5 inline-flex w-fit cursor-default items-center gap-1 text-xs text-slate-400">
                        {t("emp.skills")}
                        <span className="flex h-4 w-4 items-center justify-center rounded-full border border-slate-300 text-[10px] font-semibold text-slate-400 group-hover:border-indigo-400 group-hover:text-indigo-600">
                          i
                        </span>
                        <div className="invisible absolute left-0 top-5 z-20 w-max max-w-64 rounded-xl border border-slate-200 bg-white p-2 opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100">
                          <div className="flex flex-wrap gap-1">
                            {emp.skills.map((s) => (
                              <span
                                key={s.id}
                                className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700"
                              >
                                {s.name}
                              </span>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-500">
                    <div>{emp.email ?? "—"}</div>
                    <div className="text-xs text-slate-400">{t("emp.hireDate")}: {emp.hire_date}</div>
                  </td>
                  <td className="px-4 py-3 text-right">{emp.weekly_hours}h</td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-500">
                    <div><span className="font-sans text-slate-400">{t("emp.taxId")}: </span>{rev?.tax_id ?? emp.tax_id_masked ?? "—"}</div>
                    <div><span className="font-sans text-slate-400">{t("emp.taj")}: </span>{rev?.taj ?? emp.taj_masked ?? "—"}</div>
                    <div><span className="font-sans text-slate-400">{t("emp.bankAccount")}: </span>{rev?.bank_account ?? emp.bank_account_masked ?? "—"}</div>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {isAdmin && (
                      <div className="flex justify-end gap-1.5">
                        {!rev && (emp.tax_id_masked || emp.taj_masked) && (
                          <button
                            onClick={() => reveal(emp.id)}
                            className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                            title={t("emp.revealTitle")}
                          >
                            👁
                          </button>
                        )}
                        <button
                          onClick={() => openEdit(emp)}
                          title={t("common.edit")}
                          className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                        >
                          ✏️
                        </button>
                        <button
                          onClick={() => resetPassword(emp.id)}
                          title={t("account.resetPassword")}
                          className="rounded border border-slate-300 px-2 py-1 text-sm leading-none hover:bg-slate-100"
                        >
                          🔑
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
            {employees.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-slate-400">
                  {t("emp.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {showForm && (
        <div onMouseDown={(e) => { if (e.target === e.currentTarget) setShowForm(false); }} className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4">
          <form
            onSubmit={submit}
            className="my-8 w-full max-w-2xl space-y-4 rounded-2xl bg-white p-6 shadow-xl"
          >
            <h2 className="text-lg font-semibold">
              {editing
                ? t("emp.editTitle", { name: `${editing.last_name} ${editing.first_name}` })
                : t("emp.newTitle")}
            </h2>

            <label className="flex items-start gap-2 rounded-xl border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-900">
              <input
                type="checkbox"
                checked={contractor}
                onChange={(e) => setContractor(e.target.checked)}
                className="mt-0.5 h-4 w-4"
              />
              <span>
                <span className="font-semibold">🧾 {t("emp.contractorCheckbox")}</span>
                <span className="mt-0.5 block text-xs text-indigo-700">{t("emp.contractorHint")}</span>
              </span>
            </label>

            <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <legend className="mb-1 text-sm font-semibold text-slate-700">{t("emp.personalSection")}</legend>
              <Field label={t("emp.lastName")}>
                <input required value={form.last_name} onChange={(e) => set("last_name", e.target.value)} className={inputCls} />
              </Field>
              <Field label={t("emp.firstName")}>
                <input required value={form.first_name} onChange={(e) => set("first_name", e.target.value)} className={inputCls} />
              </Field>
              {!contractor && (
              <>
              <Field label={t("emp.birthName")}>
                <input value={form.birth_name} onChange={(e) => set("birth_name", e.target.value)} className={inputCls} />
              </Field>
              <Field label={t("emp.motherName")}>
                <input value={form.mother_name} onChange={(e) => set("mother_name", e.target.value)} className={inputCls} />
              </Field>
              <Field label={t("emp.birthPlace")}>
                <input value={form.birth_place} onChange={(e) => set("birth_place", e.target.value)} className={inputCls} />
              </Field>
              <Field label={t("emp.birthDate")}>
                <input type="date" value={form.birth_date} onChange={(e) => set("birth_date", e.target.value)} className={inputCls} />
              </Field>
              <Field label={t("emp.citizenship")}>
                <input value={form.citizenship} onChange={(e) => set("citizenship", e.target.value)} className={inputCls} />
              </Field>
              </>
              )}
              <Field label={t("emp.phone")}>
                <input value={form.phone} onChange={(e) => set("phone", e.target.value)} placeholder="+36 30 123 4567" className={inputCls} />
              </Field>
              <Field label={t("emp.address")}>
                <input value={form.address} onChange={(e) => set("address", e.target.value)} className={inputCls} />
              </Field>
              {!contractor && (
              <Field label={t("emp.residenceAddress")}>
                <input value={form.residence_address} onChange={(e) => set("residence_address", e.target.value)} className={inputCls} />
              </Field>
              )}
            </fieldset>

            <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <legend className="mb-1 text-sm font-semibold text-slate-700">{t("emp.idsSection")}</legend>
              {contractor ? (
                <Field label={t("emp.companyTaxNumber")}>
                  <input value={companyTax} onChange={(e) => setCompanyTax(e.target.value)} placeholder="12345678-1-42" className={inputCls} />
                </Field>
              ) : (
                <>
                <Field label={editing ? t("emp.taxIdLabelEdit") : t("emp.taxIdLabel")}>
                  <input value={form.tax_id} onChange={(e) => set("tax_id", e.target.value)} placeholder="8xxxxxxxxx" className={fieldCls("tax_id")} />
                  {fieldErrors.tax_id && <p className="mt-1 text-xs text-red-600">{fieldErrors.tax_id}</p>}
                </Field>
                <Field label={editing ? t("emp.tajLabelEdit") : t("emp.tajLabel")}>
                  <input value={form.taj} onChange={(e) => set("taj", e.target.value)} placeholder="xxx xxx xxx" className={fieldCls("taj")} />
                  {fieldErrors.taj && <p className="mt-1 text-xs text-red-600">{fieldErrors.taj}</p>}
                </Field>
                </>
              )}
              <Field label={editing ? t("emp.bankLabelEdit") : t("emp.bankLabel")}>
                <input value={form.bank_account} onChange={(e) => set("bank_account", e.target.value)} placeholder="xxxxxxxx-xxxxxxxx" className={fieldCls("bank_account")} />
                {fieldErrors.bank_account && <p className="mt-1 text-xs text-red-600">{fieldErrors.bank_account}</p>}
              </Field>
              {!contractor && (
              <Field label={t("emp.wage")}>
                <input value={form.wage_amount} onChange={(e) => set("wage_amount", e.target.value)} placeholder={t("emp.wagePlaceholder")} className={inputCls} />
              </Field>
              )}
            </fieldset>

            <fieldset className={`grid grid-cols-1 gap-3 sm:grid-cols-2 ${contractor ? "hidden" : ""}`}>
              <legend className="mb-1 text-sm font-semibold text-slate-700">{t("emp.employmentSection")}</legend>
              <Field label={t("emp.hireDateLabel")}>
                <input required={!contractor} type="date" value={form.hire_date} onChange={(e) => set("hire_date", e.target.value)} className={inputCls} />
              </Field>
              <Field label={t("emp.jobTitleLabel")}>
                <input value={form.job_title} onChange={(e) => set("job_title", e.target.value)} className={inputCls} />
              </Field>
              <Field label={t("emp.feor")}>
                <input value={form.feor_code} onChange={(e) => set("feor_code", e.target.value)} placeholder="9223" className={inputCls} />
              </Field>
              <Field label={t("emp.weeklyHoursLabel")}>
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={form.weekly_hours}
                  onChange={(e) => set("weekly_hours", Number(e.target.value))}
                  className={inputCls}
                />
              </Field>
              <Field label={t("emp.employmentType")}>
                <select value={form.employment_type} onChange={(e) => set("employment_type", e.target.value)} className={inputCls}>
                  <option value="full_time">{t("emp.fullTime")}</option>
                  <option value="part_time">{t("emp.partTime")}</option>
                </select>
              </Field>
              <Field label={t("emp.contractType")}>
                <select value={form.contract_type} onChange={(e) => set("contract_type", e.target.value)} className={inputCls}>
                  <option value="indefinite">{t("emp.indefinite")}</option>
                  <option value="fixed_term">{t("emp.fixedTerm")}</option>
                </select>
              </Field>
              <Field label={t("emp.wageType")}>
                <select value={form.wage_type} onChange={(e) => set("wage_type", e.target.value)} className={inputCls}>
                  <option value="monthly">{t("emp.monthly")}</option>
                  <option value="hourly">{t("emp.hourly")}</option>
                </select>
              </Field>
              <Field label={t("emp.leaveDays")}>
                <input
                  type="number"
                  min={0}
                  max={60}
                  value={form.annual_leave_days}
                  onChange={(e) => set("annual_leave_days", Number(e.target.value))}
                  className={inputCls}
                />
              </Field>
              <Field label={t("emp.payrollSource")}>
                <select
                  value={form.payroll_source}
                  onChange={(e) => set("payroll_source", e.target.value as "attendance" | "schedule")}
                  className={inputCls}
                >
                  <option value="attendance">{t("emp.payrollAttendance")}</option>
                  <option value="schedule">{t("emp.payrollSchedule")}</option>
                </select>
                <span className="mt-1 block text-xs text-slate-400">{t("emp.payrollSourceHint")}</span>
              </Field>
            </fieldset>

            <fieldset>
              <legend className="mb-1 text-sm font-semibold text-slate-700">{t("emp.availabilitySection")}</legend>
              <p className="mb-2 text-xs text-slate-400">{t("emp.availabilityHint")}</p>
              <div className="space-y-1">
                {["0", "1", "2", "3", "4", "5", "6"].map((day) => (
                  <div key={day} className="flex items-center gap-2 text-sm">
                    <label className="flex w-28 items-center gap-2">
                      <input
                        type="checkbox"
                        checked={avail[day].on}
                        onChange={(e) => setAvail({ ...avail, [day]: { ...avail[day], on: e.target.checked } })}
                        className="h-4 w-4"
                      />
                      {t(`emp.day${day}`)}
                    </label>
                    {avail[day].on && (
                      <>
                        <input
                          type="time"
                          value={avail[day].from}
                          onChange={(e) => setAvail({ ...avail, [day]: { ...avail[day], from: e.target.value } })}
                          className="rounded-lg border border-slate-300 px-2 py-1 text-sm"
                        />
                        <span className="text-slate-400">–</span>
                        <input
                          type="time"
                          value={avail[day].to}
                          onChange={(e) => setAvail({ ...avail, [day]: { ...avail[day], to: e.target.value } })}
                          className="rounded-lg border border-slate-300 px-2 py-1 text-sm"
                        />
                      </>
                    )}
                  </div>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="mb-1 text-sm font-semibold text-slate-700">{t("emp.skillsSection")}</legend>
              {allSkills.length === 0 ? (
                <p className="text-xs text-slate-400">{t("emp.noSkillsYet")}</p>
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

            <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <legend className="mb-1 text-sm font-semibold text-slate-700">{t("emp.accountSection")}</legend>
              {!editing && (
                <>
                  <Field label={t("emp.loginEmail")}>
                    <input required type="email" value={form.email} onChange={(e) => set("email", e.target.value)} className={inputCls} />
                  </Field>
                  <Field label={t("emp.initialPassword")}>
                    <input value={form.initial_password} onChange={(e) => set("initial_password", e.target.value)} minLength={form.initial_password ? 10 : undefined} className={inputCls} />
                  </Field>
                </>
              )}
              <Field label={t("emp.loginRole")}>
                <select
                  value={form.role}
                  onChange={(e) => set("role", e.target.value as (typeof LOGIN_ROLES)[number])}
                  className={inputCls}
                >
                  {LOGIN_ROLES.map((r) => (
                    <option key={r} value={r}>{t(`roles.${r}`)}</option>
                  ))}
                </select>
              </Field>
            </fieldset>

            {editing && (
              <fieldset className="rounded-xl border border-red-200 bg-red-50 p-3">
                <legend className="px-1 text-xs font-semibold uppercase text-red-500">
                  {t("emp.terminationSection")}
                </legend>
                <label className="flex items-start gap-2 text-sm text-red-900">
                  <input
                    type="checkbox"
                    checked={archive}
                    onChange={(e) => setArchive(e.target.checked)}
                    className="mt-0.5 h-4 w-4"
                  />
                  <span>
                    <span className="font-semibold">{t("emp.terminateCheckbox")}</span>
                    <span className="mt-0.5 block text-xs text-red-700">{t("emp.terminateHint")}</span>
                  </span>
                </label>
                {archive && (
                  <label className="mt-2 block text-sm text-red-900">
                    {t("emp.terminationDate")}
                    <input
                      type="date"
                      value={termDate}
                      onChange={(e) => setTermDate(e.target.value)}
                      className="mt-1 w-48 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm"
                    />
                  </label>
                )}
              </fieldset>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100"
              >
                {t("common.cancel")}
              </button>
              <button
                disabled={busy}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {busy ? t("common.saving") : t("common.save")}
              </button>
            </div>
          </form>
        </div>
      )}
    </AppShell>
  );
}
