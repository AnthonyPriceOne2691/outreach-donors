/**
 * Запросы и типы вкладки «Сбор базы» — прогон гипотезы: ключи → выдача → судья сегмента →
 * адрес → лиды (`backend/api/sales/segment.py`). Своим файлом раздела: общие `api/sales*.ts`
 * у предела длины, а типы этой вкладки больше никто не читает.
 *
 * Сервер считает смету и отказывает словами — экран показывает их как есть: что дорого,
 * чего не хватает и почему нельзя, решает правило сервера, а не копия на экране.
 */

import { request } from '../api/client';

/** Режим судьи сегмента — как у судьи площадки: наблюдает, режет, выключен. */
export type SegmentMode = 'shadow' | 'enforce' | 'off';

/** Полоса сайта: ещё не судили, принят, на проверке, отсечён. */
export type SegmentLane = 'pending' | 'accept' | 'review' | 'reject';

/** Где прогон: что сделано последним. */
export type SegmentStage = 'queued' | 'serp' | 'judge' | 'contacts' | 'done' | 'stopped';

export interface SegmentRunRow {
  id: number;
  created_at: string;
  finished_at: string | null;
  created_by: string | null;
  country: string;
  language: string;
  depth_pages: number;
  keywords: number;
  mode: SegmentMode;
  stage: SegmentStage;
  error: string | null;
  job_id: string | null;
}

export interface SegmentHome {
  hypothesis_id: number;
  /** Описание сегмента гипотезы — его читает судья. Пусто — описания нет. */
  segment: string | null;
  mode: SegmentMode;
  countries: string[];
  languages: string[];
  max_keywords: number;
  max_depth: number;
  runs: SegmentRunRow[];
}

/** Что просят посчитать и запустить. Язык пуст — язык рынка выдачи. */
export interface SegmentAsk {
  hypothesis_id: number;
  keywords: string;
  country: string;
  language: string | null;
  depth_pages: number;
}

export interface SegmentEstimate {
  keywords: string[];
  /** Отсеянный ключ → почему, словами. */
  rejected: Record<string, string>;
  country: string;
  language: string;
  depth_pages: number;
  mode: SegmentMode;
  results: number;
  sites: number;
  serp_usd: number;
  tokens: number;
  tokens_left: number | null;
  hunter_searches: number;
  hunter_checks: number;
  /** Смета словами — строкой на статью: выдача, судья, Hunter. */
  lines: string[];
  /** Запускать нельзя — почему, словами сервера. */
  refusal: string | null;
}

export interface SegmentCandidate {
  id: number;
  host: string;
  page_url: string | null;
  page_title: string | null;
  keyword: string | null;
  lane: SegmentLane;
  /** Что сказал судья сам: при наблюдении «отказ» лежит здесь, а сайт — на проверке. */
  verdict: SegmentLane | null;
  reason: string | null;
  quote: string | null;
  unanswered: boolean;
  decided_by_user: string | null;
}

export interface SegmentCount {
  words: string;
  count: number;
}

export interface SegmentSummary {
  hosts: number;
  accepted: number;
  review: number;
  rejected: number;
  no_address: number;
  leads: number;
}

export interface SegmentRunDetail {
  run: SegmentRunRow;
  hypothesis: string;
  keywords: string[];
  estimate_lines: string[];
  summary: SegmentSummary;
  lanes: Record<string, number>;
  rejected: SegmentCount[];
  no_address: SegmentCount[];
  waiting_address: number;
  /** Лиды прогона по состоянию очистки: новый, готов, отклонён. */
  leads: Record<string, number>;
  /** Отклонённые очисткой: код причины → сколько. */
  lead_reasons: Record<string, number>;
  unanswered: number;
  report: Record<string, unknown>;
  review: SegmentCandidate[];
}

export interface SegmentQueued {
  run_id: number;
  job_id: string;
}

export function readSegment(hypothesis: number): Promise<SegmentHome> {
  return request<SegmentHome>(`/sales/segment?hypothesis=${hypothesis}`);
}

/** Описание сегмента гипотезы. Пустое — описания нет, и судье не с чем сравнивать. */
export function writeSegment(
  hypothesis: number,
  description: string,
): Promise<{ hypothesis_id: number; description: string | null }> {
  return request(`/sales/hypotheses/${hypothesis}`, { method: 'PATCH', body: { description } });
}

export function estimateRun(ask: SegmentAsk): Promise<SegmentEstimate> {
  return request<SegmentEstimate>('/sales/segment/estimate', { method: 'POST', body: ask });
}

export function startRun(ask: SegmentAsk): Promise<SegmentQueued> {
  return request<SegmentQueued>('/sales/segment/runs', { method: 'POST', body: ask });
}

export function resumeRun(run: number): Promise<SegmentQueued> {
  return request<SegmentQueued>(`/sales/segment/runs/${run}/resume`, { method: 'POST' });
}

export function readRun(run: number): Promise<SegmentRunDetail> {
  return request<SegmentRunDetail>(`/sales/segment/runs/${run}`);
}

export function decideSite(candidate: number, accept: boolean): Promise<SegmentCandidate> {
  return request<SegmentCandidate>(`/sales/segment/candidates/${candidate}/decision`, {
    method: 'POST',
    body: { accept },
  });
}
