/** Saját számlázó cégek — a partner szerződött cége dönti el, ki számláz. */

export type CompanyKey = "xp" | "pc";

export const COMPANIES: Record<CompanyKey, string> = {
  xp: "X-Presso Coffee Kft.",
  pc: "Premium Caffe Kft.",
};

export const COMPANY_SHORT: Record<CompanyKey, string> = {
  xp: "X-Presso",
  pc: "Premium Caffe",
};

export const COMPANY_CHIP: Record<CompanyKey, string> = {
  xp: "bg-blue-100 text-blue-800",
  pc: "bg-purple-100 text-purple-800",
};
