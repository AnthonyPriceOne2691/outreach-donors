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
  /** «Диалоги» — столько же, сколько «Ждут человека» на их экране. */
  threads: number;
  forms: number;
  /** «Рекламодатели» — спорных на ручной проверке. */
  advertisers: number;
}

/** Только числа меню, без сводки: их спрашивает каждый экран. */
export function fetchWork(): Promise<WorkView> {
  return request<WorkView>('/overview/work');
}
