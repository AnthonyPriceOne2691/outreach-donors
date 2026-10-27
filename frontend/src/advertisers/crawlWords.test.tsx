/**
 * Почему обход кончился и сколько это от потолка — словами под значком (23.10.2026): каждое
 * слово причины, потолки — числами сервера, и запись без новых полей — значок как был, слов с
 * числами, которых нет, — нет.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { CrawlRow, CrawlTargets } from '../api/crawls';
import { renderWith } from '../test/render';
import { CrawlState, crawlState } from './CrawlTable';
import { crawlRule, spanWords, stopWords } from './crawlWords';

/** Текст без неразрывных пробелов разрядов: «1 000». */
function plain(text: string | null): string | null {
  return text === null ? null : text.replace(/[\u00a0\u202f]/g, ' ');
}

/** Обход, записанный до 23.10.2026: ни потолков, кроме страниц, ни отчёта о том, откуда
 *  брались адреса. */
function old(overrides: Partial<CrawlRow> = {}): CrawlRow {
  return {
    id: 5,
    host: 'donor.example.test',
    status: 'done',
    outcome: 'ok',
    stop_reason: 'exhausted',
    pages: 380,
    max_pages: 1000,
    articles: 400,
    links: 120,
    resumes: 0,
    requested_by: 'op@team.example.test',
    created_at: '2026-10-22T08:00:00Z',
    finished_at: '2026-10-22T09:00:00Z',
    reason: null,
    ...overrides,
  };
}

/** Законченный обход, как его отдаёт сервер теперь: с потолками и отчётом. */
function done(overrides: Partial<CrawlRow> = {}): CrawlRow {
  return old({
    max_attempts: 2000,
    max_seconds: 3600,
    delay_sec: 1,
    max_delay_sec: 10,
    source: 'sitemap',
    sitemap_complete: true,
    ...overrides,
  });
}

const TIMED_OUT = { outcome: 'partial', stop_reason: 'timeout', pages: 412 } as const;

describe('значок — страницы против потолка', () => {
  it.each([
    [done({ pages: 380 }), 'обойдён: 380 из 1 000 страниц'],
    [done(TIMED_OUT), 'частично: 412 из 1 000 страниц'],
    [done({ pages: 1, max_pages: 1001 }), 'обойдён: 1 из 1 001 страницы'],
    // Потолок — числом сервера, а не тысячей из экрана.
    [done({ pages: 200, stop_reason: 'max_pages', max_pages: 200 }), 'обойдён: 200 из 200 страниц'],
  ])('%#: %s', (crawl, label) => {
    expect(plain(crawlState(crawl).label)).toBe(label);
  });

  it('потолка нет — как было: «обойдён: 380 страниц»', () => {
    const bare = JSON.parse(JSON.stringify({ ...old(), max_pages: null })) as CrawlRow;

    expect(crawlState(bare).label).toBe('обойдён: 380 страниц');
  });
});

describe('почему обход кончился — словами', () => {
  it.each([
    [{}, 'в карте сайта больше адресов нет'],
    // «Больше нет» по неполной карте — не вся правда, и это не прячется.
    [{ sitemap_complete: false }, 'в карте сайта больше адресов нет, но карта прочитана не вся'],
    [{ source: 'links', sitemap_complete: false }, 'по ссылкам больше страниц не нашли'],
    [
      { source: 'sitemap+links', sitemap_complete: false },
      'в карте сайта и по ссылкам больше адресов нет, но карта прочитана не вся',
    ],
    [{ stop_reason: 'max_pages', pages: 1000 }, 'потолок 1 000 страниц на донора'],
    [{ ...TIMED_OUT }, 'кончился час на донора'],
    [
      { ...TIMED_OUT, crawl_delay: 6 },
      'кончился час на донора; сайт просит паузу 6 с между страницами',
    ],
    [
      { ...TIMED_OUT, crawl_delay: 2.5 },
      'кончился час на донора; сайт просит паузу 2,5 с между страницами',
    ],
    // Пауза короче двух наших — темп почти наш, строка ничего бы не объяснила.
    [{ ...TIMED_OUT, crawl_delay: 1.5 }, 'кончился час на донора'],
    // Просит дольше нашего потолка паузы — и сколько ждём на деле.
    [
      { ...TIMED_OUT, crawl_delay: 60 },
      'кончился час на донора; сайт просит паузу 60 с между страницами — ждём 10 с',
    ],
    // Свой срок обхода сильнее общего потолка.
    [
      { ...TIMED_OUT, crawl_delay: 6, time_budget_sec: 10020 },
      'кончилось время на донора — 2 ч 47 мин; сайт просит паузу 6 с между страницами',
    ],
    [{ stop_reason: 'max_attempts', outcome: 'partial' }, 'кончились попытки — 2 000 запросов'],
    [{ stop_reason: 'unhealthy', outcome: 'partial' }, 'сайт стал отказывать — остановились'],
  ] as [Partial<CrawlRow>, string][])('%o → %s', (overrides, said) => {
    expect(plain(stopWords(done(overrides)))).toBe(said);
  });

  it.each([
    // Исход значка говорит сам: «robots.txt запрещает», «не открылся».
    [{ stop_reason: 'robots', outcome: 'forbidden', pages: 0 }],
    [{ stop_reason: 'no_start', outcome: 'failed', pages: 0 }],
    // Идёт — ещё не кончился; остановленный разбором — причина за «!».
    [{ status: 'running', outcome: null, stop_reason: null }],
    [{ status: 'stopped', outcome: null, stop_reason: null }],
  ] as [Partial<CrawlRow>][])('без слов: %o', (overrides) => {
    expect(stopWords(done(overrides))).toBeNull();
  });

  it('потолки — числами сервера, какими их прислали', () => {
    const tight = { max_attempts: 3000, max_seconds: 5400, delay_sec: 3, max_delay_sec: 6 };

    expect(plain(stopWords(done({ stop_reason: 'max_pages', max_pages: 1500 })))).toBe(
      'потолок 1 500 страниц на донора',
    );
    expect(plain(stopWords(done({ ...tight, stop_reason: 'max_attempts' })))).toBe(
      'кончились попытки — 3 000 запросов',
    );
    // Наша пауза 3 с: сайт, просящий 5 с, темпа вдвое не меняет — о нём молчим.
    expect(stopWords(done({ ...tight, ...TIMED_OUT, crawl_delay: 5 }))).toBe(
      'кончилось время на донора — 1 ч 30 мин',
    );
    expect(stopWords(done({ ...tight, ...TIMED_OUT, crawl_delay: 9 }))).toBe(
      'кончилось время на донора — 1 ч 30 мин; сайт просит паузу 9 с между страницами — ждём 6 с',
    );
  });

  it('запись без новых полей — слова без чисел, которых нет', () => {
    expect(stopWords(old())).toBe('адреса для обхода кончились');
    expect(stopWords(old({ stop_reason: 'max_attempts', outcome: 'partial' }))).toBe(
      'кончились попытки',
    );
    expect(stopWords(old({ ...TIMED_OUT }))).toBe('кончилось время на донора');
  });
});

describe('срок словами', () => {
  it.each([
    [3600, 'час'],
    [7200, '2 ч'],
    [5400, '1 ч 30 мин'],
    [10020, '2 ч 47 мин'],
    [1800, '30 мин'],
    [45, '45 с'],
  ])('%s с → %s', (seconds, said) => {
    expect(spanWords(seconds)).toBe(said);
  });
});

describe('«i» панели: путь обхода и потолки', () => {
  const TARGETS: CrawlTargets = {
    donors: [],
    no_price: 0,
    stale_price: 0,
    supplier: 0,
    notes: [],
    max_pages: 1000,
    workers: 4,
    max_attempts: 2000,
    max_seconds: 3600,
    delay_sec: 1,
    max_delay_sec: 10,
  };

  // Другие числа сервера — в тесте панели (`CrawlPanel.test`): «i» берёт их из ответа.
  it('числами сервера', () => {
    expect(plain(crawlRule(TARGETS))).toBe(
      ' Обход — с главной по карте сайта, свежие адреса первыми; карта кончилась или её нет — дальше по ссылкам сайта.' +
        ' На донора — не больше 1 000 страниц, 2 000 запросов и часа; пауза между страницами —' +
        ' та, что просит сайт в robots.txt, но не меньше 1 с и не больше 10 с. Что кончилось' +
        ' первым — под значком обхода.',
    );
  });

  it('срок медленных — только если сервер его даёт и он длиннее общего', () => {
    expect(plain(crawlRule({ ...TARGETS, max_seconds_slow: 10800 }))).toContain(
      'и часа (сайту с долгой паузой — до 3 ч, чтобы успеть страницы); пауза',
    );
    expect(plain(crawlRule({ ...TARGETS, max_seconds_slow: 3600 }))).toContain('и часа; пауза');
  });

  it('ответа нет или сервер старше экрана — путь обхода без выдуманных чисел', () => {
    const { donors, no_price, stale_price, supplier, notes, max_pages, workers } = TARGETS;
    const older = { donors, no_price, stale_price, supplier, notes, max_pages, workers };

    for (const data of [undefined, older]) {
      expect(crawlRule(data)).toBe(
        ' Обход — с главной по карте сайта, свежие адреса первыми; карта кончилась или её нет — дальше по ссылкам сайта.',
      );
    }
  });
});

describe('причина — под значком, до чисел здоровья', () => {
  it('в каждом месте, где стоит значок: строкой сразу под ним', () => {
    renderWith(
      <CrawlState
        crawl={done({ ...TIMED_OUT, crawl_delay: 6, blocked_share: 0, requests: 412 })}
      />,
    );

    const lines = screen
      .getAllByText(/./, { selector: 'p, .mantine-Badge-label' })
      .map((node) => plain(node.textContent));
    expect(lines).toEqual([
      'частично: 412 из 1 000 страниц',
      'кончился час на донора; сайт просит паузу 6 с между страницами',
      'закрыто 0% из 412 запросов',
    ]);
  });
});
