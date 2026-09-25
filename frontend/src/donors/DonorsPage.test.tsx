/**
 * Таблица доноров: вердикт, причина отсева, фильтры в шапке и страницы.
 *
 * Отдельно проверяется, что «не проверен» и «не подходит» — разные
 * состояния: спутать их значит копить ложные отказы. И что фильтр и
 * страница уходят на сервер параметрами, а не фильтруют уже пришедшее:
 * страниц у базы много, и фильтр по одной из них врал бы числами.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { REVOKE_AFTER_MS } from '../api/donors';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

const ROWS = [
  {
    id: 1,
    host: 'good.example.test',
    status: 'suitable',
    reject_reason: null,
    dr: 45,
    org_traffic: 1_600_000_000,
    geo: 'us',
    geo_top_share: 0.62,
    contacts: 2,
    contact_status: 'found',
    last_price: null,
    last_price_currency: null,
    metrics_refreshed_at: '2026-09-18T10:00:00+00:00',
    fresh: true,
  },
  {
    id: 2,
    host: 'weak.example.test',
    status: 'unsuitable',
    reject_reason: 'dr ниже порога',
    dr: 8,
    org_traffic: 300,
    geo: 'de',
    geo_top_share: 0.4,
    contacts: 0,
    contact_status: null,
    last_price: null,
    last_price_currency: null,
    metrics_refreshed_at: '2026-06-01T10:00:00+00:00',
    fresh: false,
  },
  {
    id: 3,
    host: 'blank.example.test',
    status: 'unchecked',
    reject_reason: null,
    dr: null,
    org_traffic: null,
    geo: null,
    geo_top_share: null,
    contacts: 0,
    contact_status: null,
    last_price: null,
    last_price_currency: null,
    metrics_refreshed_at: null,
    fresh: false,
  },
];

const COUNTS = { suitable: 1, unsuitable: 1, unchecked: 3 };
const PAGE = { rows: ROWS, total: 3, counts: COUNTS };
const NOTHING = { rows: [], total: 0, counts: COUNTS };
/** База на три страницы: сорок пять доноров, и сводка сходится с ними. */
const BIG = { suitable: 40, unsuitable: 2, unchecked: 3 };

const CONTACTS = { pending: 3, running: false, job_id: null, last: null, workers: 1 };

function donorsAt(query: string): string {
  return `GET /api/donors?${query}`;
}

async function openDonors(
  routes: Record<string, Answer> = {},
  { path = '/donors', ready = 'good.example.test' }: { path?: string; ready?: string } = {},
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/contacts': { body: CONTACTS },
    [donorsAt('limit=20&offset=0')]: { body: PAGE },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(ready);
  return recorded;
}

/** Текст файла. Здесь `Blob` от jsdom, и `text()` у него нет, а читатель есть. */
function textOf(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      typeof reader.result === 'string'
        ? resolve(reader.result)
        : reject(new Error('файл прочитан не текстом'));
    reader.onerror = () => reject(reader.error ?? new Error('файл не прочитан'));
    reader.readAsText(blob);
  });
}

function listCalls(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => call.method === 'GET' && call.path.startsWith('/api/donors?'))
    .map((call: Call) => call.path);
}

async function choose(field: string, option: string) {
  const user = userEvent.setup();
  // Выпадающий список Mantine в jsdom остаётся `display: none` — раскладки
  // здесь нет, и без `hidden` его пункты не видны запросу. Клик по пункту
  // при этом настоящий, и проверяется именно он.
  await user.click(screen.getByRole('textbox', { name: field }));
  await user.click(await screen.findByRole('option', { name: option, hidden: true }));
}

describe('доноры: таблица', () => {
  it('показывает вердикт и причину отсева', async () => {
    await openDonors();

    const row = screen.getByText('weak.example.test').closest('tr')!;
    expect(within(row).getByText('не подходит')).toBeInTheDocument();
    expect(within(row).getByText('dr ниже порога')).toBeInTheDocument();
  });

  it('свежесть данных видна в таблице: за свежие второй раз не платят', async () => {
    await openDonors();

    const good = screen.getByText('good.example.test').closest('tr')!;
    const weak = screen.getByText('weak.example.test').closest('tr')!;
    const blank = screen.getByText('blank.example.test').closest('tr')!;
    expect(within(good).getByText('в сроке')).toBeInTheDocument();
    expect(within(weak).getByText('пора обновить')).toBeInTheDocument();
    // Данных не было вовсе — обновлять нечего, слово то же, что в карточке.
    expect(within(blank).getByText('не проверялись')).toBeInTheDocument();
  });

  it('числа и страны — общими форматами, адрес — числом и исходом поиска', async () => {
    await openDonors();

    const good = screen.getByText('good.example.test').closest('tr')!;
    expect(within(good).getByText('1,6 млрд')).toBeInTheDocument();
    expect(within(good).getByText('США · 62%')).toBeInTheDocument();
    expect(within(good).getByText('адрес найден')).toBeInTheDocument();
    // «Не искали» — не «адреса нет»: первое ждёт поиска, второе — срока.
    const weak = screen.getByText('weak.example.test').closest('tr')!;
    expect(within(weak).getByText('не искали')).toBeInTheDocument();
  });

  it('по двадцать на страницу, и страница уходит на сервер', async () => {
    const recorded = await openDonors({
      [donorsAt('limit=20&offset=0')]: { body: { ...PAGE, total: 45, counts: BIG } },
      [donorsAt('limit=20&offset=20')]: {
        body: {
          rows: [{ ...ROWS[0], id: 21, host: 'second.example.test' }],
          total: 45,
          counts: BIG,
        },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Страница 2' }));

    await screen.findByText('second.example.test');
    expect(listCalls(recorded)).toEqual([
      '/api/donors?limit=20&offset=0',
      '/api/donors?limit=20&offset=20',
    ]);
  });

  it('страница и фильтры берутся из адреса: «назад» и обновление их не теряют', async () => {
    const recorded = await openDonors(
      {
        [donorsAt('status=suitable&min_dr=30&has_contact=false&limit=20&offset=20')]: {
          body: { ...PAGE, total: 25, counts: BIG },
        },
      },
      { path: '/donors?status=suitable&min_dr=30&has_contact=false&page=2' },
    );

    expect(listCalls(recorded)).toEqual([
      '/api/donors?status=suitable&min_dr=30&has_contact=false&limit=20&offset=20',
    ]);
    expect(screen.getByRole('textbox', { name: 'Вердикт' })).toHaveValue('подходит · 40');
    expect(screen.getByRole('textbox', { name: 'DR не ниже' })).toHaveValue('30');
    expect(screen.getByRole('textbox', { name: 'Адреса' })).toHaveValue('без адреса');
    expect(screen.getByText('найдено 25 из 45')).toBeInTheDocument();
  });

  it('смена фильтра возвращает на первую страницу', async () => {
    const recorded = await openDonors(
      {
        [donorsAt('limit=20&offset=20')]: { body: { ...PAGE, total: 45, counts: BIG } },
        [donorsAt('status=unchecked&limit=20&offset=0')]: { body: { ...PAGE, counts: BIG } },
      },
      { path: '/donors?page=2' },
    );

    await choose('Вердикт', 'не проверен · 3');

    await waitFor(() =>
      expect(listCalls(recorded).at(-1)).toBe('/api/donors?status=unchecked&limit=20&offset=0'),
    );
  });

  it('«не проверен» — отдельным числом в фильтре вердикта, а не вместе с «не подходит»', async () => {
    await openDonors();
    const user = userEvent.setup();

    await user.click(screen.getByRole('textbox', { name: 'Вердикт' }));

    expect(
      await screen.findByRole('option', { name: 'не проверен · 3', hidden: true }),
    ).toBeTruthy();
    expect(screen.getByRole('option', { name: 'не подходит · 1', hidden: true })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'все · 5', hidden: true })).toBeTruthy();
  });

  it('пояснение «не проверен ≠ не подходит» — подсказкой у фильтра вердикта', async () => {
    await openDonors();
    const user = userEvent.setup();

    await user.hover(screen.getByRole('button', { name: 'Что значит «не проверен»' }));

    expect(await screen.findByText(/у домена не было данных/)).toBeInTheDocument();
  });

  it('поиск уходит на сервер после паузы в наборе, а не по букве', async () => {
    const recorded = await openDonors({
      [donorsAt('search=weak&limit=20&offset=0')]: { body: { ...PAGE, rows: [ROWS[1]], total: 1 } },
    });
    const user = userEvent.setup();

    await user.type(
      screen.getByRole('textbox', { name: 'Поиск по домену или причине отсева' }),
      'weak',
    );

    await waitFor(() =>
      expect(listCalls(recorded).at(-1)).toBe('/api/donors?search=weak&limit=20&offset=0'),
    );
    expect(listCalls(recorded).filter((path) => path.includes('search='))).toEqual([
      '/api/donors?search=weak&limit=20&offset=0',
    ]);
  });

  it('порог DR и «без адреса» уходят параметрами', async () => {
    const recorded = await openDonors({
      [donorsAt('min_dr=30&limit=20&offset=0')]: { body: PAGE },
      [donorsAt('min_dr=30&has_contact=false&limit=20&offset=0')]: { body: PAGE },
    });
    const user = userEvent.setup();

    await user.type(screen.getByRole('textbox', { name: 'DR не ниже' }), '30');
    await waitFor(() =>
      expect(listCalls(recorded).at(-1)).toBe('/api/donors?min_dr=30&limit=20&offset=0'),
    );
    // «3» на пути к «30» — не фильтр: запроса с ним не было.
    expect(listCalls(recorded).filter((path) => path.includes('min_dr=3&'))).toEqual([]);
    await choose('Адреса', 'без адреса');

    await waitFor(() =>
      expect(listCalls(recorded).at(-1)).toBe(
        '/api/donors?min_dr=30&has_contact=false&limit=20&offset=0',
      ),
    );
  });

  it('пустой результат называет условия и даёт их сбросить', async () => {
    const recorded = await openDonors(
      { [donorsAt('status=unchecked&min_dr=30&limit=20&offset=0')]: { body: NOTHING } },
      { path: '/donors?status=unchecked&min_dr=30', ready: 'Под фильтр ничего не попало.' },
    );
    const user = userEvent.setup();

    expect(
      screen.getByText('Условия: вердикт «не проверен», DR не ниже 30. Всего доноров в базе — 5.'),
    ).toBeInTheDocument();
    // Фильтры остаются на месте: условие поправляют тут же, не возвращаясь.
    expect(screen.getByRole('textbox', { name: 'DR не ниже' })).toHaveValue('30');

    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));

    await screen.findByText('good.example.test');
    expect(listCalls(recorded).at(-1)).toBe('/api/donors?limit=20&offset=0');
  });

  it('вердикта нет во всей базе — так и сказано, а не «сузьте условия»', async () => {
    await openDonors(
      {
        [donorsAt('status=unchecked&limit=20&offset=0')]: {
          body: { ...NOTHING, counts: { suitable: 4, unsuitable: 1 } },
        },
      },
      { path: '/donors?status=unchecked', ready: 'С вердиктом «не проверен» доноров нет.' },
    );

    expect(screen.getByText(/Нет во всей базе/)).toBeInTheDocument();
  });

  it('строка открывает карточку донора', async () => {
    await openDonors({
      'GET /api/donors/2': {
        body: {
          ...ROWS[1],
          contacts: [],
          geo_breakdown: null,
          geo_partial: false,
          metrics: null,
          expires_at: null,
          contact_attempted_at: null,
          last_price_at: null,
          contact_refusal: 'Адрес ищут только подходящим донорам — этот не прошёл пороги.',
        },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByText('dr ниже порога'));

    expect(await screen.findByRole('heading', { name: 'weak.example.test' })).toBeInTheDocument();
  });
});

describe('ждут адреса', () => {
  it('общий поиск — строкой в шапке таблицы, и уходит на сервер', async () => {
    const recorded = await openDonors({
      'POST /api/contacts': { body: { job_id: 'job-1', pending: 3 } },
      'GET /api/jobs/job-1': {
        body: {
          job_id: 'job-1',
          kind: 'поиск контактов',
          state: 'queued',
          title: 'в очереди',
          error: null,
          report: null,
          retries_left: 3,
          next_try_at: null,
          ended_at: null,
        },
      },
    });
    const user = userEvent.setup();

    expect(screen.getByText(/ждут адреса:/)).toHaveTextContent('ждут адреса: 3');
    await user.click(screen.getByRole('button', { name: 'найти адреса всем ждущим' }));

    await screen.findByText(/Поиск поставлен в очередь/);
    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent[0]?.path).toBe('/api/contacts');
    expect(await screen.findByRole('status')).toHaveTextContent('Поиск контактов: в очереди');
  });

  it('ждущих нет — строки нет, а не «ждут адреса: 0»', async () => {
    await openDonors({ 'GET /api/contacts': { body: { ...CONTACTS, pending: 0 } } });

    // Ответ о контактах уже пришёл: иначе отсутствие строки ничего не доказывает.
    await waitFor(() => expect(screen.getByText('всего 5')).toBeInTheDocument());
    expect(screen.queryByText(/ждут адреса/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'найти адреса всем ждущим' })).toBeNull();
  });

  it('без права на запуск число видно, а кнопки нет', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: { ...OPERATOR, permissions: ['view'] } },
      'GET /api/contacts': { body: CONTACTS },
      [donorsAt('limit=20&offset=0')]: { body: PAGE },
    });
    renderWith(<AppRoutes />, '/donors');

    expect(await screen.findByText(/ждут адреса:/)).toHaveTextContent('ждут адреса: 3');
    expect(screen.queryByRole('button', { name: 'найти адреса всем ждущим' })).toBeNull();
  });

  it('упавший поиск виден с причиной, а не тишиной', async () => {
    await openDonors({
      'GET /api/contacts': {
        body: {
          ...CONTACTS,
          job_id: 'job-9',
          job: {
            job_id: 'job-9',
            kind: 'поиск контактов',
            state: 'failed',
            title: 'упала',
            error: 'ConnectError: сеть',
            report: null,
            retries_left: 0,
            next_try_at: null,
            ended_at: '2026-09-24T12:00:00Z',
          },
        },
      },
    });

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Поиск контактов: упала — ConnectError: сеть',
    );
  });
});

describe('выгрузка', () => {
  const created: Blob[] = [];
  const clicked: HTMLAnchorElement[] = [];
  const revoked = vi.fn();

  beforeEach(() => {
    created.length = 0;
    clicked.length = 0;
    // В jsdom ссылок на объекты нет вовсе: подставляем их, запоминая файл.
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn((blob: Blob) => {
        created.push(blob);
        return 'blob:donors';
      }),
    });
    revoked.mockReset();
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: revoked,
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

  it('файл скачивается запросом с пропуском и теми же фильтрами, что на экране', async () => {
    const recorded = await openDonors(
      {
        [donorsAt('status=suitable&limit=20&offset=0')]: { body: PAGE },
        'GET /api/donors/export?status=suitable': {
          raw: 'host,status\r\ngood.example.test,suitable\r\n',
          headers: {
            'content-type': 'text/csv; charset=utf-8',
            'content-disposition': 'attachment; filename="donors-2026-09-25.csv"',
          },
        },
      },
      { path: '/donors?status=suitable' },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Выгрузить' }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    const call = recorded.calls.find((sent) => sent.path.startsWith('/api/donors/export'));
    // Раньше это была ссылка без пропуска, и сервер отвечал ей 401.
    expect(call?.token).toBe('Bearer пропуск');
    expect(call?.path).toBe('/api/donors/export?status=suitable');
    expect(clicked[0]?.download).toBe('donors-2026-09-25.csv');
    expect(clicked[0]?.getAttribute('href')).toBe('blob:donors');
    expect(await textOf(created[0]!)).toBe('host,status\r\ngood.example.test,suitable\r\n');
    // Ссылку на файл убирают — не сразу, но убирают.
    await waitFor(() => expect(revoked).toHaveBeenCalledWith('blob:donors'), {
      timeout: REVOKE_AFTER_MS + 1000,
    });
  });

  it('отказ сервера — уведомлением с его текстом, а не молчанием', async () => {
    await openDonors({
      'GET /api/donors/export': {
        status: 500,
        body: { detail: 'Выгрузка не собралась: база не ответила — повторите через минуту' },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Выгрузить' }));

    expect(
      await screen.findByText('Выгрузка не собралась: база не ответила — повторите через минуту'),
    ).toBeInTheDocument();
    expect(clicked).toHaveLength(0);
  });
});
