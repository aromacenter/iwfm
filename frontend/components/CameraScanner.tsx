"use client";

/** Kamerás vonalkód-olvasó (BarcodeDetector API — Chrome/Android/Edge).
 * Ahol a böngésző nem támogatja (pl. régebbi iOS Safari), a gomb nem jelenik
 * meg — ott marad a kézi beírás / hardveres szkenner. */

import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n";

/* eslint-disable @typescript-eslint/no-explicit-any */

export function cameraScanSupported(): boolean {
  return typeof window !== "undefined" && "BarcodeDetector" in window;
}

export default function CameraScanner({
  onDetect,
  onClose,
}: {
  onDetect: (value: string) => void;
  onClose: () => void;
}) {
  const { t } = useT();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef(false);

  const stop = useCallback(() => {
    stopRef.current = true;
    const stream = videoRef.current?.srcObject as MediaStream | null;
    stream?.getTracks().forEach((tr) => tr.stop());
  }, []);

  useEffect(() => {
    let detector: any;
    try {
      detector = new (window as any).BarcodeDetector({
        formats: ["code_128", "code_39", "ean_13", "ean_8", "qr_code", "itf"],
      });
    } catch {
      setError(t("scanner.unsupported"));
      return;
    }
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
        });
        if (!videoRef.current) return;
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        const tick = async () => {
          if (stopRef.current || !videoRef.current) return;
          try {
            const codes = await detector.detect(videoRef.current);
            const value = codes?.[0]?.rawValue?.trim();
            if (value) {
              stop();
              onDetect(value);
              return;
            }
          } catch {
            /* frame-hiba — próbáljuk tovább */
          }
          setTimeout(tick, 180);
        };
        void tick();
      } catch {
        setError(t("scanner.noCamera"));
      }
    })();
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      onMouseDown={(e) => { if (e.target === e.currentTarget) { stop(); onClose(); } }}
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4"
    >
      <div className="w-full max-w-md space-y-3 rounded-2xl bg-white p-4 shadow-xl">
        <div className="flex items-center justify-between">
          <p className="font-semibold">{t("scanner.title")}</p>
          <button
            onClick={() => { stop(); onClose(); }}
            className="rounded px-2 py-1 text-lg leading-none text-slate-400 hover:text-slate-700"
          >
            ✕
          </button>
        </div>
        {error ? (
          <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>
        ) : (
          <>
            {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
            <video ref={videoRef} playsInline muted className="w-full rounded-xl bg-black" />
            <p className="text-center text-xs text-slate-500">{t("scanner.hint")}</p>
          </>
        )}
      </div>
    </div>
  );
}
