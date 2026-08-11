"use client";

/** AI-asszisztens — Sidekick-stílusú, jobb oldalra DOKKOLT panel: asztali
 * nézetben a tartalom mellé rendeződik (nem takar), mobilon teljes képernyő.
 * A nyitott állapot és az előzmény munkameneten belül megmarad (navigálás
 * közben is). A szerveroldali eszköz-hurokkal beszél (/api/assistant/chat);
 * a műveletek chipként jelennek meg, a navigate eseményre átnavigál. */

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface ChatEvent {
  kind: string;
  label: string;
  path?: string | null;
}

interface Msg {
  role: "user" | "assistant";
  content: string;
  events?: ChatEvent[];
}

const STORE_KEY = "iwfm-assistant-chat";
const OPEN_KEY = "iwfm-assistant-open";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Recognition = any;

function getRecognition(): Recognition | null {
  if (typeof window === "undefined") return null;
  const w = window as any;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

/** Mini-markdown: **félkövér** megjelenítése (a modell gyakran használja). */
function rich(text: string) {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return parts.map((p, i) =>
    i % 2 === 1 ? <strong key={i}>{p}</strong> : <Fragment key={i}>{p}</Fragment>,
  );
}

export default function AssistantChat() {
  const { t, lang } = useT();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [micAvailable, setMicAvailable] = useState(false);
  const recRef = useRef<Recognition | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const baseTextRef = useRef("");

  // előzmények + nyitott állapot visszatöltése (munkameneten belül)
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(STORE_KEY);
      if (saved) setMessages(JSON.parse(saved) as Msg[]);
      if (sessionStorage.getItem(OPEN_KEY) === "1") setOpen(true);
    } catch {
      /* sérült mentés — üresen indulunk */
    }
    setMicAvailable(getRecognition() !== null);
  }, []);

  useEffect(() => {
    try {
      sessionStorage.setItem(STORE_KEY, JSON.stringify(messages.slice(-30)));
    } catch {
      /* tárhely-hiba nem kritikus */
    }
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, open, busy]);

  function setOpenPersist(next: boolean) {
    setOpen(next);
    try {
      sessionStorage.setItem(OPEN_KEY, next ? "1" : "0");
    } catch {
      /* nem kritikus */
    }
  }

  const stopListening = useCallback(() => {
    recRef.current?.stop();
    recRef.current = null;
    setListening(false);
  }, []);

  function startListening() {
    const rec = getRecognition();
    if (!rec) return;
    baseTextRef.current = input ? input.trim() + " " : "";
    rec.lang = lang === "hu" ? "hu-HU" : "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e: any) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      setInput(baseTextRef.current + text);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recRef.current = rec;
    setListening(true);
    rec.start();
  }

  async function send(textOverride?: string) {
    const text = (textOverride ?? input).trim();
    if (!text || busy) return;
    stopListening();
    const history: Msg[] = [...messages, { role: "user", content: text }];
    setMessages(history);
    setInput("");
    setBusy(true);
    try {
      const res = await api.post<{ reply: string; events: ChatEvent[] }>(
        "/api/assistant/chat",
        {
          messages: history.slice(-20).map((m) => ({ role: m.role, content: m.content })),
        },
      );
      setMessages((ms) => [
        ...ms,
        { role: "assistant", content: res.reply, events: res.events },
      ]);
      const nav = res.events.find((e) => e.kind === "navigate" && e.path);
      if (nav?.path) router.push(nav.path);
    } catch (err) {
      const notConfigured =
        err instanceof ApiError && err.code === "assistant.ai_not_configured";
      setMessages((ms) => [
        ...ms,
        {
          role: "assistant",
          content: notConfigured ? t("assistant.notConfigured") : errorMessage(err),
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpenPersist(true)}
        title={t("assistant.open")}
        className="fixed bottom-5 right-5 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-indigo-600 text-2xl text-white shadow-lg hover:bg-indigo-700"
      >
        ✨
      </button>
    );
  }

  return (
    // lg-n: flex-gyerek az AppShell sorában → a tartalom MELLÉ rendeződik
    <div className="lg:w-[400px] lg:shrink-0 lg:border-l lg:border-slate-200 lg:bg-white">
      <div className="fixed inset-0 z-50 flex flex-col bg-white lg:sticky lg:inset-auto lg:top-0 lg:z-auto lg:h-screen">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-lg">✨</span>
            <span className="font-semibold">{t("assistant.title")}</span>
          </div>
          <div className="flex items-center gap-1">
            {messages.length > 0 && (
              <button
                onClick={() => setMessages([])}
                title={t("assistant.clear")}
                className="rounded px-2 py-1 text-sm text-slate-400 hover:text-slate-700"
              >
                🗑
              </button>
            )}
            <button
              onClick={() => { stopListening(); setOpenPersist(false); }}
              title={t("common.close")}
              className="rounded px-2 py-1 text-lg leading-none text-slate-400 hover:text-slate-700"
            >
              ✕
            </button>
          </div>
        </div>

        <div ref={listRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
          {messages.length === 0 && (
            <div className="mt-6 space-y-3 text-sm text-slate-500">
              <p className="font-medium text-slate-700">{t("assistant.hello")}</p>
              <p>{t("assistant.examples")}</p>
              <div className="flex flex-col items-start gap-2">
                {(["ex1", "ex2", "ex3", "ex4"] as const).map((k) => (
                  <button
                    key={k}
                    onClick={() => void send(t(`assistant.${k}`).replace(/^[„"]|["”]$/g, ""))}
                    className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs text-slate-600 hover:border-indigo-300 hover:bg-indigo-50"
                  >
                    {t(`assistant.${k}`)}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div
                className={
                  m.role === "user"
                    ? "max-w-[88%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-indigo-600 px-3 py-2 text-sm text-white"
                    : "max-w-[88%] whitespace-pre-wrap rounded-2xl rounded-bl-sm bg-slate-100 px-3 py-2 text-sm text-slate-800"
                }
              >
                {rich(m.content)}
                {m.events && m.events.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {m.events.map((e, j) => (
                      <button
                        key={j}
                        onClick={() => e.path && router.push(e.path)}
                        className={`rounded-full border px-2 py-0.5 text-xs ${
                          e.kind === "navigate"
                            ? "border-indigo-200 bg-indigo-50 text-indigo-700"
                            : "border-emerald-200 bg-emerald-50 text-emerald-700"
                        } ${e.path ? "cursor-pointer hover:bg-indigo-100" : "cursor-default"}`}
                      >
                        {e.kind === "created" || e.kind === "updated" ? "✔ " : ""}
                        {e.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-sm bg-slate-100 px-3 py-2 text-sm text-slate-500">
                {t("assistant.thinking")}
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-slate-200 p-3">
          {listening && (
            <p className="mb-1 animate-pulse text-xs font-medium text-rose-600">
              🎙 {t("assistant.listening")}
            </p>
          )}
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={2}
              placeholder={t("assistant.placeholder")}
              className="flex-1 resize-none rounded-xl border border-slate-300 px-3 py-2 text-sm"
            />
            {micAvailable && (
              <button
                onClick={() => (listening ? stopListening() : startListening())}
                title={t("assistant.micHint")}
                className={`h-10 w-10 shrink-0 rounded-full text-lg ${
                  listening
                    ? "bg-rose-600 text-white"
                    : "border border-slate-300 bg-white hover:bg-slate-100"
                }`}
              >
                🎙
              </button>
            )}
            <button
              onClick={() => void send()}
              disabled={busy || !input.trim()}
              className="h-10 shrink-0 rounded-xl bg-indigo-600 px-4 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              ➤
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
