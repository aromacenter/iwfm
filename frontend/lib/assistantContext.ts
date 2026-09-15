"use client";

/** Könnyű híd az oldalak és az AI-asszisztens között: az aktuális oldal
 * beállíthatja a képernyő-kontextust (pl. a Gépek oldalon pipával kijelölt
 * gépek), az AssistantChat pedig minden üzenethez csatolja. Modul-szintű
 * változó — nincs render-függés, nem kell provider. */

export interface AssistantContext {
  page?: string;
  /** Kijelölt gépek ember-olvasható címkéi (vonalkód + név). */
  selected_machines?: string[];
}

let current: AssistantContext | null = null;

export function setAssistantContext(ctx: AssistantContext | null) {
  current = ctx && (ctx.page || ctx.selected_machines?.length) ? ctx : null;
}

export function getAssistantContext(): AssistantContext | null {
  return current;
}
