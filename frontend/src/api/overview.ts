import { request } from './client';
import type { CapBinding } from './settings';
import type { OverviewView } from './types';

/**
 * Во что расход юнитов Ahrefs упрётся раньше и сколько до этого осталось из скольких — числами
 * того же счётчика (сервер — `ops/overview.Wall`, проверка прода 10.11.2026): наш расход на
 * «Обзоре» читался остатком «кап минус наше», а на «Расходе» осталось меньше — ключ общий с
 * соседней системой, и её траты видны только в счётчике ключа. `null` — кап неизвестен; полей
 * нет — сервер старше экрана. Здесь, а не в `types.ts`: тот у потолка длины.
 */
export interface OverviewWall {
  ahrefs_binding?: CapBinding | null;
  ahrefs_left?: number | null;
  ahrefs_left_of?: number | null;
}

/** Сводка главной вместе с упором юнитов. */
export type Overview = OverviewView & OverviewWall;

/** Главная: ключевые числа и работа, которая ждёт человека. */
export function fetchOverview(): Promise<Overview> {
  return request<Overview>('/overview');
}

/** Числа у пунктов меню: сколько в разделе ждёт человека — правилами «Ждут человека». */
export interface WorkView {
  /** «Прогон» — доменов ждут решения в очередях прогонов. */
  run: number;
  /** «Диалоги с донорами» — ждут человека переписки доноров: столько же, сколько «Ждут
   *  человека» на их экране (23.10.2026: у каждого из двух пунктов диалогов — своё число). */
  donor_threads: number;
  /** «Диалоги с рекламодателями» — ждут человека переписки рекламодателей (лиды). */
  advertiser_threads: number;
  forms: number;
  /** «Рекламодатели» — ждут «пишем / не пишем»: спорные и бизнесы ниши. */
  advertisers: number;
  /** «Диалоги продаж» — переписки продаж (группа меню «Продажи»). Без права — ноль. */
  sales_threads: number;
}

/** Только числа меню, без сводки: их спрашивает каждый экран. */
export function fetchWork(): Promise<WorkView> {
  return request<WorkView>('/overview/work');
}
