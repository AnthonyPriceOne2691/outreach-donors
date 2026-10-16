import type {
  AlarmCard,
  ArticleCard,
  SpendingView,
  ThresholdRange,
  ThresholdsBody,
  ThresholdsVersion,
  ThresholdsView,
  WatchdogView,
} from './types';
import { request } from './client';
import type { Stage } from './stages';

/** Статья расхода с этапом: одна операция у двух этапов — две статьи (DR кандидатов
 *  Этапа 2 и метрики прогона — обе «метрики доменов»). `null` — трата общая на все
 *  этапы или этап не назван. Провайдер — строкой: сервер заводит новых (прокси,
 *  рендеринг), и экран о них знает из `settings/usageLabels.ts`. */
export interface StagedArticle extends Omit<ArticleCard, 'provider'> {
  provider: string;
  stage: Stage | null;
}

/** Расход этапа: единицы по провайдерам — каждый в своей валюте — и деньги. */
export interface StageSpending {
  stage: Stage | null;
  units_by_provider: Record<string, number>;
  amount_usd: string;
  calls: number;
}

/** Платный обход Этапа 2 — прокси и рендеринг — против своего потолка; `cap_usd`
 *  `null` — своего потолка нет. Потрачено — тем же числом, что сравнивает сам обход. */
export interface CrawlSpend {
  cap_usd: string | null;
  spent_usd: string;
}

/** Смена месячного капа Ahrefs: что поставили, кто и когда. `set_by` `null` — учётку
 *  удалили, а строка смены осталась. */
export interface CapChange {
  id: number;
  units_cap: number;
  set_by: string | null;
  set_at: string;
}

/**
 * Расход учётки. Без права «Продажи» статьи, плитки и итоги — без расхода продаж и сходятся
 * между собой (решение Anthony 10.10.2026, П2б); экран говорит это словами. Расширением,
 * а не в `types.ts`: тот у потолка длины.
 *
 * Кап Ahrefs (`ahrefs_cap`) — заданный на этом экране, а без смен — умолчание сервера
 * (решение владельца 16.10.2026); остаток по нему и границы поля смены — от сервера.
 */
export interface UsageView extends Omit<SpendingView, 'articles'> {
  articles: StagedArticle[];
  sales_hidden: boolean;
  stages: StageSpending[];
  crawl: CrawlSpend;
  /** Осталось по капу — тем же правилом, что у потолка прогона: экран называет, а не считает. */
  ahrefs_cap_left: number;
  /** Допустимый кап — от единицы до лимита тарифа: поле смены проверяется ими до нажатия. */
  ahrefs_cap_limits: ThresholdRange;
  /** Последние смены капа, новые первыми; пусто — кап по умолчанию, на экране не меняли. */
  ahrefs_cap_changes: CapChange[];
}

/** Тревога сторожа с этапом, о почте которого она: `null` — общая тревога (П2б). */
export interface StagedAlarm extends AlarmCard {
  stage: Stage | null;
}

export interface StagedWatchdog extends WatchdogView {
  alarms: StagedAlarm[];
}

/** Эти пороги против действующих — по метрикам доменов базы, одним правилом с обеих
 *  сторон. Вердикты в базе сохранение не переписывает (проверка прода 10.10.2026).
 *
 *  Тип лежит здесь, а не в `types.ts`: тот уже за пределом длины файла. */
export interface ConsequencesView {
  /** Домены с метриками: их и сравнивают. */
  checked: number;
  /** Пропускают действующие пороги. */
  passing_now: number;
  /** Пропустят эти. */
  passing_after: number;
  /** Действующие пропускают, эти — нет. */
  cut: number;
  /** Из них — с полученной ценой. */
  cut_with_price: number;
  /** Эти пропускают, действующие — нет. */
  admitted: number;
  /** Действующие отсекают, эти пустили бы дальше, но метрик для решения нет. */
  undecided: number;
  /** Домены без метрик: пороги их не судят. */
  without_metrics: number;
  /** С вердиктом «подходит» в базе — то же число, что «Прошли пороги» на «Обзоре». */
  suitable: number;
}

/** Правило региона и срок метрик версии — правятся на «Порогах» вместе с порогами
 *  (сверка ТЗ 11.10.2026: «все пороги — в настройках прогона»). Доля — процентами,
 *  как в поле. Типы здесь, а не в `types.ts`: тот за пределом длины файла. */
export interface RunRules {
  /** Страна подходит, если она в этом топе стран по органическому трафику… */
  geo_top_n: number;
  /** …или даёт не меньше этой доли трафика, процентами. */
  geo_min_share_percent: number;
  /** Сколько дней метрики Ahrefs годны: до срока прогон их не покупает заново. */
  metrics_ttl_days: number;
}

/** Порог крупного сайта: DR выше — «не подходит», писем нет (порог версии с 12.10.2026). */
export interface DrCeiling {
  max_dr: number;
}

/** Пороги экрана — четыре нижних и порог крупного сайта. */
export type ThresholdsFields = ThresholdsBody & DrCeiling;

/** Версия целиком: пороги, правило региона и срок метрик. */
export type VersionBody = ThresholdsFields & RunRules;

export interface RulesVersion extends ThresholdsVersion, DrCeiling, RunRules {}

/** Пороги и история версий — с правилом региона и сроком; их границы — отдельно. */
export interface RulesView extends Omit<
  ThresholdsView,
  'current' | 'defaults' | 'history' | 'limits'
> {
  current: RulesVersion | null;
  defaults: VersionBody;
  history: RulesVersion[];
  limits: Record<keyof ThresholdsFields, ThresholdRange>;
  rule_limits: Record<keyof RunRules, ThresholdRange>;
}

export function fetchThresholds(): Promise<RulesView> {
  return request<RulesView>('/settings/thresholds');
}

export function previewThresholds(body: ThresholdsBody): Promise<ConsequencesView> {
  return request<ConsequencesView>('/settings/preview', { method: 'POST', body });
}

export function saveThresholds(body: VersionBody): Promise<RulesVersion> {
  return request<RulesVersion>('/settings/thresholds', { method: 'POST', body });
}

export function fetchUsage(): Promise<UsageView> {
  return request<UsageView>('/usage');
}

/** Новый месячный кап Ahrefs — с этой минуты; право — «править пороги», как у порогов. */
export function saveAhrefsCap(unitsCap: number): Promise<CapChange> {
  return request<CapChange>('/usage/ahrefs-cap', { method: 'POST', body: { units_cap: unitsCap } });
}

/** Сторож тишины: поломки, которые выглядят как «ничего не происходит». С этапом — полоса
 *  раздела этапа: тревоги этого этапа, без опроса провайдеров (его место — «Обзор»). */
export function fetchWatchdog(stage?: Stage): Promise<StagedWatchdog> {
  return request<StagedWatchdog>(stage === undefined ? '/watchdog' : `/watchdog?stage=${stage}`);
}

/** Вес или порог Этапа 2: что значит, сколько и какой настройкой окружения меняется. */
export interface SettingNumber {
  label: string;
  value: number;
  setting: string;
}

/** Список «кому не пишем» Этапа 2 и настройка, которой он меняется. */
export interface SettingList {
  label: string;
  values: string[];
  setting: string;
}

/** Чем Этап 2 судит ссылки сейчас — только чтение (сверка ТЗ 11.10.2026): значения —
 *  настройки окружения, а не версии, как пороги отбора доноров. */
export interface AdvertiserSettingsView {
  points: SettingNumber[];
  thresholds: SettingNumber[];
  lists: SettingList[];
}

export function fetchAdvertiserSettings(): Promise<AdvertiserSettingsView> {
  return request<AdvertiserSettingsView>('/settings/advertisers');
}
