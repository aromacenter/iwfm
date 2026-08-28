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
  triggerDownload(blob, filename);
}

/** POST-kéréssel előállított fájl letöltése (pl. tömeges QR-címkeív). */
export async function downloadFilePost(path: string, body: unknown, filename: string) {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, `http.${res.status}`, null);
  const blob = await res.blob();
  triggerDownload(blob, filename);
}

/** PDF megnyitása közvetlenül a böngésző nyomtatási ablakában (rejtett
 *  iframe-ben töltjük be, majd print() — nem indul letöltés). */
export async function printFile(path: string) {
  const res = await fetch(`${API_URL}${path}`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, `http.${res.status}`, null);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const iframe = document.createElement("iframe");
  iframe.style.position = "fixed";
  iframe.style.right = "0";
  iframe.style.bottom = "0";
  iframe.style.width = "0";
  iframe.style.height = "0";
  iframe.style.border = "0";
  iframe.src = url;
  iframe.onload = () => {
    try {
      iframe.contentWindow?.focus();
      iframe.contentWindow?.print();
    } catch {
      window.open(url, "_blank");
    }
  };
  document.body.appendChild(iframe);
  // az iframe-et nyitva hagyjuk, amíg a nyomtatási ablak él; később takarítjuk
  setTimeout(() => {
    URL.revokeObjectURL(url);
    iframe.remove();
  }, 120_000);
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Telefon natív megosztás (Web Share API) — ha nem támogatott (vagy asztali),
 *  letöltésre esik vissza. Visszatérés: "shared" | "downloaded" | "cancelled". */
export async function shareOrDownloadFile(
  path: string,
  filename: string,
): Promise<"shared" | "downloaded" | "cancelled"> {
  const res = await fetch(`${API_URL}${path}`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, `http.${res.status}`, null);
  const blob = await res.blob();
  const file = new File([blob], filename, { type: blob.type || "application/pdf" });
  const nav = navigator as Navigator & {
    canShare?: (data: { files: File[] }) => boolean;
    share?: (data: { files: File[]; title?: string }) => Promise<void>;
  };
  if (nav.share && nav.canShare && nav.canShare({ files: [file] })) {
    try {
      await nav.share({ files: [file], title: filename });
      return "shared";
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return "cancelled";
      // egyéb hiba → letöltés
    }
  }
  triggerDownload(blob, filename);
  return "downloaded";
}

/** Emberi hibaüzenetek a backend hibakódjaihoz — a nyelvi fájlokból
 * (i18n/hu.json, i18n/en.json → "errors" szakasz). A lenti táblázat már csak
 * tartalék, ha egy kód hiányozna a szótárból. */
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
  "tasks.no_candidates": "Nincs aktív dolgozó, akire a feladat kiosztható lenne.",
  "worksheet.not_found": "Ehhez a feladathoz még nincs munkalap kitöltve.",
  "worksheet.bad_signature": "Hibás aláírás-formátum — rajzold újra.",
  "tasks.ai_suggest_failed": "Az AI javaslat nem sikerült — próbáld újra, vagy válassz kézzel.",
  "skills.name_taken": "Ilyen nevű skill már létezik.",
};

interface ValidationItem {
  loc?: (string | number)[];
  msg?: string;
}

/** Pydantic/FastAPI validációs üzenet → lefordított, emberi szöveg. */
function translateValidationMsg(
  msg: string,
  translate: (key: string, params?: Record<string, string | number>) => string,
): string {
  const rules: [RegExp, (m: RegExpMatchArray) => string][] = [
    [/valid email address/i, () => translate("validation.email")],
    [/Field required/i, () => translate("validation.required")],
    [/valid integer/i, () => translate("validation.integer")],
    [/valid number/i, () => translate("validation.number")],
    [/at most (\d+) characters?/i, (m) => translate("validation.tooLong", { max: m[1] })],
    [/at least (\d+) characters?/i, (m) => translate("validation.tooShort", { min: m[1] })],
    [/greater than or equal to (\S+)/i, (m) => translate("validation.min", { min: m[1] })],
    [/less than or equal to (\S+)/i, (m) => translate("validation.max", { max: m[1] })],
    [/at least (\d+) items?/i, (m) => translate("validation.minItems", { min: m[1] })],
  ];
  // Saját validátoraink: "Value error, partner.bad_type" → errors.* kulcs
  const custom = msg.match(/^Value error,\s*(\S+)$/);
  if (custom) {
    const translated = translate(`errors.${custom[1]}`);
    if (translated !== `errors.${custom[1]}`) return translated;
  }
  for (const [pattern, build] of rules) {
    const m = msg.match(pattern);
    if (m) return build(m);
  }
  return msg;
}

/** Mezőnév → emberi címke (az impex.fields szótárból, ha van). */
function fieldLabel(
  field: string,
  translate: (key: string) => string,
): string {
  const viaImpex = translate(`impex.fields.${field}`);
  if (viaImpex !== `impex.fields.${field}`) return viaImpex;
  return field;
}

export function errorMessage(err: unknown): string {
  // Lusta import, hogy ne legyen körkörös függés a modulok között.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { translate } = require("@/lib/i18n") as typeof import("@/lib/i18n");
  if (err instanceof ApiError) {
    // Külső szolgáltatás (pl. MyGLS) konkrét hibaszövege: mutassuk, ne a
    // generikus "Szerverhiba" jöjjön — enélkül nem lehet tudni, mi a gond.
    const detailMsg =
      err.detail && typeof err.detail === "object"
        ? (err.detail as { message?: unknown }).message
        : undefined;
    if (typeof detailMsg === "string" && detailMsg) {
      return translate(`errors.${err.code}.withMessage`) !==
        `errors.${err.code}.withMessage`
        ? translate(`errors.${err.code}.withMessage`, { message: detailMsg })
        : detailMsg;
    }
    const localized = translate(`errors.${err.code}`);
    if (localized !== `errors.${err.code}`) return localized;
    if (ERROR_MESSAGES[err.code]) return ERROR_MESSAGES[err.code];

    // FastAPI/Pydantic validációs hiba: mezőnkénti pontos üzenet
    if (err.status === 422 && Array.isArray(err.detail)) {
      const parts = (err.detail as ValidationItem[]).slice(0, 5).map((item) => {
        const locs = (item.loc ?? []).filter(
          (x): x is string => typeof x === "string" && x !== "body",
        );
        const field = locs.length > 0 ? locs[locs.length - 1] : "";
        const msg = translateValidationMsg(item.msg ?? "", translate);
        return field ? `${fieldLabel(field, translate)}: ${msg}` : msg;
      });
      return translate("errors.validation", { details: parts.join("; ") });
    }

    // Kódolatlan HTTP-hibák érthető szöveggel
    const httpKey = `errors.http.${err.status}`;
    const httpMsg = translate(httpKey);
    if (httpMsg !== httpKey) return httpMsg;
    if (err.status >= 500) return translate("errors.server", { status: err.status });

    return translate("errors.unknown", { code: err.code });
  }
  return translate("errors.network");
}
