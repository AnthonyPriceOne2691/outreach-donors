/**
 * Шапка отчёта прогона (`runFacts`) называет купленную глубину выдачи, а не всю (стенд 19.11):
 * волна плана работ «стр. 4–5» покупает 20 результатов на ключ, а шапка писала «50 результатов на
 * ключ». С первой страницы — как было; сервер старше `from_page` — тоже с первой.
 */

import { describe, expect, it } from 'vitest';

import type { SegmentRunRow } from './segmentApi';
import { runDepthTitle, runFacts } from './segmentData';

/** Прогон волны плана: кончился, судья в наблюдении; часы рендера шапке не нужны. */
const WAVE: SegmentRunRow = {
  id: 41,
  hypothesis_id: 7,
  created_at: '2026-11-19T09:30:00+00:00',
  finished_at: '2026-11-19T09:52:00+00:00',
  created_by: null,
  country: 'de',
  language: 'en',
  depth_pages: 5,
  from_page: 4,
  keywords: 20,
  mode: 'shadow',
  stage: 'done',
  error: null,
  job_id: 'sales-segment-41',
  active: false,
};

/** Начало шапки — язык писем и выдача; кто и когда — дальше, по часовому поясу машины. */
function head(run: SegmentRunRow): string {
  return runFacts(run).split(' · ').slice(0, 2).join(' · ');
}

describe('шапка прогона — купленная глубина выдачи', () => {
  it('волны плана — купленные страницы и места результатов на ключ', () => {
    expect(head(WAVE)).toBe(
      'Язык писем — английский · выдача — стр.\u00a04–5, результаты 31–50 на ключ',
    );
    expect(head({ ...WAVE, from_page: 3, depth_pages: 3 })).toBe(
      'Язык писем — английский · выдача — стр.\u00a03, результаты 21–30 на ключ',
    );
    expect(head({ ...WAVE, from_page: 2, depth_pages: 2 })).toBe(
      'Язык писем — английский · выдача — стр.\u00a02, результаты 11–20 на ключ',
    );
  });

  it('с первой страницы — как было: сколько результатов на ключ', () => {
    expect(head({ ...WAVE, from_page: 1, depth_pages: 1 })).toBe(
      'Язык писем — английский · выдача — 10 результатов на ключ',
    );
    expect(runDepthTitle({ from_page: 1, depth_pages: 5 })).toBe('50 результатов на ключ');
  });

  it('сервер старше `from_page` — глубина с первой страницы', () => {
    const old: SegmentRunRow = { ...WAVE, depth_pages: 2 };
    delete old.from_page;
    expect(runDepthTitle(old)).toBe('20 результатов на ключ');
  });

  it('остальная шапка — прежняя: кто запустил и когда кончился', () => {
    expect(runFacts(WAVE)).toMatch(
      /^Язык писем — английский · выдача — стр\.\u00a04–5, результаты 31–50 на ключ · запустил 19\.11\.2026, \d\d:30 · кончился 19\.11\.2026, \d\d:52\.$/,
    );
  });
});
