"use client";

/** Kamerás vonalkód-olvasó. Ahol van BarcodeDetector API (Chrome/Android/
 * Edge), azzal olvasunk (QR + hagyományos vonalkódok); iOS-en (Safari/Chrome
 * — mindkettő WebKit, nincs BarcodeDetector) jsQR-tartalékkal QR-kódot
 * olvasunk canvasról. A gomb így iPhone-on is megjelenik. */

import jsQR from "jsqr";
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/lib/i18n";

/* eslint-disable @typescript-eslint/no-explicit-any */

export function cameraScanSupported(): boolean {
  // BarcodeDetector VAGY kamera-hozzáférés (jsQR-tartalék) elegendő.
  return (
    typeof window !== "undefined"
    && ("BarcodeDetector" in window || !!navigator.mediaDevices?.getUserMedia)
  );
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
    // BarcodeDetector, ha van; különben jsQR-tartalék (QR-kód canvasról) —
    // az iOS-böngészők (WebKit) csak az utóbbit tudják.
    let detector: any = null;
    if ("BarcodeDetector" in window) {
      try {
        detector = new (window as any).BarcodeDetector({
          formats: ["code_128", "code_39", "ean_13", "ean_8", "qr_code", "itf"],
        });
      } catch {
        detector = null;
      }
    }
    if (detector === null && !navigator.mediaDevices?.getUserMedia) {
      setError(t("scanner.unsupported"));
      return;
    }
    const canvas = document.createElement("canvas");
    const canvasCtx = canvas.getContext("2d", { willReadFrequently: true });
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
            let value: string | undefined;
            if (detector) {
              const codes = await detector.detect(videoRef.current);
              value = codes?.[0]?.rawValue?.trim();
            } else if (canvasCtx && videoRef.current.videoWidth > 0) {
              // jsQR: a videó képkockáját canvasra rajzoljuk és QR-t keresünk
              const w = Math.min(videoRef.current.videoWidth, 640);
              const h = Math.round(
                (videoRef.current.videoHeight / videoRef.current.videoWidth) * w,
              );
              canvas.width = w;
              canvas.height = h;
              canvasCtx.drawImage(videoRef.current, 0, 0, w, h);
              const img = canvasCtx.getImageData(0, 0, w, h);
              const code = jsQR(img.data, w, h, { inversionAttempts: "dontInvert" });
              value = code?.data?.trim();
            }
            if (value) {
              stop();
              onDetect(value);
              return;
            }
          } catch {
            /* frame-hiba — próbáljuk tovább */
          }
          setTimeout(tick, detector ? 180 : 260);
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
