/**
 * Вкладка «emails» раздела «Продажи» (слово владельца 20.10.2026): имя вкладки, колонка «Дата
 * добавления» и период «с … по …» — в адресе днями своих ключей, на сервер моментами по правилу
 * своих дат воронки.
 *
 * Гипотезы — разных ниш, люди и компании — разными письменностями: кириллица, латиница,
 * греческий, японский. Всё выдумано (`*.example.test`).
 */

import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation, useNavigate } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView, KbView, LeadCard, LeadsView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { funnelQuery } from './funnelData';
import { NO_LEAD_FILTERS, queryOf, readLeadFilters, writeLeadFilters } from './leadFilters';
import { addedDayOf, addedProblem, addedQuery, addedWords } from './leadPeriod';
import { TABLE_MIN_WIDTH } from './LeadsTable';

const SCREEN_WAIT = { timeout: 5000 };

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 1,
      name: 'стоматологии · DE',
      description: null,
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 1, ready: 1, rejected: 0 },
      total: 2,
    },
    {
      id: 2,
      name: 'SaaS for logistics · US',
      description: null,
      created_at: '2026-10-02T10:00:00+00:00',
      leads: { new: 1, ready: 0, rejected: 0 },
      total: 1,
    },
    {
      id: 3,
      name: 'οινοποιεία · GR',
      description: null,
      created_at: '2026-10-03T10:00:00+00:00',
      leads: { new: 2, ready: 0, rejected: 0 },
      total: 2,
    },
  ],
  total: 3,
  module_enabled: true,
};

/** Полдень дня по часам браузера: в любом поясе машины тот же день на экране. */
function noon(month: number, day: number): string {
  return new Date(2026, month - 1, day, 12).toISOString();
}

const ANNA: LeadCard = {
  id: 1,
  email: 'anna@zahnarzt.example.test',
  name: 'Анна Смирнова',
  position: 'главный врач',
  company: 'Зубная клиника',
  host: 'zahnarzt.example.test',
  country: 'de',
  timezone: 'Europe/Berlin',
  language: 'de',
  hypothesis_id: 1,
  hypothesis: 'стоматологии · DE',
  source: 'import',
  status: 'new',
  rejection_reason: null,
  cleaning_note: null,
  verification_status: null,
  created_at: noon(10, 3),
};

const JANE: LeadCard = {
  ...ANNA,
  id: 2,
  email: 'ops@route-ai.example.test',
  name: 'Jane Doe',
  position: 'COO',
  company: 'Route AI',
  host: 'route-ai.example.test',
  country: 'us',
  timezone: 'America/Chicago',
  language: 'en',
  hypothesis_id: 2,
  hypothesis: 'SaaS for logistics · US',
  created_at: noon(10, 14),
};

const NIKOS: LeadCard = {
  ...ANNA,
  id: 3,
  email: 'nikos@krasi.example.test',
  name: 'Νίκος Παπαδόπουλος',
  position: null,
  company: 'Κτήμα Άλφα',
  host: 'krasi.example.test',
  country: 'gr',
  timezone: 'Europe/Athens',
  language: 'el',
  hypothesis_id: 3,
  hypothesis: 'οινοποιεία · GR',
  created_at: noon(9, 28),
};

const YAMADA: LeadCard = {
  ...NIKOS,
  id: 4,
  email: 'yamada@sake.example.test',
  name: '山田太郎',
  company: '山田酒造',
  host: 'sake.example.test',
  country: 'jp',
  timezone: 'Asia/Tokyo',
  language: 'ja',
  created_at: noon(10, 15),
};

function view(rows: LeadCard[], extra: Partial<LeadsView> = {}): LeadsView {
  return {
    rows,
    total: rows.length,
    page: 1,
    limit: 20,
    states: { new: 4, ready: 1, rejected: 0 },
    reasons: { duplicate: 0, no_mail: 0 },
    ...extra,
  };
}

const KB: KbView = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief', 'service', 'case', 'objection', 'price_policy', 'forbidden', 'cta'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
  gaps: [],
};

const LEADS = 'GET /api/sales/leads';

/** Период с 1 по 15 октября — моментами, как их шлёт экран: полночи по часам браузера. */
const OCTOBER = {
  added_since: new Date(2026, 9, 1).toISOString(),
  added_until: new Date(2026, 9, 16).toISOString(),
};

function at(query: Record<string, string>): string {
  return `${LEADS}?${new URLSearchParams(query).toString()}`;
}

function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

/** «Назад» браузера: на шаг по истории роутера. */
function Back() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => void navigate(-1)}>
      назад в истории
    </button>
  );
}

function where(): string {
  return screen.getByTestId('where').textContent ?? '';
}

async function openScreen(
  routes: Record<string, Answer> = {},
  { path = '/sales', ready = 'anna@zahnarzt.example.test' } = {},
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    [LEADS]: { body: view([ANNA, JANE, NIKOS, YAMADA]) },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
      <Back />
    </>,
    path,
  );
  await screen.findByText(ready, {}, SCREEN_WAIT);
  return recorded;
}

/** Что спрашивали у списка лидов — строками запроса, по порядку. */
function asked(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => call.method === 'GET' && call.path.startsWith('/api/sales/leads'))
    .map((call: Call) => call.path.replace('/api/sales/leads', '').replace(/^\?/, ''));
}

function rowOf(email: string): HTMLElement {
  const row = screen.getByText(email).closest('tr');
  if (row === null) throw new Error(`строки ${email} нет`);
  return row;
}

function head(): HTMLElement {
  const found = document.querySelector<HTMLElement>('.leadsTable thead');
  if (found === null) throw new Error('шапки таблицы лидов нет');
  return found;
}

/** Окно шириной `width` px — как `windowOf` в `SalesPage.test.tsx`. */
function windowOf(width: number) {
  const fits = (query: string) => {
    const max = /max-width:\s*([\d.]+)em/.exec(query);
    const min = /min-width:\s*([\d.]+)em/.exec(query);
    return (
      (max === null || width <= Number(max[1]) * 16) &&
      (min === null || width >= Number(min[1]) * 16)
    );
  };
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: fits(query),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

function setDay(name: 'Добавлены с' | 'Добавлены по', value: string) {
  fireEvent.change(screen.getByLabelText(name), { target: { value } });
}

describe('продажи: вкладка «emails» (слово владельца 20.10.2026)', () => {
  it('вкладка в ряду — «emails» со счётом; прочие слова «лиды» на экране прежние', async () => {
    // Ряд вкладок — с окна шире 105em (`SalesTabs`: двенадцать вкладок с «Рассылкой»).
    windowOf(1760);
    await openScreen();

    expect(screen.getByRole('radio', { name: 'emails — 5' })).toBeChecked();
    expect(screen.queryByText(/^Лиды —/)).toBeNull();
    // Переименована вкладка, а не слово: колонка лида и плитки говорят по-прежнему.
    expect(screen.getByRole('columnheader', { name: 'Лид' })).toBeInTheDocument();
    expect(screen.getByText('Новые', { selector: '.metricTitle' })).toBeInTheDocument();
  });

  it('на телефоне — тот же пункт в списке «Раздел»', async () => {
    windowOf(390);
    await openScreen();
    const user = userEvent.setup();

    const field = screen.getByRole('textbox', { name: 'Раздел' });
    expect(field).toHaveValue('emails — 5');
    await user.click(field);
    expect(
      await screen.findByRole('option', { name: 'emails — 5', hidden: true }, SCREEN_WAIT),
    ).toBeInTheDocument();
  });
});

describe('продажи: колонка «Дата добавления»', () => {
  it('стоит за гипотезой; в строке — день добавления форматом дат продаж', async () => {
    await openScreen();

    const titles = [...document.querySelectorAll('.leadsTable thead tr:first-child th')].map(
      (cell) => cell.textContent,
    );
    expect(titles).toEqual([
      'Лид',
      'Компания',
      'Гипотеза',
      'Дата добавления',
      'Страна',
      'Состояние',
      'Причина',
    ]);
    for (const [lead, day] of [
      [ANNA, '03.10.2026'],
      [JANE, '14.10.2026'],
      [NIKOS, '28.09.2026'],
      [YAMADA, '15.10.2026'],
    ] as const) {
      const cells = rowOf(lead.email).querySelectorAll('td');
      expect(cells[3]).toHaveTextContent(day);
    }
  });

  it('ширина таблицы — сумма ширин колонок: узкому окну — прокрутка, а не сломанная рама', async () => {
    windowOf(390);
    await openScreen();

    const widths = [...document.querySelectorAll<HTMLElement>('.leadsTable col')].map((col) =>
      Number(col.style.width.replace('rem', '')),
    );
    expect(widths.reduce((sum, rem) => sum + rem * 16, 0)).toBe(TABLE_MIN_WIDTH);
    const scroll = document.querySelector('.leadsTable')?.closest('[style*="--table-min-width"]');
    expect(scroll?.getAttribute('style')).toContain(`${TABLE_MIN_WIDTH / 16}rem`);
    // Поля периода — под своей колонкой, в шапке: уезжают в прокрутку вместе с таблицей.
    expect(within(head()).getByLabelText('Добавлены с')).toBeInTheDocument();
    expect(within(head()).getByLabelText('Добавлены по')).toBeInTheDocument();
  });
});

describe('продажи: период добавления «с … по …»', () => {
  it('из адреса: поля — днями, на сервер — полуночи по часам браузера тем же правилом, что у воронки', async () => {
    const recorded = await openScreen(
      { [at(OCTOBER)]: { body: view([ANNA, JANE, YAMADA]) } },
      { path: '/sales?added_from=2026-10-01&added_to=2026-10-15' },
    );

    expect(asked(recorded)).toEqual([new URLSearchParams(OCTOBER).toString()]);
    expect(screen.getByLabelText('Добавлены с')).toHaveValue('2026-10-01');
    expect(screen.getByLabelText('Добавлены по')).toHaveValue('2026-10-15');
    const funnel = funnelQuery(
      { hypothesis: null, period: 'custom', from: '2026-10-01', to: '2026-10-15' },
      new Date(),
    );
    expect([funnel.since, funnel.until]).toEqual([OCTOBER.added_since, OCTOBER.added_until]);
  });

  it('день в поле — в адрес своими ключами, на первую страницу; прочие фильтры стоят', async () => {
    const since = { added_since: OCTOBER.added_since };
    const recorded = await openScreen(
      {
        [at({ state: 'new', hypothesis: '3', page: '3' })]: {
          body: view([NIKOS], { total: 45, page: 3 }),
        },
        [at({ state: 'new', hypothesis: '3', ...since })]: { body: view([YAMADA]) },
      },
      { path: '/sales?state=new&hypothesis=3&page=3', ready: 'nikos@krasi.example.test' },
    );

    setDay('Добавлены с', '2026-10-01');

    await waitFor(
      () => expect(where()).toBe('/sales?state=new&hypothesis=3&added_from=2026-10-01'),
      SCREEN_WAIT,
    );
    await screen.findByText('yamada@sake.example.test', {}, SCREEN_WAIT);
    expect(asked(recorded).at(-1)).toBe(
      new URLSearchParams({ state: 'new', hypothesis: '3', ...since }).toString(),
    );
    expect(screen.getByRole('textbox', { name: 'Состояние' })).toHaveValue('новый');
  });

  it('период переживает «назад»: страница — новая запись истории, период — в адресе', async () => {
    const recorded = await openScreen(
      {
        [at(OCTOBER)]: { body: view([ANNA, JANE], { total: 45 }) },
        [at({ ...OCTOBER, page: '2' })]: { body: view([YAMADA], { total: 45, page: 2 }) },
      },
      { path: '/sales?added_from=2026-10-01&added_to=2026-10-15' },
    );
    const user = userEvent.setup();

    const pages = screen.getByRole('navigation', { name: 'Страницы лидов' });
    await user.click(within(pages).getByRole('button', { name: 'Страница 2' }));
    await screen.findByText('yamada@sake.example.test', {}, SCREEN_WAIT);
    expect(where()).toBe('/sales?added_from=2026-10-01&added_to=2026-10-15&page=2');

    await user.click(screen.getByRole('button', { name: 'назад в истории' }));

    await screen.findByText('anna@zahnarzt.example.test', {}, SCREEN_WAIT);
    expect(where()).toBe('/sales?added_from=2026-10-01&added_to=2026-10-15');
    expect(screen.getByLabelText('Добавлены с')).toHaveValue('2026-10-01');
    expect(asked(recorded).at(-1)).toBe(new URLSearchParams(OCTOBER).toString());
  });

  it('незнакомая дата в адресе не сужает — ни в поле, ни на сервере', async () => {
    const recorded = await openScreen({}, { path: '/sales?added_from=2026-02-30&added_to=1e3' });

    expect(asked(recorded)).toEqual(['']);
    expect(screen.getByLabelText('Добавлены с')).toHaveValue('');
    expect(screen.getByLabelText('Добавлены по')).toHaveValue('');
  });

  it('«с» позже «по» — словами под полями, запроса нет; поправили дату — запрос ушёл', async () => {
    const fixed = {
      added_since: new Date(2026, 9, 15).toISOString(),
      added_until: new Date(2026, 9, 21).toISOString(),
    };
    const recorded = await openScreen(
      { [at(fixed)]: { body: view([YAMADA]) } },
      { path: '/sales?added_from=2026-10-15&added_to=2026-10-01', ready: 'Период задан наоборот.' },
    );

    expect(asked(recorded)).toEqual([]);
    // Слова — под полями, в шапке; объяснение вместо строк — под таблицей, не в ней.
    expect(within(head()).getByText('«С» позже «по» — поменяйте даты местами')).toBeInTheDocument();
    expect(screen.getByText('Период задан наоборот.').closest('table')).toBeNull();
    expect(screen.getByLabelText('Добавлены по')).toHaveAttribute('aria-invalid', 'true');

    setDay('Добавлены по', '2026-10-20');

    await screen.findByText('yamada@sake.example.test', {}, SCREEN_WAIT);
    expect(asked(recorded)).toEqual([new URLSearchParams(fixed).toString()]);
    expect(screen.queryByText('«С» позже «по» — поменяйте даты местами')).toBeNull();
  });

  it('под периодом пусто — условие словами в пустой таблице; «Сбросить фильтры» снимает и его', async () => {
    const empty = {
      added_since: new Date(2026, 10, 1).toISOString(),
      added_until: new Date(2026, 10, 3).toISOString(),
    };
    await openScreen(
      { [at(empty)]: { body: view([]) } },
      {
        path: '/sales?added_from=2026-11-01&added_to=2026-11-02',
        ready: 'Под фильтр ничего не попало.',
      },
    );
    const user = userEvent.setup();

    expect(
      screen.getByText('Условия: добавлены с 01.11.2026 по 02.11.2026. Всего лидов — 5.'),
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));

    await waitFor(() => expect(where()).toBe('/sales'), SCREEN_WAIT);
    expect(screen.getByLabelText('Добавлены с')).toHaveValue('');
  });

  it('смена вкладки период не переносит, и свои даты воронки — не период лидов', async () => {
    const recorded = await openScreen(
      {
        [at(OCTOBER)]: { body: view([ANNA]) },
        'GET /api/sales/funnel': { status: 503, body: { detail: 'выдуманный отказ теста' } },
      },
      { path: '/sales?added_from=2026-10-01&added_to=2026-10-15' },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Воронка' }));
    await screen.findByText('Воронка не загрузилась', {}, SCREEN_WAIT);
    expect(where()).toBe('/sales?tab=funnel');

    await user.click(screen.getByRole('radio', { name: 'emails — 5' }));
    await screen.findByText('yamada@sake.example.test', {}, SCREEN_WAIT);
    expect(where()).toBe('/sales');
    expect(asked(recorded)).toEqual([new URLSearchParams(OCTOBER).toString(), '']);

    // Свои даты воронки живут под её ключами: вкладка лидов их не читает.
    const funnel = readLeadFilters(
      new URLSearchParams('tab=funnel&period=custom&from=2026-10-01&to=2026-10-05'),
    );
    expect([funnel.addedFrom, funnel.addedTo]).toEqual(['', '']);
    expect(queryOf({ ...funnel, tab: 'leads' })).toEqual({});
  });

  it('поиск любой письменностью и причина сочетаются с периодом — в адресе и на сервере', () => {
    for (const search of ['Анна', 'Νίκος', '山田酒造', 'Route AI']) {
      const address = new URLSearchParams({
        state: 'rejected',
        reason: 'duplicate',
        hypothesis: '2',
        search,
        added_from: '2026-10-01',
        added_to: '2026-10-15',
      }).toString();
      const filters = readLeadFilters(new URLSearchParams(address));
      expect(writeLeadFilters(filters).toString()).toBe(address);
      expect(queryOf(filters)).toEqual({
        state: 'rejected',
        reason: 'duplicate',
        hypothesis: 2,
        search,
        ...OCTOBER,
      });
    }
  });
});

describe('leadPeriod: дни, сутки и слова', () => {
  it('настоящий день — как есть; не день — пусто', () => {
    for (const day of ['2026-10-01', '2028-02-29', '1999-12-31']) expect(addedDayOf(day)).toBe(day);
    for (const junk of [
      '2026-02-30',
      '2026-13-01',
      '2027-02-29',
      '1e3',
      '2026-1-01',
      '0002-10-01',
      '',
      null,
    ]) {
      expect(addedDayOf(junk)).toBe('');
    }
  });

  it('границы — полночи по часам браузера, как у своих дат воронки; одна граница — одна', () => {
    // Сутки перевода часов в Европе и стык лет: полночь — по часам браузера, а не плюс 24 часа.
    const days: [string, string][] = [
      ['2026-03-28', '2026-03-29'],
      ['2026-10-24', '2026-10-25'],
      ['2026-12-31', '2027-01-01'],
    ];
    for (const [from, to] of days) {
      const funnel = funnelQuery({ hypothesis: null, period: 'custom', from, to }, new Date());
      expect(addedQuery(from, to)).toEqual({
        added_since: funnel.since,
        added_until: funnel.until,
      });
    }
    expect(addedQuery('2026-10-01', '')).toEqual({ added_since: OCTOBER.added_since });
    expect(addedQuery('', '2026-10-15')).toEqual({ added_until: OCTOBER.added_until });
    expect(addedQuery('', '')).toEqual({});
  });

  it('«с» позже «по» — отказ словами; тот же день и одна граница — годятся', () => {
    expect(addedProblem('2026-10-15', '2026-10-01')).toBe(
      '«С» позже «по» — поменяйте даты местами',
    );
    expect(addedProblem('2026-10-15', '2026-10-15')).toBeNull();
    expect(addedProblem('2026-10-15', '')).toBeNull();
    expect(addedProblem('', '2026-10-01')).toBeNull();
  });

  it('период словами — днями экрана', () => {
    expect(addedWords('2026-10-01', '2026-10-15')).toBe('добавлены с 01.10.2026 по 15.10.2026');
    expect(addedWords('2026-10-01', '')).toBe('добавлены с 01.10.2026');
    expect(addedWords('', '2026-10-15')).toBe('добавлены по 15.10.2026');
    expect(addedWords('', '')).toBeNull();
    expect(writeLeadFilters({ ...NO_LEAD_FILTERS, addedTo: '2026-10-15' }).toString()).toBe(
      'added_to=2026-10-15',
    );
  });
});
