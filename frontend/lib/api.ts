/** API kliens — httpOnly cookie alapú session, hibakód-továbbítással.
 *
 * Minden kérés RELATÍV (/api/...) — a Next.js rewrite proxyzza a backendhez
 * (lásd next.config.ts). Így a süti first-party, és nincs cross-site CORS. */

export const API_URL = "";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: unknown;

  constructor(status: number, code: string, detail: unknown) {
    super(code);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json())?.detail ?? null;
    } catch {
      /* nem JSON válasz */
    }
    const code =
      typeof detail === "object" && detail !== null && "code" in detail
        ? String((detail as { code: string }).code)
        : `http.${res.status}`;
    throw new ApiError(res.status, code, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/** Fájlletöltés cookie-s auth-tal (bérexport). */
export async function downloadFile(path: string, filename: string) {
  const res = await fetch(`${API_URL}${path}`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, `http.${res.status}`, null);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Emberi hibaüzenetek a backend hibakódjaihoz. */
const ERROR_MESSAGES: Record<string, string> = {
  "auth.bad_credentials": "Hibás email vagy jelszó.",
  "auth.rate_limited": "Túl sok próbálkozás — várj 15 percet.",
  "auth.bootstrap_closed": "Az első admin már létrejött, jelentkezz be.",
  "auth.forbidden": "Ehhez nincs jogosultságod.",
  "auth.missing": "Jelentkezz be.",
  "auth.invalid": "A munkamenet lejárt, jelentkezz be újra.",
  "employee.email_taken": "Ezzel az email címmel már létezik fiók.",
  "employee.invalid_ids": "Hibás azonosító (adóazonosító / TAJ / bankszámla).",
  "employee.not_found": "A dolgozó nem található.",
  "employee.none_for_user": "Ehhez a fiókhoz nem tartozik dolgozói adatlap.",
  "shift.on_time_off": "A dolgozónak jóváhagyott távolléte van ezen a napon.",
  "shift.bad_employee": "Érvénytelen dolgozó.",
  "shift.modify_notice": "A közölt beosztás 96 órán belül csak megerősítéssel módosítható (Mt. 97.§ (5)).",
  "publish.blocked": "A közzétételt munkajogi hiba akadályozza.",
  "publish.needs_confirmation": "A közzététel figyelmeztetésekkel jár — megerősítés szükséges.",
  "publish.nothing_to_publish": "Nincs közzétehető (piszkozat) műszak ezen a héten.",
  "timeoff.already_decided": "Ez a kérelem már el lett bírálva.",
  "timeoff.bad_range": "Hibás dátumtartomány.",
  "timeclock.already_open": "Már van nyitott bejelentkezésed.",
  "timeclock.not_open": "Nincs nyitott bejelentkezésed.",
  "timeclock.bad_range": "A kijelentkezés nem lehet a bejelentkezés előtt.",
  "payroll.bad_range": "Hibás időszak.",
  "payroll.range_too_long": "Az időszak legfeljebb 3 hónap lehet.",
  "settings.email_not_configured": "Előbb mentsd el az email-beállításokat (bekapcsolva).",
  "settings.email_send_failed": "Az email küldése nem sikerült — ellenőrizd az SMTP adatokat.",
  "settings.ai_not_configured": "Előbb mentsd el az API kulcsot ehhez a szolgáltatóhoz.",
  "settings.ai_test_failed": "A szolgáltató nem válaszolt — ellenőrizd a kulcsot és a modellnevet.",
  "settings.ai_bad_provider": "Ismeretlen AI szolgáltató.",
  "skills.name_taken": "Ilyen nevű skill már létezik.",
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return ERROR_MESSAGES[err.code] ?? `Hiba történt (${err.code}).`;
  }
  return "Hálózati hiba — fut a backend?";
}
