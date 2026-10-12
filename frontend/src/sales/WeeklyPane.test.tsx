/**
 * Вкладка «Отчёт»: недельный отчёт продаж — разделы словами сервера, числа по гипотезам,
 * неделя в адресе, отправка в группу строкой, выгрузка CSV. Ответы сервера — записанные
 * (`serve()`), незаписанный запрос роняет тест; имена и даты выдуманы.
 *
 * Проверяется то, ради чего вкладка: экран не считает сам — показывает строки и числа ответа;
 * неделя уходит на сервер понедельником и живёт в адресе; не доставленный в группу отчёт
 * назван словами — с причиной и сроком повтора — и всё равно стоит на экране; файл уходит
 * с пропуском; отказ сервера — словами.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView } from '../api/salesTypes';
import type { SalesWeeklyView, WeeklyDelivery, WeeklyNumbers } from '../api/salesWeekly';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { deliveryWords, weekWords } from './weeklyData';

const SCREEN_WAIT = { timeout: 5000 };
const WEEKLY = '/api/sales/weekly';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 5,
      name: 'тестовая гипотеза',
      description: null,
      created_at: '2027-02-01T10:00:00+00:00',
      leads: { new: 0, ready: 3, rejected: 0 },
      total: 3,
    },
  ],
  total: 1,
  module_enabled: true,
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

function numbers(values: Partial<WeeklyNumbers>): WeeklyNumbers {
  return {
    loaded: 0,
    ready: 0,
    rejected: 0,
    cleaning: 0,
    queued: 0,
    sent: 0,
    delivered: 0,
    bounced: 0,
    answered: 0,
    handed_off: 0,
    letters: [0, 0, 0],
    answers: 0,
    replies: { auto_reply: 0 },
    active: 0,
    silent: 0,
    waiting: 0,
    handed: 0,
    unwritten: 0,
    letters_queued: 0,
    ...values,
  };
}

const FIRST = numbers({
  loaded: 41,
  ready: 37,
  sent: 23,
  delivered: 21,
  bounced: 2,
  answers: 3,
  replies: { auto_reply: 4 },
  active: 2,
  silent: 1,
  handed: 1,
  letters_queued: 9,
});

const SENT: WeeklyDelivery = {
  status: 'sent',
  sent_at: '2027-03-08T06:01:00Z',
  due_at: null,
  error: null,
  schedule: 'в понедельник в 09:00 по Москве, за прошлую неделю',
};

function view(week: string, delivery: WeeklyDelivery = SENT): SalesWeeklyView {
  return {
    week,
    last_day: week === '2027-03-01' ? '2027-03-07' : '2027-02-28',
    weeks: ['2027-03-01', '2027-02-22'],
    built_at: '2027-03-08T06:13:00Z',
    sections: [
      { key: 'contacts', title: 'Контакты', lines: ['загружено за неделю — 41: готово — 37'] },
      { key: 'replies', title: 'Ответы', lines: ['ответов за неделю — 3', 'автоответов — 4'] },
      {
        key: 'leads',
        title: 'Лиды поимённо',
        lines: [
          'передано телемаркетологу за неделю — 1',
          'Warm Ltd (warm.example.test) · jane@warm.example.test — https://crm.example.test/1',
        ],
      },
    ],
    rows: [{ hypothesis_id: 5, name: 'тестовая гипотеза', numbers: FIRST }],
    total: FIRST,
    delivery,
  };
}

type Routes = Record<string, Answer | ((call: Call) => Answer)>;

/** Где сейчас экран: адрес целиком — неделя живёт в адресе раздела. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

async function openReport(routes: Routes = {}, path = '/sales?tab=report') {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    [`GET ${WEEKLY}`]: { body: view('2027-03-01') },
    ...routes,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
  await screen.findByText('Загружено', { selector: '.metricTitle' }, SCREEN_WAIT);
  return recorded;
}

function calls(recorded: Recorded, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === 'GET' && call.path === path);
}

/** Плитка: подпись, число и пояснение — тексты её трёх строк. */
function tile(title: string): string[] {
  const label = screen.getByText(title, { selector: '.metricTitle' });
  const card = label.closest('.metricTile');
  if (!(card instanceof HTMLElement)) throw new Error(`плитки «${title}» нет`);
  return Array.from(card.children).map((child) => child.textContent ?? '');
}

describe('недельный отчёт продаж', () => {
  it('разделы — строками сервера по порядку, первая строка — итог, дальше — список', async () => {
    await openReport();

    const sections = Array.from(document.querySelectorAll('.weeklySection'));
    expect(sections.map((section) => section.querySelector('p')?.textContent)).toEqual([
      '1. Контакты',
      '2. Ответы',
      '3. Лиды поимённо',
    ]);
    const leads = sections[2] as HTMLElement;
    expect(within(leads).getByText('передано телемаркетологу за неделю — 1')).toBeInTheDocument();
    expect(
      within(leads)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([
      'Warm Ltd (warm.example.test) · jane@warm.example.test — https://crm.example.test/1',
    ]);
  });

  it('плитки и таблица — числа сервера как есть', async () => {
    await openReport();

    expect(tile('Загружено')).toEqual(['Загружено', '41', 'готово к письмам — 37']);
    expect(tile('Первое письмо')).toEqual(['Первое письмо', '23', 'лидов, не писем']);
    expect(tile('Доставлено')).toEqual(['Доставлено', '21', 'отказ — 2']);
    expect(tile('Ответов')).toEqual(['Ответов', '3', 'автоответов — 4']);
    expect(tile('В диалоге')).toEqual(['В диалоге', '3', 'молчащих — 1']);
    expect(tile('Передано')).toEqual(['Передано', '1', 'телемаркетологу']);
    const table = screen.getByRole('table', { name: 'Недельный отчёт по гипотезам' });
    const rows = within(table).getAllByRole('row');
    expect(
      rows.slice(1).map((row) =>
        within(row)
          .getAllByRole('cell')
          .map((cell) => cell.textContent),
      ),
    ).toEqual([
      ['тестовая гипотеза', '41', '23', '21', '3', '3', '1', '9'],
      ['Всего', '41', '23', '21', '3', '3', '1', '9'],
    ]);
  });

  it('неделя — понедельником в запросе и в адресе раздела', async () => {
    const asked = `${WEEKLY}?week=2027-02-22`;
    const recorded = await openReport({ [`GET ${asked}`]: { body: view('2027-02-22') } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('textbox', { name: 'Неделя' }));
    await user.click(
      await screen.findByRole('option', { name: '22.02–28.02.2027', hidden: true }, SCREEN_WAIT),
    );

    await waitFor(() => expect(calls(recorded, asked)).toHaveLength(1));
    expect(screen.getByTestId('where')).toHaveTextContent('/sales?tab=report&week=2027-02-22');
  });

  it('неделя из адреса уходит на сервер сразу', async () => {
    const asked = `${WEEKLY}?week=2027-02-22`;
    const recorded = await openReport(
      { [`GET ${asked}`]: { body: view('2027-02-22') } },
      '/sales?tab=report&week=2027-02-22',
    );

    expect(calls(recorded, asked)).toHaveLength(1);
    expect(screen.getByRole('textbox', { name: 'Неделя' })).toHaveValue('22.02–28.02.2027');
  });

  it('неделя старше списка выбора — в поле, а не пустота', async () => {
    const asked = `${WEEKLY}?week=2027-01-11`;
    await openReport(
      { [`GET ${asked}`]: { body: view('2027-01-11') } },
      '/sales?tab=report&week=2027-01-11',
    );

    expect(screen.getByRole('textbox', { name: 'Неделя' })).toHaveValue('11.01–17.01.2027');
  });

  it('отчёт в группу не уходит (так по умолчанию) — одна строка, сам отчёт на месте', async () => {
    const off: WeeklyDelivery = { ...SENT, status: 'off', sent_at: null, schedule: null };
    await openReport({ [`GET ${WEEKLY}`]: { body: view('2027-03-01', off) } });

    const line = document.querySelector('.weeklyDelivery');
    expect(line).toHaveTextContent(
      'Отчёт в группу продаж не уходит — отправку раз в неделю включает администратор.',
    );
    expect(line?.closest('[role="alert"]')).toBeNull();
    expect(tile('Загружено')[1]).toBe('41');
    expect(document.querySelectorAll('.weeklySection')).toHaveLength(3);
  });

  it('не доставлен в группу — плашкой с причиной и сроком повтора, отчёт на месте', async () => {
    // A3: Telegram недоступен — отчёт доступен на экране
    const retrying: WeeklyDelivery = {
      ...SENT,
      status: 'undelivered',
      sent_at: null,
      due_at: '2027-03-08T06:20:00Z',
      error: 'Telegram отказал (HTTP 503: Unavailable) — повтор позже',
    };
    await openReport({ [`GET ${WEEKLY}`]: { body: view('2027-03-01', retrying) } });

    const line = document.querySelector('.weeklyDelivery');
    expect(line).toHaveTextContent(
      /^Не доставлен в группу продаж: Telegram отказал \(HTTP 503: Unavailable\) — повтор позже\. Повтор — не раньше /,
    );
    expect(line?.closest('[role="alert"]') ?? line).toBeInTheDocument();
    expect(tile('Загружено')[1]).toBe('41');
  });

  it('отказ сервера — словами', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: HYPOTHESES },
      'GET /api/sales/kb': { body: KB },
      [`GET ${WEEKLY}`]: {
        status: 422,
        body: { detail: 'неделя отчёта начинается с понедельника — выберите понедельник' },
      },
    });
    renderWith(<AppRoutes />, '/sales?tab=report');

    const alert = (await screen.findByText('Отчёт не загрузился', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    expect(alert).toHaveTextContent('неделя отчёта начинается с понедельника');
  });
});

describe('выгрузка отчёта', () => {
  const clicked: HTMLAnchorElement[] = [];

  beforeEach(() => {
    clicked.length = 0;
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:weekly'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this);
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(URL, 'createObjectURL');
    Reflect.deleteProperty(URL, 'revokeObjectURL');
  });

  it('файл недели уходит с пропуском и именем сервера', async () => {
    const file = `${WEEKLY}.csv?week=2027-03-01`;
    const recorded = await openReport({
      [`GET ${file}`]: {
        raw: 'раздел;гипотеза;показатель;значение\r\n',
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'content-disposition': 'attachment; filename="sales-week-2027-03-01.csv"',
        },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Выгрузить CSV' }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(calls(recorded, file)[0]?.token).toBe('пропуск');
    expect(clicked[0]?.download).toBe('sales-week-2027-03-01.csv');
    expect(await screen.findByText('Отчёт за 01.03–07.03.2027 выгружен')).toBeInTheDocument();
  });
});

describe('слова вкладки', () => {
  it('неделя — по её понедельнику, без часов браузера', () => {
    expect(weekWords('2027-03-01')).toBe('01.03–07.03.2027');
    expect(weekWords('2026-12-28')).toBe('28.12–03.01.2027');
  });

  it('отправка в группу — словами каждого исхода', () => {
    expect(deliveryWords({ ...SENT, status: 'pending', sent_at: null }).text).toBe(
      'Отчёт отправляется в группу продаж.',
    );
    expect(
      deliveryWords({
        ...SENT,
        status: 'undelivered',
        sent_at: null,
        error: 'чат группы не задан',
      }),
    ).toEqual({
      text: 'Не доставлен в группу продаж: чат группы не задан. Повторов больше нет — отчёт здесь и в CSV.',
      color: 'red',
    });
    expect(deliveryWords({ ...SENT, status: 'none', sent_at: null }).text).toBe(
      'В группу продаж не отправлялся. Отчёт уходит в понедельник в 09:00 по Москве, за прошлую неделю.',
    );
    const off = deliveryWords({ ...SENT, status: 'off', sent_at: null, schedule: null });
    expect(off.text).toBe(
      'Отчёт в группу продаж не уходит — отправку раз в неделю включает администратор.',
    );
    // Имени настройки на экране нет — как у плашки «Модуль продаж выключен».
    expect(off.text).not.toMatch(/SALES_|WEEKLY/);
  });
});
