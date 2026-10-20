/**
 * Прогон по ссылкам конкурентов (`backend/api/runs/competitors.py`): смета и запуск.
 *
 * Своим файлом, а не строками `types.ts` и `runs.ts`: первый у потолка длины, а этот вход
 * прогона — отдельная тема экрана. Бюджет у сметы тот же, что у ключей (`BudgetFigures`).
 */

import { request } from './client';
import type { BudgetFigures } from './forecastLimits';
import type { RunCard, RunQueued } from './types';

export interface CompetitorRunBody {
  /** Домены конкурентов — сервер сводит их к корневым и без повторов. */
  competitors: string[];
  country: string;
  /** Сколько ссылающихся доменов взять у каждого конкурента, сильнейшие первыми. */
  depth: number;
  /** Потолок юнитов на этот прогон. Пусто — весь остаток по капу. */
  cap?: number;
}

/** Список одного конкурента в смете: до стольких строк и до стольких юнитов. */
export interface CompetitorList {
  target: string;
  rows: number;
  units: number;
}

export interface CompetitorForecast extends BudgetFigures {
  lists: CompetitorList[];
  depth: number;
  /** Цена строки списка при порогах прогона — юнитов Ahrefs. */
  row_units: number;
  units_lists: number;
  /** Сайтов на проверку в худшем случае: каждый список до глубины, все новые. */
  expected_domains: number;
  units_screen: number;
  units_metrics: number;
  units_by_country: number;
  /** Всё в юнитах Ahrefs: списки и три ступени воронки. */
  units_total: number;
  /** Токены модели на суд сайтов — другой счёт; судья выключен — ноль. */
  tokens: number;
  judge_mode: string;
  /** Тематика сайтов той же пачкой судьи — худший случай и на сайт; ноль — тем не будет. */
  topic_tokens: number;
  topic_tokens_per_site: number;
  topic_measured: boolean;
  /** Суд не помещается в потолки модели — словами сервера, и прогон не запускается. */
  llm_refusal: string | null;
}

export function estimateCompetitors(body: CompetitorRunBody): Promise<CompetitorForecast> {
  return request<CompetitorForecast>('/runs/competitors/estimate', { method: 'POST', body });
}

export function startCompetitors(body: CompetitorRunBody): Promise<RunQueued> {
  return request<RunQueued>('/runs/competitors', { method: 'POST', body });
}

/** Конкуренты строки истории (`RunCard.competitors` сервера); у прогона по ключам — пусто.
 *  Читается здесь, а не полем `RunCard` в `types.ts`: тот у потолка длины. */
export function competitorsOf(run: RunCard): string[] {
  const value: unknown = (run as RunCard & { competitors?: unknown }).competitors;
  if (!Array.isArray(value)) return [];
  return (value as unknown[]).filter((item): item is string => typeof item === 'string');
}
