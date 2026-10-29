/**
 * Почему обход кончился и сколько это от потолка — словами под значком (23.10.2026): каждое
 * слово причины, потолки — числами сервера, и запись без новых полей — значок как был, слов с
 * числами, которых нет, — нет.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { CrawlPlace, CrawlRow, CrawlTargets } from '../api/crawls';
import { renderWith } from '../test/render';
import { CrawlState, crawlState } from './CrawlTable';
import { crawlRule, queueRule, queueWords, spanWords, stopWords } from './crawlWords';

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

  // Сайт не пустил с самого начала — причина словами, а не голый красный значок (тест-группа
  // 29.10.2026: «2 были с ошибками»); и что повтор, скорее всего, кончится так же.
  it.each([
    [
      { stop_reason: 'robots', outcome: 'forbidden', pages: 0 },
      'robots.txt запрещает',
      'правила сайта для роботов запрещают обход; повтор поможет, только если сайт их сменит',
    ],
    [
      { stop_reason: 'robots', outcome: 'failed', pages: 0 },
      'robots.txt не отдал',
      'сайт не отдал правила для роботов — отказ, обрыв или сбой; без них не обходим, повтор обычно кончается так же',
    ],
    // Запрос правил счёлся закрытым (403 на robots.txt) — та же беда, тот же значок.
    [
      { stop_reason: 'robots', outcome: 'blocked', pages: 0 },
      'robots.txt не отдал',
      'сайт не отдал правила для роботов — отказ, обрыв или сбой; без них не обходим, повтор обычно кончается так же',
    ],
    [
      { stop_reason: 'no_start', outcome: 'blocked', pages: 0 },
      'закрылся от нас',
      'главная закрыта с первого запроса — защита от ботов; повтор обычно кончается так же',
    ],
    [
      { stop_reason: 'no_start', outcome: 'failed', pages: 0 },
      'не открылся',
      'главная не ответила ни в одном виде — сайт недоступен; можно повторить позже',
    ],
  ] as [Partial<CrawlRow>, string, string][])('не пустил: %o → %s', (overrides, label, said) => {
    expect(crawlState(done(overrides)).label).toBe(label);
    expect(stopWords(done(overrides))).toBe(said);
  });

  it.each([
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

describe('в очереди — где ждёт и когда начнётся', () => {
  const waiting = (place: CrawlPlace | null) =>
    old({ status: 'queued', outcome: null, stop_reason: null, pages: 0, place });

  // Тест-группа 29.10.2026: десять доноров разом, восемь пошли, двое «в очереди» — «когда она
  // активируется?». Ответ — под значком: занятые обходчики и место; начнётся сам.
  it.each([
    [
      { busy: 8, workers: 8, ahead: 1 },
      'ждёт свободного обходчика — заняты 8 из 8; перед ним 1 обход',
    ],
    [
      { busy: 8, workers: 8, ahead: 5 },
      'ждёт свободного обходчика — заняты 8 из 8; перед ним 5 обходов',
    ],
    [{ busy: 8, workers: 8, ahead: 0 }, 'ждёт свободного обходчика — заняты 8 из 8; он следующий'],
    // Места нет (задачу как раз берёт обходчик или очередь не ответила) — без места.
    [{ busy: 8, workers: 8, ahead: null }, 'ждёт свободного обходчика — заняты 8 из 8'],
    // Обходчик упал, а строка ещё «идёт» — занятых больше, чем обходчиков: заняты все.
    [{ busy: 9, workers: 8, ahead: 0 }, 'ждёт свободного обходчика — заняты 8 из 8; он следующий'],
    [{ busy: 3, workers: 8, ahead: 0 }, 'есть свободный обходчик — вот-вот начнётся'],
    [{ busy: 3, workers: 8, ahead: 0, held: 0 }, 'есть свободный обходчик — вот-вот начнётся'],
    // Свободные обходчики есть, но их держит сторож нагрузки: обход ждёт машину, а не обходчика.
    [
      {
        busy: 10,
        workers: 16,
        ahead: 0,
        held: 6,
        held_reason: 'свободно 1,2 ядра, а новому обходу нужно 1,75 (0,75 на обход и 1 в запас)',
      },
      'машина занята — обходчики ждут, пока спадёт нагрузка: свободно 1,2 ядра, а новому ' +
        'обходу нужно 1,75 (0,75 на обход и 1 в запас)',
    ],
    [
      { busy: 10, workers: 16, ahead: 0, held: 6, held_reason: null },
      'машина занята — обходчики ждут, пока спадёт нагрузка',
    ],
    [{ busy: 0, workers: 0, ahead: 0 }, 'обходчиков нет — обход никто не возьмёт'],
    [{ busy: 8, workers: null, ahead: 2 }, 'ждёт свободного обходчика — начнётся сам'],
    // Сервер старше экрана или «Приёмка по донорам» (очередь она не спрашивает).
    [null, 'ждёт свободного обходчика — начнётся сам'],
  ] as [CrawlPlace | null, string][])('%o → %s', (place, said) => {
    expect(queueWords(waiting(place))).toBe(said);
  });

  it('у идущего и законченного места нет', () => {
    expect(queueWords(old({ status: 'running', outcome: null, stop_reason: null }))).toBeNull();
    expect(queueWords(done())).toBeNull();
  });

  it('строкой под значком «в очереди»', () => {
    renderWith(<CrawlState crawl={waiting({ busy: 8, workers: 8, ahead: 1 })} />);

    const lines = screen
      .getAllByText(/./, { selector: 'p, .mantine-Badge-label' })
      .map((node) => node.textContent);
    expect(lines).toEqual([
      'в очереди',
      'ждёт свободного обходчика — заняты 8 из 8; перед ним 1 обход',
    ]);
  });

  it('«i» панели: сколько идёт сразу, остальные начинаются сами, почему сайт не обходится', () => {
    const targets: CrawlTargets = {
      donors: [],
      no_price: 0,
      stale_price: 0,
      supplier: 0,
      notes: [],
      max_pages: 1000,
      workers: 8,
    };
    const said = queueRule(targets);

    expect(said).toBe(
      ' Сразу идёт столько обходов, сколько обходчиков — сейчас 8 обходчиков; остальные ждут в' +
        ' очереди и начинаются сами, как только обходчик освобождается: у ждущего видно, сколько' +
        ' обходчиков занято и сколько обходов перед ним. Новый обход начинается, только если на' +
        ' машине есть свободные ядра под него: машина занята — ждущий так и скажет. Сайт, не' +
        ' отдавший правила для роботов (robots.txt) или запретивший в них обход, не обходим, как' +
        ' и закрывшийся от нас с первой' +
        ' страницы: причина — под значком, и повтор такого сайта обычно кончается так же.',
    );
    // Обходчиков не спросили — без числа, а не «сейчас 0».
    expect(queueRule(undefined)).toContain('сколько обходчиков; остальные ждут');
  });
});
