/**
 * Правило региона и срок метрик версии — черновик и проверка, как у порогов
 * (`thresholdDraft.ts`): поле держит набранное, границы приходят с сервера
 * (`rule_limits`), отказ — словами под полем, до нажатия.
 *
 * **Топ × доля — не меньше 100%, и это тоже до нажатия.** Страна с долей ниже
 * 100% / топ может стоять за краем ответа Ahrefs, и фильтр молча отсеял бы годного
 * донора. Сервер откажет тем же правилом (`geo.assert_settings_allow_limited_fetch`);
 * экран говорит это раньше, чем человек нажмёт «Сохранить».
 */

import type { RunRules } from '../api/settings';
import type { ThresholdRange } from '../api/types';
import { numberText, validNumber } from '../components/numberText';

export type RuleKey = keyof RunRules;

/** Поле как набрано. */
export type RulesDraft = Record<RuleKey, string>;

export type RuleLimits = Record<RuleKey, ThresholdRange>;

export const RULE_KEYS: RuleKey[] = ['geo_top_n', 'geo_min_share_percent', 'metrics_ttl_days'];

/** Правило в полях — числами по-русски: от него правят. */
export function rulesDraftOf(rules: RunRules): RulesDraft {
  return {
    geo_top_n: numberText(rules.geo_top_n),
    geo_min_share_percent: numberText(rules.geo_min_share_percent),
    metrics_ttl_days: numberText(rules.metrics_ttl_days),
  };
}

/** Правило, готовое уйти на сервер, или `null`, если хоть одно поле не годится. */
export function rulesOf(draft: RulesDraft, limits: RuleLimits): RunRules | null {
  const rules: Partial<RunRules> = {};
  for (const key of RULE_KEYS) {
    const value = validNumber(draft[key], { decimals: 0, ...limits[key] });
    if (value === null) return null;
    rules[key] = value;
  }
  return rules as RunRules;
}

/** Почему правило несогласованно — словами сервера; `null` — согласованно. */
export function coverageRefusal(rules: RunRules): string | null {
  const { geo_top_n: top, geo_min_share_percent: share } = rules;
  if (top * share >= 100) return null;
  return (
    `При топ-${top} и пороге доли ${share}% страна с достаточной долей может не попасть ` +
    `в ответ. Нужна доля не ниже ${Math.ceil(100 / top)}%.`
  );
}

export function sameRules(left: RunRules, right: RunRules): boolean {
  return RULE_KEYS.every((key) => left[key] === right[key]);
}
