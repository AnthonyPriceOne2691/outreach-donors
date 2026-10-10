import type {
  AlarmCard,
  ConsequencesView,
  SpendingView,
  ThresholdsBody,
  ThresholdsVersion,
  ThresholdsView,
  WatchdogView,
} from './types';
import { request } from './client';
import type { Stage } from './stages';

/**
 * Расход учётки. Без права «Продажи» статьи, плитки и итоги — без расхода продаж и сходятся
 * между собой (решение Anthony 10.10.2026, П2б); экран говорит это словами. Расширением,
 * а не в `types.ts`: тот у потолка длины.
 */
export interface UsageView extends SpendingView {
  sales_hidden: boolean;
}

/** Тревога сторожа с этапом, о почте которого она: `null` — общая тревога (П2б). */
export interface StagedAlarm extends AlarmCard {
  stage: Stage | null;
}

export interface StagedWatchdog extends WatchdogView {
  alarms: StagedAlarm[];
}

export function fetchThresholds(): Promise<ThresholdsView> {
  return request<ThresholdsView>('/settings/thresholds');
}

export function previewThresholds(body: ThresholdsBody): Promise<ConsequencesView> {
  return request<ConsequencesView>('/settings/preview', { method: 'POST', body });
}

export function saveThresholds(body: ThresholdsBody): Promise<ThresholdsVersion> {
  return request<ThresholdsVersion>('/settings/thresholds', { method: 'POST', body });
}

export function fetchUsage(): Promise<UsageView> {
  return request<UsageView>('/usage');
}

/** Сторож тишины: поломки, которые выглядят как «ничего не происходит». */
export function fetchWatchdog(): Promise<StagedWatchdog> {
  return request<StagedWatchdog>('/watchdog');
}
