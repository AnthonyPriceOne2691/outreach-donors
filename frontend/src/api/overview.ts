import { request } from './client';
import type { OverviewView } from './types';

/** Главная: ключевые числа и работа, которая ждёт человека. */
export function fetchOverview(): Promise<OverviewView> {
  return request<OverviewView>('/overview');
}
