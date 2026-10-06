"use client";

/** 📷 gomb: kamerás QR/vonalkód-beolvasás gép-azonosító mezőkhöz.
 * SZABÁLY: minden felületen, ahol gép azonosítóját kell megadni, ott a
 * telefon kamerájával is beolvasható a gép QR-kódja (5d25ca92, 255777ce).
 * A beolvasott QR-URL tokenjét a by-barcode végpont oldja fel vonalkódra. */

import { useState } from "react";
import CameraScanner, { cameraScanSupported } from "@/components/CameraScanner";
import { api, errorMessage } from "@/lib/api";
import { useUI } from "@/lib/ui";

export default function ScanAssetButton({
  onBarcode,
  className,
  title,
}: {
  onBarcode: (barcode: string) => void;
  className?: string;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const { toast } = useUI();
  if (!cameraScanSupported()) return null;
  return (
    <>
      <button
        type="button"
        title={title || "QR"}
        onClick={() => setOpen(true)}
        className={
          className
          || "rounded-lg border border-slate-300 px-2 py-1.5 text-sm hover:bg-slate-50"
        }
      >
        📷
      </button>
      {open && (
        <CameraScanner
          onClose={() => setOpen(false)}
          onDetect={(value) => {
            setOpen(false);
            const raw = value.trim();
            const lookup = raw.includes("/")
              ? (raw.split("?")[0].split("/").filter(Boolean).pop() ?? raw)
              : raw;
            api
              .get<{ barcode: string }>(`/api/assets/by-barcode/${encodeURIComponent(lookup)}`)
              .then((a) => onBarcode(a.barcode))
              .catch((err) => toast(errorMessage(err), "error"));
          }}
        />
      )}
    </>
  );
}
