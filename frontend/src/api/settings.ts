import type {
  ConsequencesView,
  SpendingView,
  ThresholdsBody,
  ThresholdsVersion,
  ThresholdsView,
} from './types';
import { request } from './client';

export function fetchThresholds(): Promise<ThresholdsView> {
  return request<ThresholdsView>('/settings/thresholds');
}

export function previewThresholds(body: ThresholdsBody): Promise<ConsequencesView> {
  return request<ConsequencesView>('/settings/preview', { method: 'POST', body });
}

export function saveThresholds(body: ThresholdsBody): Promise<ThresholdsVersion> {
  return request<ThresholdsVersion>('/settings/thresholds', { method: 'POST', body });
}

export function fetchUsage(): Promise<SpendingView> {
  return request<SpendingView>('/usage');
}
