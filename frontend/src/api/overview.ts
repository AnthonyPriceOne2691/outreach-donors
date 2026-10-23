import { request } from './client';
import type { OverviewView } from './types';

/** Главная: ключевые числа и работа, которая ждёт человека. */
export function fetchOverview(): Promise<OverviewView> {
  return request<OverviewView>('/overview');
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
