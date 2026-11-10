/**
 * Журнал действий на экране: кто, что, когда, над чем и подробности — словами, а не
 * кодами журнала; фильтры — в адресе и в запросе; отрезок с концом раньше начала не
 * уходит на сервер; без права «Продажи» — без действий продаж и со словами об этом.
 * Подробности — итог одной строкой и «+N ещё» на месте; шапка закреплена; страницы —
 * и над таблицей, и под ней (проверка прода 10.11.2026).
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { JournalView } from '../api/journal';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY, card } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { JOURNAL_MIN_WIDTH } from './JournalTable';

const PAGE: JournalView = {
  entries: [
    {
      id: 30,
      created_at: '2026-10-11T09:15:00Z',
      action: 'user_updated',
      author_id: 1,
      author_email: 'админ@site.com',
      target: 'user:2',
      details: { role: 'admin', is_active: false, permissions: { send: true, sales: false } },
    },
    {
      id: 29,
      created_at: '2026-10-11T09:00:00Z',
      action: 'run_started',
      author_id: 2,
      author_email: 'оператор@site.com',
      target: 'run:27',
      details: { ключей: 12, страна: 'us', 'потолок юнитов': 5000, задача: 'abc' },
    },
    {
      id: 28,
      created_at: '2026-10-11T08:00:00Z',
      action: 'login_failed',
      author_id: null,
      author_email: null,
      target: null,
      details: { email: 'чужой@x.example.test', reason: 'пара не подошла' },
    },
    {
      id: 27,
      created_at: '2026-10-10T18:00:00Z',
      action: 'user_updated',
      author_id: 1,
      author_email: 'админ@site.com',
      target: 'run:26',
      details: { действие: 'рассмотрение прогона', решение: 'accepted', кандидатов: 3 },
    },
    {
      id: 26,
      created_at: '2026-10-10T17:00:00Z',
      action: 'brand_new_action',
      author_id: null,
      author_email: null,
      target: 'run:1, run:2, reply:5',
      details: { кто: 'консоль', версия: { было: 3, стало: 4 } },
    },
  ],
  total: 5,
  page: 1,
  limit: 50,
  sales_hidden: false,
  advertisers_hidden: false,
};

const USERS = [card(ADMIN), card(OPERATOR)];

/** Письма в журнале: ушедшее по-настоящему и ушедшее нулевой почтой. */
const LETTERS: JournalView = {
  ...PAGE,
  entries: [
    {
      id: 41,
      created_at: '2026-10-11T10:00:00Z',
      action: 'letter_sent',
      author_id: 2,
      author_email: 'оператор@site.com',
      target: 'message:41',
      details: {
        кому: 'editor@ads.example.test',
        этап: 'advertisers',
        рекламодатель: 'ads.example.test',
        'от кого': 'hello@mail.example.test',
        транспорт: 'sendgrid',
        'ушло на самом деле': true,
      },
    },
    {
      id: 40,
      created_at: '2026-10-11T09:30:00Z',
      action: 'letter_sent',
      author_id: null,
      author_email: null,
      target: 'message:40',
      details: {
        кому: 'ceo@lead.example.test',
        этап: 'donors',
        донор: 'lead.example.test',
        'от кого': 'hi@mail.example.test',
        транспорт: 'null',
        'ушло на самом деле': false,
      },
    },
  ],
  total: 2,
};

function openJournal(path = '/journal', me: Me = ADMIN, view: JournalView = PAGE) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: me },
    'GET /api/users': { body: USERS },
    'GET /api/journal?page=1': { body: view },
    'GET /api/journal?page=1&action=login_failed': {
      body: { ...view, entries: [PAGE.entries[2]], total: 1 },
    },
  });
  renderWith(<AppRoutes />, path);
  return recorded;
}

/** Строка журнала по тексту в ней — когда ответ уже пришёл. В таблице, а не на всём
 *  экране: те же слова стоят пунктами в списке фильтра действий. */
async function rowOf(text: string): Promise<HTMLElement> {
  const table = await screen.findByRole('table');
  const row = (await within(table).findByText(text)).closest('tr');
  if (row === null) throw new Error(`строки с «${text}» нет`);
  return row;
}

/** Пункт выпадающего списка Mantine: в jsdom список остаётся `display: none` — раскладки
 *  нет, и без `hidden` пункты не видны запросу. Клик при этом настоящий. */
async function choose(field: string, option: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('textbox', { name: field }));
  await user.click(await screen.findByRole('option', { name: option, hidden: true }));
}

describe('журнал действий', () => {
  it('кто, что, над чем и подробности — словами, а не кодами', async () => {
    openJournal();

    expect(await screen.findByRole('heading', { name: 'Журнал действий' })).toBeInTheDocument();
    // Правка учётки: роль, учётка и права — словами окна прав, учётка — почтой.
    const access = await rowOf('Доступ учётки изменён');
    expect(within(access).getByText('учётка оператор@site.com')).toBeInTheDocument();
    // Роль — с заглавной, как все роли (`ROLE_TITLES`).
    expect(within(access).getByText(/Админ/)).toBeInTheDocument();
    expect(within(access).getByText(/отключена/)).toBeInTheDocument();
    expect(
      within(access).getByText(/отправлять письма — выдано, Продажи — снято/),
    ).toBeInTheDocument();
    // Прогон — ссылкой на его отчёт.
    const run = await rowOf('Прогон запущен');
    expect(within(run).getByRole('link', { name: 'прогон №27' })).toHaveAttribute(
      'href',
      '/runs/27',
    );
    // Неудачный вход с чужой почтой: автора нет, почта — в подробностях.
    const failed = await rowOf('Вход не удался');
    expect(within(failed).getByText('без автора')).toBeInTheDocument();
    expect(within(failed).getByText(/чужой@x\.example\.test/)).toBeInTheDocument();
    expect(within(failed).getByText(/пара не подошла/)).toBeInTheDocument();
  });

  it('у общей «Правки» под названием — что сделано, а решение — словом', async () => {
    openJournal();

    const review = await rowOf('рассмотрение прогона');
    expect(within(review).getByText('Правка')).toBeInTheDocument();
    expect(within(review).getByText(/принят/)).toBeInTheDocument();
    expect(within(review).queryByText(/accepted/)).not.toBeInTheDocument();
  });

  it('подробности — последней колонкой, по центру, и отделены от «Над чем» двойным зазором', async () => {
    // Просьба Anthony 12.10.2026: «посерединке… чуть-чуть расстояние добавить». Раскладку
    // jsdom не считает — проверяется договор с правилами `glass.css`: общее «всё, кроме
    // первой, — по центру» у `dataTable` и поле последней колонки у `journalTable`
    // (замер в браузере — в описании правила).
    openJournal();

    const table = await screen.findByRole('table');
    expect(table).toHaveClass('dataTable', 'journalTable');
    expect(within(table).getAllByRole('columnheader').at(-1)).toHaveTextContent('Подробности');
    const glass = readFileSync(resolve(process.cwd(), 'src/styles/glass.css'), 'utf8');
    expect(glass).toMatch(
      /\.dataTable th:not\(:first-child\),\s*\.dataTable td:not\(:first-child\) \{\s*text-align: center;/,
    );
    const last = /\.journalTable th:last-child,\s*\.journalTable td:last-child \{([^}]*)\}/.exec(
      glass,
    );
    expect(last?.[1]).toMatch(/padding-inline-start: calc\(2 \* var\(--table-horizontal-spacing/);
    expect(last?.[1]).not.toMatch(/text-align/);
  });

  it('влезает рядом с развёрнутым меню на 1440: подробностям остаётся не меньше 208 px', async () => {
    // Просьба Anthony 12.10.2026: на 1440 × 900 с развёрнутым меню под таблицу 1 078 px
    // (замер в браузере), а при 1 180 она уезжала в прокрутку, и подробности по центру
    // уходили за край. Запас — полоса прокрутки страницы, где она занимает место (до 17 px).
    openJournal();

    const table = await screen.findByRole('table');
    const widths = [...table.querySelectorAll('col')].map((col) => col.style.width);
    // Ширина задана у всех, кроме подробностей: им — остаток.
    expect(widths.at(-1)).toBe('');
    const fixed = widths.reduce(
      (sum, width) => sum + (width === '' ? 0 : parseFloat(width) * 16),
      0,
    );
    expect(JOURNAL_MIN_WIDTH - fixed).toBeGreaterThanOrEqual(208);
    expect(JOURNAL_MIN_WIDTH).toBeLessThanOrEqual(1078 - 17);
  });

  it('незнакомое действие не роняет экран, кто без учётки — из подробностей, было → стало', async () => {
    openJournal();

    const unknown = await rowOf('Другое действие (brand_new_action)');
    expect(within(unknown).getByText('консоль')).toBeInTheDocument();
    expect(within(unknown).getByText('прогон №1')).toBeInTheDocument();
    expect(within(unknown).getByText(/ответ №5/)).toBeInTheDocument();
    expect(within(unknown).getByText(/3 → 4/)).toBeInTheDocument();
  });

  it('фильтр действия — в запросе и в адресе, страница — с первой', async () => {
    const recorded = openJournal('/journal?page=1');

    await choose('Действие', 'Вход не удался');

    await waitFor(() =>
      expect(
        recorded.calls.some((call) => call.path === '/api/journal?page=1&action=login_failed'),
      ).toBe(true),
    );
    expect(await screen.findByText(/1 запись под фильтром/)).toBeInTheDocument();
  });

  it('конец отрезка раньше начала — словами под полем, на сервер не уходит', async () => {
    const recorded = openJournal('/journal?from=2026-10-06&to=2026-10-05');

    expect(await screen.findByText('Раньше первого дня — поменяйте даты')).toBeInTheDocument();
    expect(screen.getByText(/Конец отрезка раньше начала/)).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path.startsWith('/api/journal'))).toBe(false);
  });

  it('дни — по часам человека: «по» включает весь последний день', async () => {
    // Края — начало 5-го и начало 7-го по часам этой машины, а не сутки UTC.
    const since = new Date('2026-10-05T00:00:00').toISOString();
    const until = new Date('2026-10-07T00:00:00').toISOString();
    const asked = new URLSearchParams({ page: '1', since, until }).toString();
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/users': { body: USERS },
      [`GET /api/journal?${asked}`]: { body: { ...PAGE, entries: [], total: 0 } },
    });
    renderWith(<AppRoutes />, '/journal?from=2026-10-05&to=2026-10-06');

    expect(await screen.findByText(/Под фильтр ничего не попало/)).toBeInTheDocument();
    expect(recorded.calls.map((call) => call.path)).toContain(`/api/journal?${asked}`);
    expect(screen.getByLabelText('С')).toHaveValue('2026-10-05');
    expect(screen.getByLabelText('По')).toHaveValue('2026-10-06');
  });

  it('дата в поле — в адрес, а страница — снова первая', async () => {
    const day = new Date('2026-10-05T00:00:00').toISOString();
    const asked = new URLSearchParams({ page: '1', since: day }).toString();
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/users': { body: USERS },
      'GET /api/journal?page=2': { body: { ...PAGE, page: 2, total: 120 } },
      [`GET /api/journal?${asked}`]: { body: PAGE },
    });
    renderWith(<AppRoutes />, '/journal?page=2');

    fireEvent.change(await screen.findByLabelText('С'), { target: { value: '2026-10-05' } });

    await waitFor(() =>
      expect(recorded.calls.map((call) => call.path)).toContain(`/api/journal?${asked}`),
    );
  });

  it('без права «Продажи» — строка о том, что журнал без продаж, и их нет в фильтре', async () => {
    const noSales: Me = { ...ADMIN, permissions: ADMIN.permissions.filter((p) => p !== 'sales') };
    openJournal('/journal', noSales, { ...PAGE, sales_hidden: true });
    const user = userEvent.setup();

    expect(await screen.findByText(/Действия продаж не показаны/)).toBeInTheDocument();
    await user.click(screen.getByRole('textbox', { name: 'Действие' }));
    expect(await screen.findByRole('option', { name: 'Вход', hidden: true })).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'База лидов загружена', hidden: true }),
    ).not.toBeInTheDocument();
  });
});

describe('подробности: итог одной строкой, остальное — на месте', () => {
  it('у письма на виду кому и от кого, этап и домен — «+2 ещё», норма отправки не видна', async () => {
    openJournal('/journal', ADMIN, LETTERS);
    const user = userEvent.setup();

    const sent = await rowOf('письмо №41');
    expect(
      within(sent).getByText(/editor@ads\.example\.test.*hello@mail\.example\.test/),
    ).toBeInTheDocument();
    // Ушло по-настоящему — «как» не показывается вовсе: ни транспорта, ни «ушло: да».
    expect(within(sent).queryByText(/транспорт/)).not.toBeInTheDocument();
    expect(within(sent).queryByText(/ушло на самом деле/)).not.toBeInTheDocument();
    // Этап и домен — в документе (их находит поиск браузера), но скрыты до раскрытия.
    expect(within(sent).getByText('рекламодатель:')).not.toBeVisible();

    const more = within(sent).getByRole('button', { name: '+2\u00a0ещё' });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    const folded = document.getElementById(more.getAttribute('aria-controls') ?? '');
    expect(folded).toHaveAttribute('hidden', 'until-found');
    await user.click(more);

    expect(more).toHaveAttribute('aria-expanded', 'true');
    expect(more).toHaveTextContent('Свернуть');
    expect(folded).not.toHaveAttribute('hidden');
    expect(within(folded as HTMLElement).getByText('рекламодатель:')).toBeVisible();
    expect(within(folded as HTMLElement).getByText(/рекламодатели/)).toBeVisible();
    expect(within(sent).queryByText(/транспорт/)).not.toBeInTheDocument();

    await user.click(more);
    expect(more).toHaveAttribute('aria-expanded', 'false');
    expect(within(sent).getByText('рекламодатель:')).not.toBeVisible();
  });

  it('поиск браузера нашёл текст в свёрнутом — строка раскрывается сама', async () => {
    // Правило 10.11.2026: свёрнутые данные находятся поиском (`hidden="until-found"`);
    // браузер шлёт `beforematch` и снимает `hidden`, экран отвечает «Свернуть».
    openJournal('/journal', ADMIN, LETTERS);

    const sent = await rowOf('письмо №41');
    const more = within(sent).getByRole('button', { name: '+2\u00a0ещё' });
    const folded = document.getElementById(more.getAttribute('aria-controls') ?? '');
    fireEvent(folded as HTMLElement, new Event('beforematch'));

    await waitFor(() => expect(more).toHaveAttribute('aria-expanded', 'true'));
    expect(folded).not.toHaveAttribute('hidden');
    expect(within(sent).getByText('рекламодатель:')).toBeVisible();
  });

  it('не ушло на самом деле — на виду без раскрытия, транспорт — в раскрытии', async () => {
    openJournal('/journal', ADMIN, LETTERS);

    const stub = await rowOf('письмо №40');
    expect(within(stub).getByText('ушло на самом деле:')).toBeVisible();
    expect(within(stub).getByText(/^нет$/)).toBeVisible();
    expect(within(stub).getByText('транспорт:')).not.toBeVisible();
    expect(within(stub).getByRole('button', { name: '+3\u00a0ещё' })).toBeInTheDocument();
  });

  it('почта и домен переносятся по швам, а не по дефису и посреди зоны', async () => {
    openJournal('/journal', ADMIN, LETTERS);

    const sent = await rowOf('письмо №41');
    const author = within(sent).getByText('оператор@site.com');
    expect(author.innerHTML).toContain('оператор@<wbr>site<wbr>.com');
    const to = within(sent).getByText(/editor@ads/);
    expect(to.innerHTML).toContain('editor@<wbr>ads<wbr>.example<wbr>.test');
  });

  it('одну строку сверх итога не прячут: кнопка заняла бы её место', async () => {
    openJournal();

    // Роль и учётка — итог, права — строкой под ним, без «+1 ещё».
    const access = await rowOf('Доступ учётки изменён');
    expect(within(access).getByText(/отправлять письма — выдано/)).toBeInTheDocument();
    expect(within(access).queryByRole('button', { name: /ещё/ })).not.toBeInTheDocument();
  });
});

describe('длинный журнал', () => {
  const LONG: JournalView = { ...PAGE, total: 120 };

  it('шапка закреплена: строки прокручиваются под ней в высоту окна', async () => {
    openJournal('/journal', ADMIN, LONG);

    const table = await screen.findByRole('table', { name: 'Записи журнала' });
    expect(table.querySelector('thead')).toHaveAttribute('data-sticky');
    expect(table.closest('.mantine-TableScrollContainer-scrollContainer')).toHaveClass(
      'stickyTable',
    );
    // Высоту прокрутки держит общее правило закреплённых таблиц (`DataTable sticky`).
    const glass = readFileSync(resolve(process.cwd(), 'src/styles/glass.css'), 'utf8');
    expect(glass).toMatch(
      /\.stickyTable\.mantine-TableScrollContainer-scrollContainer \{\s*max-height:/,
    );
  });

  it('страницы — и над таблицей, и под ней; новая страница — таблица сначала', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/users': { body: USERS },
      'GET /api/journal?page=1': { body: LONG },
      'GET /api/journal?page=2': { body: { ...LONG, page: 2 } },
    });
    renderWith(<AppRoutes />, '/journal');
    const user = userEvent.setup();

    const top = await screen.findByRole('navigation', { name: 'Страницы журнала' });
    expect(screen.getByRole('navigation', { name: 'Страницы журнала внизу' })).toBeInTheDocument();
    const before = screen.getByRole('table', { name: 'Записи журнала' });
    await user.click(within(top).getByRole('button', { name: 'Страница 2' }));

    await waitFor(() =>
      expect(recorded.calls.map((call) => call.path)).toContain('/api/journal?page=2'),
    );
    // Прокрутка таблицы — своя: новая страница открывает её с первой строки.
    await waitFor(() =>
      expect(screen.getByRole('table', { name: 'Записи журнала' })).not.toBe(before),
    );
  });
});

describe('журнал без права «Рекламодатели»', () => {
  it('строка о том, что журнал без них, и их действий нет в фильтре', async () => {
    const permissions = ADMIN.permissions.filter((one) => one !== 'advertisers');
    openJournal('/journal', { ...ADMIN, permissions }, { ...PAGE, advertisers_hidden: true });
    const user = userEvent.setup();

    expect(await screen.findByText(/Действия рекламодателей не показаны/)).toBeInTheDocument();
    await user.click(screen.getByRole('textbox', { name: 'Действие' }));
    expect(await screen.findByRole('option', { name: 'Вход', hidden: true })).toBeInTheDocument();
    expect(
      screen.queryByRole('option', { name: 'Решение по рекламодателю', hidden: true }),
    ).not.toBeInTheDocument();
  });
});

describe('журнал в меню', () => {
  it('у того, кто заводит учётки, — рядом с «Учётками»; у оператора — нет', async () => {
    openJournal('/journal');

    const link = await screen.findByRole('link', { name: 'Журнал действий' });
    const group = link.closest('[role="group"]') as HTMLElement;
    expect(within(group).getByRole('link', { name: 'Учётки' })).toBeInTheDocument();
  });

  it('оператору журнал закрыт: пункта нет, адрес объясняет словами', async () => {
    openJournal('/journal', OPERATOR);

    await screen.findByRole('link', { name: 'Обзор' });
    expect(screen.queryByRole('link', { name: 'Журнал действий' })).not.toBeInTheDocument();
  });
});
