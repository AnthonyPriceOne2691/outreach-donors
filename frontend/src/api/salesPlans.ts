/**
 * План работ: превью книги, сохранение по отпечатку превью и список планов (`api/sales/plans.py`).
 *
 * Книга уходит формой оба раза: сервер разбирает её заново и сохраняет только то, что человек
 * видел в превью (отпечаток `digest`). Ключи листа в превью — счётом и примерами.
 */

import { request, upload } from './client';

const BASE = '/sales/plans';

/** Режим плана: экономный — выдача «стандарт», быстрый — Live. */
export type PlanMode = 'economy' | 'fast';

/** Выбор окна загрузки. Пустая цель — `null`; потолки — доллары. */
export interface PlanChoice {
  mode: PlanMode;
  goal: number | null;
  capUsd: number | null;
  hypothesisCapUsd: number | null;
}

/** Проход сметы: запросы выдачи, сайты, деньги «выдача + модель». */
export interface PlanSpend {
  queries: number;
  results: number;
  sites: number;
  serp_usd: number;
  tokens: number;
  tokens_usd: number;
  hunter_searches: number;
  hunter_checks: number;
  usd: number;
}

export interface PlanSheetKeys {
  taken: number;
  blank: number;
  repeats: number;
  rejected: number;
  examples: string[];
  /** Отсеянный ключ → причина словами. */
  rejected_examples: Record<string, string>;
}

export interface PlanSheetIssue {
  /** Поле карточки; `null` — лист целиком. */
  field: string | null;
  words: string;
}

export interface PlanSheet {
  sheet: string;
  name: string;
  segment: string;
  markets: string[];
  language: string;
  other_countries: string;
  excluded_countries: string[];
  /** Своя цель листа; `null` — общая из окна. */
  target: number | null;
  depth_pages: number;
  keys: PlanSheetKeys;
  issues: PlanSheetIssue[];
  warnings: string[];
  /** Смета — у листа без ошибок: от топ-10 до предельной глубины. */
  low: PlanSpend | null;
  high: PlanSpend | null;
  /** Цель, по которой посчитана смета: своя или общая. */
  goal: number | null;
}

export interface PlanPreview {
  file: string;
  sheets: PlanSheet[];
  skipped: Record<string, string>;
  serp_mode: string;
  low: PlanSpend;
  high: PlanSpend;
  share: number;
  share_runs: number;
  tokens_per_site: number;
  tokens_per_site_runs: number;
  notes: string[];
  /** Почему план не сохранить; пусто — можно. */
  refusals: string[];
  digest: string;
}

export interface PlanHypothesis {
  id: number;
  hypothesis_id: number;
  name: string;
  position: number;
  sheet: string;
  markets: string[];
  language: string;
  target: number;
  max_pages: number;
  cap_usd: number;
  stage: string;
  found: number;
  spent_usd: number;
  keywords: number;
  bought: number;
}

export interface PlanCard {
  id: number;
  name: string;
  mode: PlanMode;
  target_default: number | null;
  cap_usd: number;
  status: string;
  problem: string | null;
  created_by: string | null;
  created_at: string;
  hypotheses: PlanHypothesis[];
}

export interface PlansView {
  plans: PlanCard[];
}

function formOf(file: File, choice: PlanChoice, digest?: string): FormData {
  const form = new FormData();
  form.append('file', file);
  form.append('mode', choice.mode);
  const fields: [string, number | null][] = [
    ['goal', choice.goal],
    ['cap_usd', choice.capUsd],
    ['hypothesis_cap_usd', choice.hypothesisCapUsd],
  ];
  for (const [name, value] of fields) {
    if (value !== null) form.append(name, String(value));
  }
  if (digest !== undefined) form.append('digest', digest);
  return form;
}

export function previewPlan(file: File, choice: PlanChoice): Promise<PlanPreview> {
  return upload<PlanPreview>(`${BASE}/preview`, formOf(file, choice));
}

export function savePlan(file: File, choice: PlanChoice, digest: string): Promise<PlanCard> {
  return upload<PlanCard>(BASE, formOf(file, choice, digest));
}

export function listPlans(): Promise<PlansView> {
  return request<PlansView>(BASE);
}
