/** Partner-egészség (127. kör): szint → szín, chip-osztály, rövid felirat. */
export type HealthLevel = "good" | "watch" | "risk";

export interface PartnerHealthRow {
  partner_id: string;
  score: number;
  level: HealthLevel;
  reasons: string[];
  components: { trend: number; payment: number; service: number; visit: number };
  last_settlement_at: string | null;
  days_since_visit: number | null;
  trend_ratio: number | null;
  outstanding: number;
  overdue_items: number;
  tickets_90d: number;
}

export const HEALTH_COLOR: Record<HealthLevel, string> = { good: "#059669", watch: "#d97706", risk: "#dc2626" };
export const HEALTH_CHIP: Record<HealthLevel, string> = {
  good: "bg-emerald-100 text-emerald-800",
  watch: "bg-amber-100 text-amber-800",
  risk: "bg-rose-100 text-rose-800",
};
export const HEALTH_ICON: Record<HealthLevel, string> = { good: "💚", watch: "🟡", risk: "🔴" };
