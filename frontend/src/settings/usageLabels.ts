/**
 * Подписи экрана «Расход», которых нет в общих подписях: провайдеры платного обхода
 * Этапа 2, его статьи и этапы.
 *
 * Своим файлом, а не в `api/labels.ts`: тот у потолка длины. Общие подписи берутся
 * оттуда же — у одного провайдера одно название на всех экранах; здесь только то,
 * что сервер начал писать вместе с платными уровнями обхода (прокси, сервис
 * рендеринга) и этапом у строки расхода.
 */

import { operationTitle, USAGE_PROVIDERS, usageProvider } from '../api/labels';
import type { Stage } from '../api/stages';
import { formatBytes, formatNumber, plural } from '../format';

interface ProviderLabel {
  title: string;
  color: string;
}

/** Платные уровни обхода: прокси берёт за трафик, сервис рендеринга — за вызов. */
const PROXY: ProviderLabel = { title: 'прокси обхода', color: 'lagoon' };
const RENDER: ProviderLabel = { title: 'рендеринг страниц', color: 'lagoon' };
const CRAWL_PROVIDERS: Partial<Record<string, ProviderLabel>> = { proxy: PROXY, render: RENDER };

/** Все провайдеры расхода — для плиток. Платные уровни обхода — после общих. */
export const SPEND_PROVIDERS: Record<string, ProviderLabel> = {
  ...USAGE_PROVIDERS,
  proxy: PROXY,
  render: RENDER,
};

/** Провайдер словами; незнакомый — с кодом в скобках, как в общих подписях. */
export function spendProvider(provider: string): ProviderLabel {
  return CRAWL_PROVIDERS[provider] ?? usageProvider(provider);
}

/** Провайдер, которого показываем, только когда по нему тратили: прокси и рендеринг
 *  не куплены, и две плитки «трат не было» о том, чего нет, были бы шумом. */
export function shownWithoutSpend(provider: string): boolean {
  return !(provider in CRAWL_PROVIDERS);
}

const CRAWL_OPERATIONS: Partial<Record<string, string>> = {
  crawl_proxy: 'трафик прокси обхода',
  crawl_render: 'рендеринг пустых страниц',
};

/** Статья словами — `crawl_*` здесь, остальное — общими подписями. */
export function spendOperation(operation: string): string {
  return CRAWL_OPERATIONS[operation] ?? operationTitle(operation);
}

const STAGES: Record<Stage, string> = {
  donors: 'Этап 1 · доноры',
  advertisers: 'Этап 2 · рекламодатели',
  sales: 'Продажи',
};

const STAGES_SHORT: Record<Stage, string> = {
  donors: 'Этап 1',
  advertisers: 'Этап 2',
  sales: 'Продажи',
};

/** Этап словами. `null` — трата общая на все этапы (отправка письма) или этап
 *  не назван: строка записана до 12.10.2026, или её операцию делят этапы. */
export function stageTitle(stage: Stage | null): string {
  return stage === null ? 'Без этапа' : STAGES[stage];
}

/** То же коротко — для колонки таблицы статей. */
export function stageShort(stage: Stage | null): string {
  return stage === null ? 'без этапа' : STAGES_SHORT[stage];
}

/** Единица счёта у каждого провайдера — своя: Ahrefs берёт юнитами, модель — токенами,
 *  отправка считает письма, сервис рендеринга — запросы. «Юн.» у всех подряд называло
 *  токены модели юнитами Ahrefs. */
const UNIT_TITLES: Partial<Record<string, string>> = {
  ahrefs: 'юн.',
  llm: 'ток.',
  hunter: 'запр.',
  render: 'запр.',
};

/** «1 письмо, 4 письма, 5 писем» — у сокращений склонять нечего. */
export function unitOf(provider: string, count: number): string {
  if (provider !== 'email') return UNIT_TITLES[provider] ?? 'юн.';
  return plural(count, 'письмо', 'письма', 'писем');
}

/** Неразрывный пробел — кодом, а не самим знаком: в исходнике знак не отличить от пробела. */
const NBSP = '\u00a0';

/** Единицы с единицей счёта: «6 416 юн.», «4 письма». Прокси считает байты — их видно
 *  размером («12,4 МБ»), а не числом в десять знаков. Между числом и единицей —
 *  неразрывный пробел: на телефоне «4» и «письма» расходились по строкам. */
export function unitsText(provider: string, units: number): string {
  if (provider === 'proxy') return formatBytes(units).replace(' ', NBSP);
  return `${formatNumber(units)}${NBSP}${unitOf(provider, units)}`;
}
