/**
 * Отчёт прогона (`GET /runs/{id}/report`, сервер — `runs/outcome.py`): сколько доменов
 * проверено, сколько прошло фильтр, сколько отсеяно и по какому порогу, сколько
 * потрачено юнитов и денег — требование ТЗ к отчёту прогона.
 *
 * Типы — здесь, а не в `types.ts`: тот у потолка длины файла.
 */

import { request } from './client';
import type { ArticleCard, RunCard } from './types';

/** Порог, по которому домен отсеян. `other` — причина, которой нет среди порогов:
 *  её экран показывает как записана (`said`). */
export type Threshold = 'dr' | 'org_traffic' | 'keywords' | 'refdomains' | 'geo' | 'other';

/** Пороги, с которыми прогон запущен, — его версия, а не сегодняшняя с экрана «Пороги». */
export interface RunThresholds {
  min_dr: number;
  min_org_traffic: number;
  min_refdomains: number;
  min_keywords: number;
  /** Регион: страна прогона в топ-N стран по трафику или доля не меньше этой. */
  geo_top_n: number;
  geo_min_share: number;
}

/** Домены прогона по шагам: из выдачи — в проверку Ahrefs — в вердикт. */
export interface RunDomains {
  /** Уникальных доменов выдачи. `null` — отчёт записан до этого числа. */
  found: number | null;
  /** Отсёк гейт исключений: стоп-лист, свои, уже отклонённые. */
  excluded: number;
  /** Метрики в сроке — проверены раньше, платить не пришлось. */
  fresh: number;
  /** Новые — те, за которые прогон собирался платить Ahrefs. */
  to_check: number;
  /** Прошли проверку Ahrefs с любым вердиктом. */
  checked: number;
  suitable: number;
  unsuitable: number;
  unchecked: number;
  /** Новых, до Ahrefs не дошедших: отрезал судья или прогон остановился раньше. */
  not_reached: number;
  /** Судья режет: отрезанные им до Ahrefs не доходят. */
  judge_enforced: boolean;
  /** Сколько раз прогон продолжали после сбоя: числа — последней попытки. */
  resumes: number;
}

export interface RejectionCard {
  threshold: Threshold;
  count: number;
  said: string | null;
}

export interface RunReportView {
  run: RunCard;
  thresholds: RunThresholds;
  /** `null` — итога по доменам ещё нет: отчёт пишется в конце попытки. */
  domains: RunDomains | null;
  rejected: RejectionCard[];
  /** Расход по журналу — всё, что записано за этим прогоном. */
  spending: ArticleCard[];
  /** Запросы к Ahrefs, которые обслужил кэш провайдера, — бесплатно. */
  free_requests: number;
}

export function fetchRunReport(id: number): Promise<RunReportView> {
  return request<RunReportView>(`/runs/${id}/report`);
}
