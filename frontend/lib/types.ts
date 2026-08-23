export type Role = "admin" | "manager" | "uzletkoto" | "szervizes" | "employee";

export interface AuthUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  permissions: string[];
}

export interface EmployeeOut {
  id: string;
  user_id: string;
  email: string | null;
  role: string | null;
  status: "active" | "inactive";
  last_name: string;
  first_name: string;
  birth_name: string | null;
  mother_name: string | null;
  birth_place: string | null;
  birth_date: string | null;
  citizenship: string;
  phone: string | null;
  address: string | null;
  residence_address: string | null;
  hire_date: string;
  termination_date: string | null;
  job_title: string | null;
  feor_code: string | null;
  employment_type: "full_time" | "part_time";
  contract_type: "indefinite" | "fixed_term";
  weekly_hours: number;
  wage_type: "monthly" | "hourly";
  annual_leave_days: number;
  // Alvállalkozó (számlás) — bármikor átváltható alkalmazottira és vissza
  is_contractor: boolean;
  company_tax_number: string | null;
  // Bérszámfejtés alapja: blokkolás (attendance) vagy beosztás (schedule)
  payroll_source: "attendance" | "schedule";
  // Heti elérhetőség: {"0": ["08:00","16:00"], …} (0=hétfő … 6=vasárnap)
  availability: Record<string, [string, string]> | null;
  notes: string | null;
  tax_id_masked: string | null;
  taj_masked: string | null;
  bank_account_masked: string | null;
  has_wage: boolean;
  employee_code: string | null;
  telegram_linked: boolean;
  skills: { id: number; name: string }[];
}

export interface ShiftOut {
  id: string;
  employee_id: string;
  work_date: string;
  start_time: string;
  end_time: string;
  break_minutes: number;
  role_label: string | null;
  location: string | null;
  note: string | null;
  status: "draft" | "published";
  published_at: string | null;
}

export interface ViolationOut {
  code: string;
  severity: "error" | "warning";
  message: string;
  params?: Record<string, string | number>;
  employee_id: string | null;
  shift_ids: string[];
}

export interface WeekOut {
  week_start: string;
  shifts: ShiftOut[];
  time_off: { id: string; employee_id: string; type: string; start_date: string; end_date: string }[];
  violations: ViolationOut[];
}

export interface TimeOffOut {
  id: string;
  employee_id: string;
  employee_name: string | null;
  type: "annual" | "sick" | "unpaid" | "other";
  start_date: string;
  end_date: string;
  reason: string | null;
  status: "pending" | "approved" | "rejected" | "cancelled";
  decided_at: string | null;
  decision_note: string | null;
  created_at: string;
}

export interface EntryOut {
  id: string;
  employee_id: string;
  employee_name: string | null;
  clock_in: string;
  clock_out: string | null;
  break_minutes: number;
  worked_minutes: number | null;
  source: "self" | "manual";
  note: string | null;
}

// A távollét-típusok és státuszok feliratai a nyelvi fájlokban élnek:
// i18n/hu.json és i18n/en.json → "leave.types" / "leave.statuses".
