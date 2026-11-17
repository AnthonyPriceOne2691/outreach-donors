/**
 * Обход 24/7 у донора на вкладке «Обход доноров» (17.11.2026): строка под значком обхода, пометка
 * «мало рекламодателей», пометка руками и фильтр.
 *
 * Проверяется то, чем строка может подвести: показать 24/7 там, где его не было, или не показать,
 * где был; не сказать, кто поставил пометку и на какой доле; дать кнопки без права запуска;
 * отправить не то, что нажали; оставить старое состояние после нажатия; дать фильтр, которого
 * сервер не знает, — тогда под нажатой кнопкой стояли бы все доноры.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { DonorDepth } from '../api/crawlAlways';
import type { CrawlDonorsView, CrawlRow, CrawlSummary, CrawlTarget } from '../api/crawls';
import type { Me } from '../api/types';
import {
  BACKLOG_ROUTES,
  NO_NICHE,
  OPERATOR,
  PROMOTE_ROUTES,
  reviewQueue,
  TOKEN_KEY,
} from '../test/fixtures';
import type { Call } from '../test/server';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { depthLines, depthShown, depthWords, markActions } from './crawlAlwaysWords';
import { readCrawlFilters, writeCrawlFilters } from './crawlFilters';

const WEAK_URL = '/api/crawls/always/weak';

function crawl(host: string): CrawlRow {
  return {
    id: 7,
    host,
    status: 'done',
    outcome: 'ok',
    stop_reason: 'max_pages',
    pages: 1000,
    max_pages: 1000,
    articles: 900,
    links: 120,
    resumes: 0,
    requested_by: null,
    created_at: '2026-11-16T15:00:00Z',
    finished_at: '2026-11-16T16:00:00Z',
    reason: null,
  };
}

function depth(overrides: Partial<DonorDepth>): DonorDepth {
  return {
    pages: 10_000,
    parts: 1,
    yield_pct: 0.04,
    weak: false,
    weak_by: null,
    exhausted: false,
    running: false,
    ...overrides,
  };
}

function donor(host: string, said?: DonorDepth | null): CrawlTarget {
  const row: CrawlTarget = {
    host,
    price: 150,
    currency: 'USD',
    source: 'reply',
    priced_at: '2026-11-01T10:00:00Z',
    crawl: crawl(host),
    price_state: 'fresh',
  };
  // Поля нет — ответ сервера, который о 24/7 ещё не знает.
  return said === undefined ? row : { ...row, depth: said };
}

const AUTO = depth({ weak: true, weak_by: 'auto' });
const HUMAN = depth({ pages: 20_000, parts: 2, yield_pct: 0.02, weak: true, weak_by: 'human' });
const CLEARED = depth({ yield_pct: 0.03, weak_by: 'human' });
const RICH = depth({ pages: 25_431, parts: 3, yield_pct: 6.5, exhausted: true });
const GOING = depth({ pages: 3200, yield_pct: null, running: true });
const UNTOUCHED = depth({ pages: 0, parts: 0, yield_pct: null });

const ROWS: CrawlTarget[] = [
  donor('auto.example', AUTO),
  donor('human.example', HUMAN),
  donor('cleared.example', CLEARED),
  donor('rich.example', RICH),
  donor('going.example', GOING),
  donor('untouched.example', UNTOUCHED),
  donor('old.example'),
];

const COUNTS = {
  status: { never: 0, queued: 0, running: 0, done: 7, partial: 0, refused: 0, stopped: 0 },
  price: { fresh: 7, stale: 0, none: 0 },
  weak: 2,
};

const PAGE: CrawlDonorsView = {
  rows: ROWS,
  total: ROWS.length,
  page: 1,
  limit: 50,
  counts: COUNTS,
  startable: 7,
  bulk_max: 10_000,
  launch_max: 50,
  workers: 16,
  typical_crawl_sec: 1500,
  max_pages: 1000,
};

const SUMMARY: CrawlSummary = { counts: COUNTS, startable: 7, active: 0, workers: 16 };

const WATCHER: Me = { ...OPERATOR, permissions: ['advertisers', 'view'] };

/** Текст без неразрывных пробелов разрядов и единиц. */
function plain(text: string | null | undefined): string {
  return (text ?? '').replace(/[\u00a0\u202f]/g, ' ');
}

/** Сервер пометок: отвечает новым состоянием донора и помнит его — перечитанная страница
 *  уже с ним. */
function marking(rows: CrawlTarget[]) {
  let page: CrawlDonorsView = { ...PAGE, rows };
  return {
    donors: () => ({ body: page }),
    mark: (call: Call) => {
      const { host, weak } = call.body as { host: string; weak: boolean | null };
      const was = page.rows.find((row) => row.host === host)?.depth ?? depth({});
      const now: DonorDepth = {
        ...was,
        weak: weak ?? (was.yield_pct ?? 0) < 0.1,
        weak_by: weak === null ? ((was.yield_pct ?? 0) < 0.1 ? 'auto' : null) : 'human',
      };
      page = {
        ...page,
        rows: page.rows.map((row) => (row.host === host ? { ...row, depth: now } : row)),
      };
      return { body: now };
    },
  };
}

function open(
  who: Me = OPERATOR,
  routes: Record<string, unknown> = {},
  at = '/advertisers?view=crawl',
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const server = marking(ROWS);
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    ...PROMOTE_ROUTES,
    ...BACKLOG_ROUTES,
    ...NO_NICHE,
    'GET /api/advertisers': { body: reviewQueue({ counts: { bought: 1, pending: 0 } }) },
    'GET /api/crawls/summary': { body: SUMMARY },
    'GET /api/crawls/donors': server.donors,
    [`POST ${WEAK_URL}`]: server.mark,
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, at);
  return recorded;
}

function count(recorded: ReturnType<typeof open>, method: string, path: string): number {
  return recorded.calls.filter((call) => call.method === method && call.path === path).length;
}

function posted(recorded: ReturnType<typeof open>): unknown[] {
  return recorded.calls.filter((call) => call.path === WEAK_URL).map((call) => call.body);
}

async function row(host: string): Promise<HTMLElement> {
  const table = await screen.findByRole('table', { name: 'Доноры для обхода' });
  return (await within(table).findByText(host)).closest('tr')!;
}

/** Открыть «i» строки 24/7 у донора — окошком, с кнопками внутри. */
async function hint(host: string): Promise<HTMLElement> {
  const user = userEvent.setup();
  await user.click(within(await row(host)).getByRole('button', { name: `Обход 24/7: ${host}` }));
  return screen.findByRole('dialog');
}

describe('обход 24/7 в строке донора', () => {
  it('страницы 24/7 — под значком обхода; идёт и весь сайт — словами', async () => {
    open();

    expect(plain((await row('auto.example')).textContent)).toContain('24/7: 10 000 страниц');
    expect(plain((await row('going.example')).textContent)).toContain('24/7 идёт: 3 200 страниц');
    expect(plain((await row('rich.example')).textContent)).toContain(
      '24/7: 25 431 страница — весь сайт',
    );
  });

  it('24/7 донора не трогал или сервер о нём не знает — строки нет', async () => {
    open();

    for (const host of ['untouched.example', 'old.example']) {
      const shown = await row(host);
      expect(shown.querySelector('.crawlDepth')).toBeNull();
      expect(shown).not.toHaveTextContent('24/7');
    }
  });

  it('пометка — значком; снятая руками — тихо словами', async () => {
    open();

    expect(within(await row('auto.example')).getByText('мало рекламодателей')).toBeInTheDocument();
    expect(within(await row('human.example')).getByText('мало рекламодателей')).toBeInTheDocument();
    const cleared = await row('cleared.example');
    expect(within(cleared).queryByText('мало рекламодателей')).toBeNull();
    expect(within(cleared).getByText('пометка снята')).toBeInTheDocument();
    expect(within(await row('rich.example')).queryByText(/пометка|мало рекламодателей/)).toBeNull();
  });

  it('«i»: доля страниц с платной ссылкой и кто пометил; сервис — «Снять пометку»', async () => {
    open();

    const shown = await hint('auto.example');
    expect(plain(shown.textContent)).toContain('Обход 24/7 прошёл 10 000 страниц за 1 часть.');
    expect(plain(shown.textContent)).toContain('Платная ссылка на 4 из 10 000 страниц — 0,04%.');
    expect(shown).toHaveTextContent('Пометил сервис: доля ниже порога.');
    expect(
      within(shown)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['Снять пометку']);
  });

  it('«Снять пометку» — на сервер донор и `false`; строка и «i» — сразу по ответу', async () => {
    const recorded = open();
    const user = userEvent.setup();

    const shown = await hint('auto.example');
    await user.click(within(shown).getByRole('button', { name: 'Снять пометку' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ host: 'auto.example', weak: false }]));
    const now = await row('auto.example');
    await waitFor(() => expect(within(now).getByText('пометка снята')).toBeInTheDocument());
    expect(within(now).queryByText('мало рекламодателей')).toBeNull();
    // «i» не закрылось и говорит уже о новом: снял человек, можно вернуть сервису.
    expect(
      await within(shown).findByRole('button', { name: 'Вернуть оценку сервису' }),
    ).toBeVisible();
    expect(within(shown).getByRole('button', { name: 'Пометить' })).toBeVisible();
    expect(shown).toHaveTextContent('Пометку снял человек — сайт идёт дальше при любом пороге.');
    // Страница доноров и блок 24/7 перечитаны: под фильтром донор мог уйти, числа блока —
    // смениться.
    await waitFor(() => expect(count(recorded, 'GET', '/api/crawls/donors')).toBe(2));
    await waitFor(() => expect(count(recorded, 'GET', '/api/crawls/always')).toBe(2));
  });

  it('пометил человек — снять или вернуть оценку сервису (`null`)', async () => {
    const recorded = open();
    const user = userEvent.setup();

    const shown = await hint('human.example');
    expect(shown).toHaveTextContent('Пометил человек — порог сервиса к сайту не применяется.');
    expect(
      within(shown)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['Снять пометку', 'Вернуть оценку сервису']);
    await user.click(within(shown).getByRole('button', { name: 'Вернуть оценку сервису' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ host: 'human.example', weak: null }]));
  });

  it('пометку снял человек — «Пометить» и «Вернуть оценку сервису»', async () => {
    open();

    const shown = await hint('cleared.example');
    expect(
      within(shown)
        .getAllByRole('button')
        .map((button) => button.textContent),
    ).toEqual(['Пометить', 'Вернуть оценку сервису']);
  });

  it('не помечен — «Пометить» уходит с `true`', async () => {
    const recorded = open();
    const user = userEvent.setup();

    const shown = await hint('rich.example');
    expect(plain(shown.textContent)).toContain(
      'Обход 24/7 прошёл 25 431 страницу за 3 части — сайт пройден до конца.',
    );
    expect(plain(shown.textContent)).toContain(
      'Платная ссылка на 1 653 из 25 431 страницы — 6,5%.',
    );
    await user.click(within(shown).getByRole('button', { name: 'Пометить' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ host: 'rich.example', weak: true }]));
    await waitFor(async () =>
      expect(within(await row('rich.example')).getByText('мало рекламодателей')).toBeVisible(),
    );
  });

  it('без права запуска — «i» с долей и тем, кто пометил, но без кнопок', async () => {
    open(WATCHER);

    const shown = await hint('auto.example');
    expect(shown).toHaveTextContent('Пометил сервис: доля ниже порога.');
    expect(within(shown).queryByRole('button')).toBeNull();
    // Вкладка отдаёт блоку 24/7 то же право: строки есть, кнопок и поля нет.
    const always = screen.getByRole('group', { name: 'Обход 24/7' });
    expect(within(always).queryByRole('button', { name: /Запустить|Пауза/ })).toBeNull();
    expect(within(always).queryByRole('textbox')).toBeNull();
  });

  it('отказ сервера — словами в том же «i»', async () => {
    const refusal = 'Донора нет среди сайтов обхода 24/7.';
    open(OPERATOR, { [`POST ${WEAK_URL}`]: { status: 409, body: { detail: refusal } } });
    const user = userEvent.setup();

    const shown = await hint('auto.example');
    await user.click(within(shown).getByRole('button', { name: 'Снять пометку' }));

    expect(await within(shown).findByRole('alert')).toHaveTextContent(
      `Пометка не сменилась: ${refusal}`,
    );
    expect(within(await row('auto.example')).getByText('мало рекламодателей')).toBeInTheDocument();
  });

  it('на телефоне строка 24/7 — своей строкой блока под значком, переносом, а не за край', async () => {
    // Раскладку jsdom не считает — проверяется договор с `glass.css`: строка 24/7 — ребёнок блока
    // значка (`crawlSlot`), во всю его ширину, и переносится внутри себя; у ячейки — подпись
    // карточки телефона.
    open();

    const shown = await row('auto.example');
    const line = shown.querySelector<HTMLElement>('.crawlDepth');
    expect(line?.parentElement).toHaveClass('crawlSlot');
    expect(line?.closest('td')).toHaveAttribute('data-label', 'Последний обход');
    const glass = readFileSync(resolve(process.cwd(), 'src/styles/glass.css'), 'utf8');
    const rule = /\.crawlSlot \.crawlDepth \{([^}]*)\}/.exec(glass)?.[1] ?? '';
    expect(rule).toMatch(/flex-basis: 100%;/);
    expect(rule).toMatch(/flex-wrap: wrap;/);
    // «i» — одним куском с пометкой, а без неё — со словами: в узкой колонке (снимок 1280 и 390,
    // 17.11.2026) он уходил на строку один.
    const tail = /\.crawlSlot \.crawlDepthTail \{([^}]*)\}/.exec(glass)?.[1] ?? '';
    expect(tail).toMatch(/display: inline-flex;/);
    const info = (host: string, at: HTMLElement) =>
      within(at).getByRole('button', { name: `Обход 24/7: ${host}` }).parentElement;
    expect(info('auto.example', shown)).toHaveClass('crawlDepthTail');
    expect(info('auto.example', shown)).toHaveTextContent(/^мало рекламодателей$/);
    const bare = await row('rich.example');
    expect(info('rich.example', bare)).toHaveClass('crawlDepthTail');
    expect(plain(info('rich.example', bare)?.textContent)).toBe(
      '24/7: 25 431 страница — весь сайт',
    );
  });
});

describe('фильтр «мало рекламодателей»', () => {
  it('кнопка с числом сервера — рядом со статусами; нажать — условие уходит на сервер', async () => {
    const recorded = open(OPERATOR, {
      'GET /api/crawls/donors?weak=true': {
        body: { ...PAGE, rows: [ROWS[0]!, ROWS[1]!], total: 2 },
      },
    });
    const user = userEvent.setup();

    const chip = await screen.findByRole('button', { name: 'мало рекламодателей · 2' });
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    await user.click(chip);

    await waitFor(() => expect(count(recorded, 'GET', '/api/crawls/donors?weak=true')).toBe(1));
    expect(await screen.findByRole('button', { name: 'мало рекламодателей · 2' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await waitFor(() => expect(screen.queryByText('rich.example')).toBeNull());
  });

  it('из адреса — сужает сразу; «Обойти всех по фильтру» несёт то же условие', async () => {
    const recorded = open(
      OPERATOR,
      {
        'GET /api/crawls/donors?weak=true': {
          body: { ...PAGE, rows: [ROWS[0]!, ROWS[1]!], total: 2, startable: 2 },
        },
        'POST /api/crawls/donors/crawl': {
          body: { asked: 2, deferred: false, queued: 2, busy: 0, failed: 0, workers: 16 },
        },
      },
      '/advertisers?view=crawl&weak=true',
    );
    const user = userEvent.setup();

    expect(await screen.findByRole('button', { name: 'мало рекламодателей · 2' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(count(recorded, 'GET', '/api/crawls/donors')).toBe(0);
    await user.click(await screen.findByRole('button', { name: 'Обойти всех по фильтру · 2' }));
    const dialog = await screen.findByRole('dialog', { name: 'Обойти всех по фильтру?' });
    expect(dialog).toHaveTextContent('мало рекламодателей');
    await user.click(within(dialog).getByRole('button', { name: 'Поставить 2' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.path === '/api/crawls/donors/crawl')?.body).toEqual(
        { weak: true, expected: 2 },
      ),
    );
  });

  it('сервер числа не прислал — фильтра он не знает, и кнопки нет; ноль — тоже нет', async () => {
    const older = { status: COUNTS.status, price: COUNTS.price };
    open(OPERATOR, { 'GET /api/crawls/donors': { body: { ...PAGE, counts: older } } });

    await row('auto.example');
    expect(screen.queryByRole('button', { name: /мало рекламодателей/ })).toBeNull();
  });

  it('ноль помеченных — кнопки нет', async () => {
    open(OPERATOR, {
      'GET /api/crawls/donors': { body: { ...PAGE, counts: { ...COUNTS, weak: 0 } } },
    });

    await row('auto.example');
    expect(screen.queryByRole('button', { name: /мало рекламодателей/ })).toBeNull();
  });

  it('пусто под фильтром — условие названо словами', async () => {
    open(
      OPERATOR,
      {
        'GET /api/crawls/donors?weak=true': {
          body: { ...PAGE, rows: [], total: 0, counts: { ...COUNTS, weak: 0 } },
        },
      },
      '/advertisers?view=crawl&weak=true',
    );

    expect(await screen.findByText('Под фильтр никто не попал.')).toBeInTheDocument();
    expect(await screen.findByText(/^Условия: мало рекламодателей\./)).toBeInTheDocument();
  });

  it('условие — в адрес и обратно; незнакомое значение не сужает', () => {
    const filters = readCrawlFilters(new URLSearchParams('weak=true&status=done'));
    expect(filters.weak).toBe(true);
    expect(writeCrawlFilters(filters).toString()).toBe('view=crawl&status=done&weak=true');
    expect(readCrawlFilters(new URLSearchParams('weak=1')).weak).toBe(false);
  });
});

describe('обход 24/7 у донора: слова', () => {
  it('строки нет, пока 24/7 донора не трогал', () => {
    expect(depthShown(UNTOUCHED)).toBe(false);
    expect(depthShown(GOING)).toBe(true);
    expect(depthShown({ ...UNTOUCHED, weak: true, weak_by: 'human' })).toBe(true);
  });

  it('строка — страницы в именительном', () => {
    expect(plain(depthWords(depth({ pages: 1 })))).toBe('24/7: 1 страница');
    expect(plain(depthWords(depth({ pages: 22 })))).toBe('24/7: 22 страницы');
    expect(plain(depthWords(GOING))).toBe('24/7 идёт: 3 200 страниц');
  });

  it('«i»: части, доля и кто решил — по порогу доля пишется по свою сторону', () => {
    expect(depthLines(GOING).map(plain)).toEqual([
      'Обход 24/7 прошёл 3 200 страниц за 1 часть — часть идёт сейчас.',
    ]);
    expect(plain(depthLines(depth({ yield_pct: 0.0999 }), 0.1)[1])).toBe(
      'Платная ссылка на 10 из 10 000 страниц — 0,0999%.',
    );
    expect(plain(depthLines(HUMAN)[0])).toBe('Обход 24/7 прошёл 20 000 страниц за 2 части.');
  });

  it('кнопки — по тому, кто решил', () => {
    const labels = (said: DonorDepth) => markActions(said).map((action) => action.label);
    expect(labels(AUTO)).toEqual(['Снять пометку']);
    expect(labels(HUMAN)).toEqual(['Снять пометку', 'Вернуть оценку сервису']);
    expect(labels(CLEARED)).toEqual(['Пометить', 'Вернуть оценку сервису']);
    expect(labels(RICH)).toEqual(['Пометить']);
    expect(markActions(HUMAN).map((action) => action.weak)).toEqual([false, null]);
  });
});
