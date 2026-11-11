/**
 * «Обойти заново, 10 000» (02.11.2026, `crawl/deep.py`).
 *
 * Проверяется то, ради чего окно: до нажатия видно, что такой обход идёт часами, сколько разом и
 * сколько займёт всех; нельзя обойти — кнопка выключена с причиной; на сервер уходят те, кого
 * выбрали; исход — словами; обход помечен глубоким и идёт к своему потолку.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MantineProvider } from '@mantine/core';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type {
  CrawlDeepResult,
  CrawlDeepRules,
  CrawlDonorsView,
  CrawlRow,
  CrawlSummary,
  CrawlTarget,
} from '../api/crawls';
import {
  BACKLOG_ROUTES,
  NO_NICHE,
  OPERATOR,
  PROMOTE_ROUTES,
  reviewQueue,
  TOKEN_KEY,
} from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { deepPart } from './CrawlBacklog';
import { crawlState, CrawlState, deepRefusal } from './CrawlTable';
import { deepSummary } from './crawlQueries';
import { deepCost, deepLabel, deepNow, deepTiming, deepTitle, deepWho } from './DeepCrawl';

const RULES: CrawlDeepRules = {
  max_pages: 10_000,
  max_running: 4,
  going: 0,
  waiting: 0,
  typical_sec: 15_000,
  max_seconds: 57_600,
  dr_units: 2,
  dr_min_units: 50,
};

/** Текст без неразрывных пробелов разрядов. */
function plain(text: string): string {
  return text.replace(/[\u00a0\u202f]/g, ' ');
}

function crawl(overrides: Partial<CrawlRow>): CrawlRow {
  return {
    id: 7,
    host: 'done.example',
    status: 'done',
    outcome: 'ok',
    stop_reason: 'max_pages',
    pages: 1000,
    max_pages: 1000,
    articles: 900,
    links: 120,
    resumes: 0,
    requested_by: 'claude@site.com',
    created_at: '2026-10-06T15:00:00Z',
    finished_at: '2026-10-06T16:00:00Z',
    reason: null,
    ...overrides,
  };
}

function donor(overrides: Partial<CrawlTarget>): CrawlTarget {
  return {
    host: 'done.example',
    price: 150,
    currency: 'USD',
    source: 'reply',
    priced_at: '2026-10-01T10:00:00Z',
    crawl: crawl({}),
    price_state: 'fresh',
    ...overrides,
  };
}

const COUNTS = {
  status: { never: 0, queued: 0, running: 0, done: 2, partial: 0, refused: 0, stopped: 0 },
  price: { fresh: 1, stale: 1, none: 0 },
};

const PAGE: CrawlDonorsView = {
  rows: [
    donor({}),
    donor({ host: 'stale.example', price_state: 'stale', crawl: crawl({ host: 'stale.example' }) }),
  ],
  total: 2,
  page: 1,
  limit: 50,
  counts: COUNTS,
  startable: 7,
  bulk_max: 10_000,
  launch_max: 50,
  workers: 16,
  typical_crawl_sec: 1500,
  max_pages: 1000,
  deep: RULES,
};

const SUMMARY: CrawlSummary = { counts: COUNTS, startable: 7, active: 0, workers: 16 };

const STARTED: CrawlDeepResult = {
  asked: 1,
  backlog: 1,
  busy: [],
  refused: {},
  rules: { ...RULES, waiting: 1 },
};

function open(page: CrawlDonorsView = PAGE) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: OPERATOR },
    ...PROMOTE_ROUTES,
    ...BACKLOG_ROUTES,
    ...NO_NICHE,
    'GET /api/advertisers': { body: reviewQueue({ counts: { bought: 1, pending: 0 } }) },
    'GET /api/crawls/summary': { body: SUMMARY },
    'GET /api/crawls/donors': { body: page },
    'POST /api/crawls/deep': { body: STARTED },
    'POST /api/crawls/donors/crawl': {
      body: {
        asked: 7,
        deferred: true,
        queued: null,
        busy: null,
        failed: null,
        backlog: 7,
        workers: 16,
      },
    },
  });
  renderWith(<AppRoutes />, '/advertisers?view=crawl');
  return recorded;
}

function sent(recorded: ReturnType<typeof open>, path: string): unknown[] {
  return recorded.calls.filter((call) => call.path === path).map((call) => call.body);
}

describe('слова окна', () => {
  it('подпись и заголовок — числом сервера', () => {
    expect(plain(deepLabel(RULES, 'всех'))).toBe('Заново всех, 10 000');
    expect(plain(deepTitle(RULES))).toBe('Обойти заново, до 10 000 страниц');
  });

  it('один донор: сколько идёт и сколько разом — часы, а не минуты', () => {
    const said = plain(deepTiming(1, RULES));
    expect(said).toContain('около 4 ч 10 мин');
    expect(said).toContain('самое большее 16 ч');
    expect(said).toContain('Разом идёт не больше 4');
    expect(said).not.toContain('все пройдут');
  });

  it('много доноров: сколько займут все при пределе разом', () => {
    expect(plain(deepTiming(40, RULES))).toContain(
      'остальные ждут в очереди загрузки: все пройдут ≈ 42 ч',
    );
  });

  it('что уже идёт и ждёт — числами; никого — строки нет', () => {
    expect(deepNow(RULES)).toBeNull();
    expect(deepNow({ ...RULES, going: 4 })).toBe('Сейчас таких обходов идёт 4 из 4.');
    expect(deepNow({ ...RULES, going: 4, waiting: 3 })).toBe(
      'Сейчас таких обходов идёт 4 из 4, ждут ещё 3.',
    );
  });

  it('цена — только за новые домены, числами сервера, и кап не останавливает обход', () => {
    const said = deepCost(RULES);
    expect(said).toContain('только за DR новых доменов');
    expect(said).toContain('2 за домен, не меньше 50 за запрос');
    expect(said).toContain('DR подождёт, а обход дойдёт');
  });

  it('кого обходим — именем одного или числом', () => {
    expect(deepWho(['one.example'])).toBe('Донор one.example.');
    expect(deepWho(['one.example', 'two.example'])).toBe('Отмечено 2 донора.');
  });
});

describe('исход словами', () => {
  it('встали — куда и что будет дальше', () => {
    expect(plain(deepSummary(STARTED))).toBe(
      'В очередь загрузки — 1 донор: разом идёт не больше 4, начнётся само.',
    );
  });

  it('уже шли и не принятые — названы', () => {
    const said = deepSummary({
      ...STARTED,
      backlog: 0,
      busy: ['going.example'],
      refused: { 'stale.example': 'цена старше 150 дней' },
    });
    expect(said).toBe('Уже идут: going.example. stale.example: цена старше 150 дней.');
  });

  it('ноль вставших без отказов — они там уже стояли, а не тишина', () => {
    expect(deepSummary({ ...STARTED, backlog: 0 })).toBe('Уже ждут в очереди загрузки.');
  });
});

describe('строка обхода', () => {
  it('глубокий идёт к своему потолку', () => {
    const state = crawlState(crawl({ status: 'running', pages: 340, max_pages: 10_000 }));
    expect(plain(state.label)).toBe('идёт: 340 из 10 000');
  });

  it('в строке вкладки — без слова «страниц»: рядом две кнопки, а строка одной высоты', () => {
    const long = crawl({ deep: true, pages: 9870, max_pages: 10_000 });
    expect(plain(crawlState(long, true).label)).toBe('обойдён: 9 870 из 10 000');
    expect(plain(crawlState(long).label)).toBe('обойдён: 9 870 из 10 000 страниц');
  });

  it('глубокий помечен, обычный — нет', () => {
    const { rerender } = render(
      <MantineProvider>
        <CrawlState crawl={crawl({ deep: true, max_pages: 10_000 })} compact />
      </MantineProvider>,
    );
    expect(screen.getByText('глубокий')).toBeTruthy();
    rerender(
      <MantineProvider>
        <CrawlState crawl={crawl({})} compact />
      </MantineProvider>,
    );
    expect(screen.queryByText('глубокий')).toBeNull();
  });

  it('нет свежей цены — почему нельзя, до нажатия', () => {
    expect(deepRefusal(donor({}))).toBeNull();
    expect(deepRefusal(donor({ price_state: 'stale' }))).toContain('протухла');
    expect(deepRefusal(donor({ price_state: 'none' }))).toContain('Цены нет');
  });

  it('очередь загрузки называет глубоких, только если они есть', () => {
    expect(deepPart({ waiting: 5, deep: 2, eta_sec: 10, workers: 16 })).toBe(', из них глубоких 2');
    expect(deepPart({ waiting: 5, deep: 0, eta_sec: 10, workers: 16 })).toBe('');
    expect(deepPart({ waiting: 5, eta_sec: 10, workers: 16 })).toBe('');
  });
});

describe('вкладка', () => {
  it('кнопка в строке: окно со сроком и кого; на сервер — этот донор', async () => {
    const recorded = open();
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('button', { name: /^Обойти заново, .*: done\.example$/ }),
    );
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Донор done.example.')).toBeTruthy();
    expect(plain(dialog.textContent ?? '')).toContain('около 4 ч 10 мин');

    await user.click(within(dialog).getByRole('button', { name: 'Обойти заново' }));

    await waitFor(() =>
      expect(sent(recorded, '/api/crawls/deep')).toEqual([{ hosts: ['done.example'] }]),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('после глубокого обхода повтор только глубокий: обычный заменил бы найденное', async () => {
    const deepRow = donor({
      host: 'deep.example',
      crawl: crawl({ host: 'deep.example', deep: true, max_pages: 10_000 }),
    });
    open({ ...PAGE, rows: [deepRow] });

    await screen.findByRole('button', { name: /^Обойти заново, .*: deep\.example$/ });
    expect(screen.queryByRole('button', { name: 'Переобойти deep.example' })).toBeNull();
  });

  it('глубокий обход на телефоне — парой строк: значок, «глубокий», «i» и «10 000» — один поток переноса', async () => {
    // Проверка прода 10.11.2026 (390 px, карточка 284 px): группа значка с «глубокий» и «i» —
    // 296 px — переносила «i» внутри себя и занимала всю ширину: «i» вставал второй строкой, а
    // «10 000» — третьей, лесенкой. Раскладку jsdom не считает — проверяется договор с `glass.css`:
    // своего ряда у группы в блоке нет, и значок, «i» и кнопка переносятся одним потоком (замер в
    // браузере: второй строкой — «i» и «10 000» рядом).
    const deepRow = donor({
      host: 'deep.example',
      crawl: crawl({ host: 'deep.example', deep: true, pages: 10_000, max_pages: 10_000 }),
    });
    open({ ...PAGE, rows: [deepRow] });

    const again = await screen.findByRole('button', {
      name: /^Обойти заново, .*: deep\.example$/,
    });
    const slot = again.closest<HTMLElement>('.crawlSlot');
    expect(slot).not.toBeNull();
    const group = slot?.querySelector<HTMLElement>(':scope > .mantine-Group-root');
    expect(group?.querySelector('.crawlBadge')).toHaveTextContent(/^обойдён: 10.000 из 10.000$/);
    expect(within(group as HTMLElement).getByText('глубокий')).toBeInTheDocument();
    expect(
      within(group as HTMLElement).getByRole('button', { name: /^Подробнее: обойдён/ }),
    ).toBeInTheDocument();
    // Кнопка — сосед группы в том же блоке, а не строка под ней.
    expect(again.parentElement).toBe(slot);

    const glass = readFileSync(resolve(process.cwd(), 'src/styles/glass.css'), 'utf8');
    expect(/\.crawlSlot \{([^}]*)\}/.exec(glass)?.[1]).toMatch(/flex-wrap: wrap;/);
    expect(glass).toMatch(/\.crawlSlot \.mantine-Group-root \{\s*display: contents;\s*\}/);
  });

  it('протухшая цена — кнопка выключена, причина видна до нажатия', async () => {
    open();

    const button = await screen.findByRole('button', {
      name: /^Обойти заново, .*: stale\.example$/,
    });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(button.getAttribute('title')).toContain('перезапрос');
  });

  it('отмеченные: кнопка считает их, на сервер — они', async () => {
    const recorded = open();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('checkbox', { name: 'Обойти done.example' }));
    await user.click(screen.getByRole('button', { name: /^Заново отмеченных, .* · 1$/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Обойти заново' }));

    await waitFor(() =>
      expect(sent(recorded, '/api/crawls/deep')).toEqual([{ hosts: ['done.example'] }]),
    );
  });

  it('все по фильтру: окно с числом и сроком всех; на сервер — признак и сколько видели', async () => {
    const recorded = open();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /^Заново всех, .* · 7$/ }));
    const dialog = await screen.findByRole('dialog');
    expect(plain(dialog.textContent ?? '')).toContain('Встанут в очередь: 7 доноров');
    await user.click(within(dialog).getByRole('button', { name: 'Обойти заново' }));

    await waitFor(() =>
      expect(sent(recorded, '/api/crawls/donors/crawl')).toEqual([{ expected: 7, deep: true }]),
    );
  });

  it('сервер старше экрана — кнопок нет, вкладка работает', async () => {
    const old = { ...PAGE };
    delete old.deep;
    open(old);

    await screen.findByText('done.example');
    expect(screen.queryByRole('button', { name: /[Зз]аново/ })).toBeNull();
  });
});
