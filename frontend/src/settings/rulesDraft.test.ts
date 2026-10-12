/**
 * Правило региона версии: черновик и согласованность «топ × доля ≥ 100%» —
 * тем же правилом, каким откажет сервер (`geo.assert_settings_allow_limited_fetch`).
 */

import { describe, expect, it } from 'vitest';

import { coverageRefusal, rulesDraftOf, rulesOf } from './rulesDraft';

const LIMITS = {
  geo_top_n: { min: 1, max: 5 },
  geo_min_share_percent: { min: 1, max: 100 },
  metrics_ttl_days: { min: 1, max: 365 },
};

describe('правило региона', () => {
  it.each([
    [5, 20],
    [4, 25],
    [3, 34],
    [1, 100],
  ])('топ-%i и %i%% — согласованно', (top, share) => {
    expect(
      coverageRefusal({ geo_top_n: top, geo_min_share_percent: share, metrics_ttl_days: 90 }),
    ).toBeNull();
  });

  it('доля меньше 100% / топ — отказ словами и нужная доля', () => {
    expect(coverageRefusal({ geo_top_n: 3, geo_min_share_percent: 33, metrics_ttl_days: 90 })).toBe(
      'При топ-3 и пороге доли 33% страна с достаточной долей может не попасть в ответ. ' +
        'Нужна доля не ниже 34%.',
    );
  });

  it('поле за границей или не число — правила нет, сохранять нечего', () => {
    const draft = rulesDraftOf({ geo_top_n: 5, geo_min_share_percent: 20, metrics_ttl_days: 90 });

    expect(rulesOf(draft, LIMITS)).toEqual({
      geo_top_n: 5,
      geo_min_share_percent: 20,
      metrics_ttl_days: 90,
    });
    expect(rulesOf({ ...draft, geo_top_n: '6' }, LIMITS)).toBeNull();
    expect(rulesOf({ ...draft, metrics_ttl_days: '9.5' }, LIMITS)).toBeNull();
    expect(rulesOf({ ...draft, geo_min_share_percent: '' }, LIMITS)).toBeNull();
  });
});
