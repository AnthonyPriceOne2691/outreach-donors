/**
 * План работ на вкладке «Гипотезы»: планы с этапом, волной и проблемами гипотез плана и кнопки
 * плана (`api/sales/plans.py`, `sales/plan_actions.py`).
 *
 * Список — тот же `GET /sales/plans`, что у окна «Планы работ» (`PlansList`), под тем же ключом
 * кэша: окно и вкладка видят одни числа. Поля, которых нет в общем типе (`api/salesPlans.ts`), —
 * здесь, рядом с колонкой. **Опрос — раз в 20 с, пока хоть один план идёт**: числа прохода
 * конвейера меняются раз в пару минут, а стоящий или окончённый план не меняется сам.
 *
 * Кнопка — запрос; ответ — план или гипотеза плана, а список перечитывается целиком: «Продолжить»
 * гипотезы может снова пустить окончённый план. Отказ — словами сервера, у кнопки.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { request, upload } from '../api/client';
import type { PlanCard, PlanHypothesis } from '../api/salesPlans';
import { PLANS_QUERY_KEY } from './PlansList';

const PLANS = '/sales/plans';
const ROWS = '/sales/plan-hypotheses';
/** Опрос идущего плана: проход конвейера — раз в две минуты, числа меняются не чаще. */
export const PLAN_POLL_MS = 20_000;

/** Проблема гипотезы плана: вид, слова и когда. */
export interface PlanProblem {
  /** `budget`, `keys`, `run_failed`, `chain`, `stopped`. */
  code: string;
  words: string;
  at: string | null;
}

/** Гипотеза плана с тем, что видно в колонке «План работ». */
export interface PlanRow extends PlanHypothesis {
  /** Страница волны сбора: 1, 2, 3; 4 — страницы 4–5. */
  wave_page: number;
  /** Ключей, купленных до страницы волны. */
  wave_bought: number;
  reserved_usd: number;
  problems: PlanProblem[];
}

export interface PlanLive extends Omit<PlanCard, 'hypotheses'> {
  hypotheses: PlanRow[];
}

export interface PlansLive {
  plans: PlanLive[];
}

/** Вид сайта гипотезы — коды видов судьи (`hypothesis_kinds.SITE_KINDS` на сервере). */
export type SiteKind = 'own' | 'intermediary' | 'media' | 'other';

/** Изменить качество сайтов — только поправленное. */
export interface PlanQualityBody {
  max_pages?: number;
  target?: number;
  other_countries?: 'review' | 'accept';
  excluded_countries?: string[];
  /** Весь выбор; пустой — снять выбор (виды решает описание сегмента). */
  site_kinds?: SiteKind[];
}

export interface PlanKeysAdded {
  hypothesis: PlanRow;
  added: number;
  known: number;
  rejected: number;
}

export function listLivePlans(): Promise<PlansLive> {
  return request<PlansLive>(PLANS);
}

/** Идёт ли хоть один план — тогда список опрашивается. */
export function anyRunning(view: PlansLive | undefined): boolean {
  return view?.plans.some((plan) => plan.status === 'running') ?? false;
}

export function useLivePlans() {
  return useQuery({
    queryKey: PLANS_QUERY_KEY,
    queryFn: listLivePlans,
    refetchInterval: (query) => (anyRunning(query.state.data) ? PLAN_POLL_MS : false),
  });
}

/** Гипотеза плана по гипотезе продаж, с её планом. */
export interface PlanPlace {
  plan: PlanLive;
  row: PlanRow;
}

export function placesOf(view: PlansLive | undefined): Map<number, PlanPlace> {
  const found = new Map<number, PlanPlace>();
  for (const plan of view?.plans ?? []) {
    for (const row of plan.hypotheses) found.set(row.hypothesis_id, { plan, row });
  }
  return found;
}

export function stopPlan(id: number): Promise<PlanLive> {
  return request<PlanLive>(`${PLANS}/${id}/stop`, { method: 'POST' });
}

export function resumePlan(id: number): Promise<PlanLive> {
  return request<PlanLive>(`${PLANS}/${id}/resume`, { method: 'POST' });
}

export function stopRow(id: number): Promise<PlanRow> {
  return request<PlanRow>(`${ROWS}/${id}/stop`, { method: 'POST' });
}

export function resumeRow(id: number): Promise<PlanRow> {
  return request<PlanRow>(`${ROWS}/${id}/resume`, { method: 'POST' });
}

export function changeQuality(id: number, body: PlanQualityBody): Promise<PlanRow> {
  return request<PlanRow>(`${ROWS}/${id}/quality`, { method: 'PATCH', body });
}

export function collectMore(id: number, n: number): Promise<PlanRow> {
  return request<PlanRow>(`${ROWS}/${id}/more`, { method: 'POST', body: { n } });
}

/** Ключи — текстом или книгой .xlsx одним листом, одно из двух. */
export function addKeys(
  id: number,
  keys: { text: string } | { file: File },
): Promise<PlanKeysAdded> {
  const form = new FormData();
  if ('file' in keys) form.append('file', keys.file);
  else form.append('text', keys.text);
  return upload<PlanKeysAdded>(`${ROWS}/${id}/keywords`, form);
}

/** Кнопка плана: запрос, перечитанные планы — и раздел, если правка трогает гипотезу (страны). */
export function usePlanAction<A, R>(send: (args: A) => Promise<R>, onDone?: (answer: R) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: send,
    onSuccess: (answer) => {
      void client.invalidateQueries({ queryKey: PLANS_QUERY_KEY });
      onDone?.(answer);
    },
  });
}
