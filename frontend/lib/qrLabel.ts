/** Új gép QR-címkéje (123. kör, x-presso jegy ab89c1a6): ha van beállított
 * Godex nyomtató-ügynök, a matrica a nyomtatási sorba megy; csak enélkül
 * (vagy jogosultság híján) marad a PDF-letöltés. */
import { api, downloadFile } from "@/lib/api";

type T = (key: string, vars?: Record<string, string | number>) => string;

export async function offerQrLabel(
  asset: { id: string; barcode: string },
  ui: { t: T; confirm: (text: string) => Promise<boolean>; toast: (text: string, type?: "success" | "error" | "info") => void },
  errorMessage: (err: unknown) => string,
): Promise<void> {
  const { t, confirm, toast } = ui;
  let agent = false;
  try {
    agent = (await api.get<{ agent_configured: boolean }>("/api/print-jobs")).agent_configured;
  } catch {
    agent = false;
  }
  if (agent) {
    if (!(await confirm(t("tasks.newAssetQrPrintConfirm", { barcode: asset.barcode })))) return;
    try {
      await api.post("/api/print-jobs", { ids: [asset.id] });
      toast(t("inv.godexQueued", { count: 1 }), "success");
      return;
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }
  if (await confirm(t("tasks.newAssetQrConfirm", { barcode: asset.barcode }))) {
    try {
      await downloadFile(`/api/assets/${asset.id}/qr-label`, `QR-${asset.barcode}.pdf`);
    } catch (err) {
      toast(errorMessage(err), "error");
    }
  }
}
