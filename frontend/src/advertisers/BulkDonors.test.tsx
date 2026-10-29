/**
 * Доноры списком на панели «Обход доноров» — «Загрузить списком» и очередь загрузки обхода.
 *
 * Проверяется то, ради чего окно: файл уходит на сервер сначала на предпросмотр, и до записи
 * видно, сколько заведётся, у кого обновится цена и кого пропустят — с номером строки и
 * причиной словами сервера; «Завести» уходит с галочкой обхода; без права запуска кнопки нет.
 * Строка очереди загрузки говорит, сколько ждёт и сколько займёт, и снимается с вопросом.
 *
 * Доработка 29.10.2026: «i» у кнопки и у поля говорит, какой файл подойдёт, и скачивает
 * шаблон; файл не того вида, пустой или больше предела не уходит — причина у поля; отказ
 * сервера, и страница 413 прокси тоже, — плашкой в окне, словами, а не кодом ответа.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { BulkReport, BulkRow } from '../api/bulkDonors';
import { ADMIN, CRAWL_ROUTES, NO_NICHE, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { reportCsv, reportWords } from './BulkDonors';
import { etaWords, termWords } from './CrawlBacklog';

const QUEUE = { rows: [], waiting: 0, counts: { bought: 1, pending: 0 } };

function row(overrides: Partial<BulkRow>): BulkRow {
  return {
    line: 2,
    given: 'example.com',
    outcome: 'created',
    host: 'example.com',
    reason: null,
    amount: '150.00',
    currency: 'USD',
    crawlable: true,
    ...overrides,
  };
}

const REPORT: BulkReport = {
  file: 'доноры.csv',
  rows: [
    row({}),
    row({ line: 3, given: 'example.org', host: 'example.org', outcome: 'priced' }),
    row({
      line: 4,
      given: 'не-адрес',
      host: null,
      outcome: 'skipped',
      reason: '«не-адрес» — не домен: впишите адрес сайта, например example.com.',
      crawlable: false,
    }),
  ],
  created: 1,
  priced: 1,
  skipped: 1,
  crawlable: 2,
  queued: 0,
  backlog: { waiting: 2, eta_sec: 3_600, workers: 8 },
};

function open(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    ...CRAWL_ROUTES,
    ...NO_NICHE,
    'GET /api/advertisers': { body: QUEUE },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/advertisers');
  return recorded;
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error('поля файла нет');
  return input;
}

/** Файл размером `size` байт: содержимое тестам не нужно, размер — да. */
function sized(name: string, type: string, size: number): File {
  const file = new File(['домен,цена\n'], name, { type });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

async function openWindow(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(await screen.findByRole('button', { name: 'Загрузить списком' }));
  return screen.findByRole('dialog', { name: 'Загрузить доноров списком' });
}

function formsSentTo(path: string): FormData[] {
  return vi
    .mocked(globalThis.fetch)
    .mock.calls.filter(([input]) => typeof input === 'string' && input.endsWith(path))
    .map(([, init]) => init?.body)
    .filter((body): body is FormData => body instanceof FormData);
}

describe('доноры списком', () => {
  it('файл — сначала отчёт без записи, потом «Завести» с галочкой обхода', async () => {
    open({
      'POST /api/donors/import/preview': { body: REPORT },
      'POST /api/donors/import': { body: { ...REPORT, queued: 2 } },
    });
    const user = userEvent.setup();

    const button = await screen.findByRole('button', { name: 'Загрузить списком' });
    expect(button).toHaveAttribute('aria-haspopup', 'dialog');
    await user.click(button);
    const dialog = await screen.findByRole('dialog', { name: 'Загрузить доноров списком' });
    const enter = within(dialog).getByRole('button', { name: /Завести/ });
    expect(enter).toBeDisabled();
    await user.upload(fileInput(), new File(['домен,цена\n'], 'доноры.csv', { type: 'text/csv' }));

    expect(
      await within(dialog).findByText(
        'Новых доноров заведём: 1, цена обновится у 1, пропустим 1 строку.',
      ),
    ).toBeInTheDocument();
    const problems = within(dialog).getByRole('table', { name: 'Строки с причиной' });
    expect(within(problems).getByText(/«не-адрес» — не домен/)).toBeInTheDocument();
    expect(
      within(dialog).getByRole('checkbox', { name: /Поставить в обход: 2 донора/ }),
    ).toBeChecked();
    expect(within(dialog).getByText(/≈ час при 8 обходчиках/)).toBeInTheDocument();
    expect(formsSentTo('/api/donors/import/preview')).toHaveLength(1);

    await user.click(within(dialog).getByRole('button', { name: 'Завести · 2' }));

    expect(
      await screen.findByText(/Новых доноров заведено: 1.*В очередь обхода: 2 донора\./),
    ).toBeInTheDocument();
    const [sent] = formsSentTo('/api/donors/import');
    expect(sent?.get('crawl')).toBe('true');
    expect(sent?.get('file')).toBeInstanceOf(File);
  });

  it('отказ сервера — плашкой в окне, словами: каких колонок нет', async () => {
    const said =
      'в первой строке нет колонок «домен» и «цена» — назовите колонки, как в шаблоне: ' +
      'домен, цена, валюта, откуда цена. Сейчас в первой строке: имя, сумма';
    open({ 'POST /api/donors/import/preview': { status: 400, body: { detail: said } } });
    const user = userEvent.setup();

    const dialog = await openWindow(user);
    await user.upload(fileInput(), new File(['имя,сумма\n'], 'x.csv', { type: 'text/csv' }));

    const alert = await within(dialog).findByRole('alert');
    expect(within(alert).getByText('Файл не прочитан')).toBeInTheDocument();
    expect(
      within(alert).getByText(
        'В первой строке нет колонок «домен» и «цена» — назовите колонки, как в шаблоне: ' +
          'домен, цена, валюта, откуда цена. Сейчас в первой строке: имя, сумма.',
      ),
    ).toBeInTheDocument();
    // Плашка, а не уведомление: слова отказа на экране — один раз, в окне.
    expect(screen.getAllByText(/В первой строке нет колонок/)).toHaveLength(1);
    expect(within(dialog).getByRole('button', { name: /Завести/ })).toBeDisabled();
  });

  it('страница 413 прокси — словами предела, а не «Сервер ответил 413»', async () => {
    open({
      'POST /api/donors/import/preview': {
        status: 413,
        raw: '<html><body><h1>413 Request Entity Too Large</h1></body></html>',
        headers: { 'content-type': 'text/html' },
      },
    });
    const user = userEvent.setup();

    const dialog = await openWindow(user);
    await user.upload(fileInput(), new File(['домен,цена\n'], 'x.csv', { type: 'text/csv' }));

    const alert = await within(dialog).findByRole('alert');
    expect(
      within(alert).getByText('Файл «x.csv» больше 7 МБ — разделите список на несколько файлов.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Сервер ответил 413/)).toBeNull();
  });

  it('отказ записи — плашкой «Доноры не заведены», отчёт остаётся', async () => {
    open({
      'POST /api/donors/import/preview': { body: REPORT },
      'POST /api/donors/import': {
        status: 403,
        body: { detail: 'заводить доноров может только тот, у кого есть право запуска' },
      },
    });
    const user = userEvent.setup();

    const dialog = await openWindow(user);
    await user.upload(fileInput(), new File(['домен,цена\n'], 'x.csv', { type: 'text/csv' }));
    await user.click(await within(dialog).findByRole('button', { name: 'Завести · 2' }));

    const alert = await within(dialog).findByRole('alert');
    expect(within(alert).getByText('Доноры не заведены')).toBeInTheDocument();
    expect(
      within(alert).getByText('Заводить доноров может только тот, у кого есть право запуска.'),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('table', { name: 'Строки с причиной' })).toBeInTheDocument();
  });

  it.each([
    [
      'список.pdf',
      'application/pdf',
      100,
      'Файл «список.pdf» — не таблица CSV или Excel (.xlsx): сохраните список как .csv или .xlsx, как в шаблоне.',
    ],
    [
      'фото.csv',
      'image/png',
      100,
      'Файл «фото.csv» — не таблица CSV или Excel (.xlsx): сохраните список как .csv или .xlsx, как в шаблоне.',
    ],
    [
      'старый.xls',
      'application/vnd.ms-excel',
      100,
      'Старый формат Excel (.xls) не читается — сохраните файл как .xlsx или CSV UTF-8.',
    ],
    ['пустой.csv', 'text/csv', 0, 'Файл «пустой.csv» пуст — в нём нет ни одной строки.'],
    [
      'большой.xlsx',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      7 * 2 ** 20 + 1,
      'Файл «большой.xlsx» больше 7 МБ — разделите список на несколько файлов.',
    ],
  ])('%s не уходит на сервер — причина у поля', async (name, type, size, said) => {
    open({});
    // Окно выбора пропускает и «Все файлы»: проверка — у поля, а не только у `accept`.
    const user = userEvent.setup({ applyAccept: false });

    const dialog = await openWindow(user);
    await user.upload(fileInput(), sized(name, type, size));

    expect(within(dialog).getByText(said)).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(formsSentTo('/api/donors/import/preview')).toEqual([]);
    expect(within(dialog).getByRole('button', { name: /Завести/ })).toBeDisabled();
  });

  it('ровно 7 МБ — уходит: сервер отказывает только тому, что больше', async () => {
    open({ 'POST /api/donors/import/preview': { body: REPORT } });
    const user = userEvent.setup();

    const dialog = await openWindow(user);
    await user.upload(fileInput(), sized('ровно.csv', 'text/csv', 7 * 2 ** 20));

    expect(await within(dialog).findByText(/Новых доноров заведём: 1/)).toBeInTheDocument();
    expect(formsSentTo('/api/donors/import/preview')).toHaveLength(1);
  });

  it('без права запуска кнопки нет', async () => {
    open({}, { ...ADMIN, permissions: ['view'] });

    await screen.findByRole('heading', { name: 'Обход доноров' });
    expect(screen.queryByRole('button', { name: 'Загрузить списком' })).toBeNull();
  });

  it('очередь загрузки — сколько ждёт и сколько займёт; снимается с вопросом', async () => {
    const recorded = open({
      'GET /api/donors/import/backlog': { body: { waiting: 3, eta_sec: 7_200, workers: 8 } },
      'DELETE /api/donors/import/backlog': { body: { dropped: 3 } },
    });
    const user = userEvent.setup();

    expect(
      await screen.findByText(/Очередь загрузки: 3 донора ждут обхода — ≈ 2 ч при 8 обходчиках/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Снять очередь' }));
    const ask = await screen.findByRole('dialog', { name: 'Снять очередь загрузки?' });
    await user.click(within(ask).getByRole('button', { name: 'Снять очередь' }));

    expect(
      await screen.findByText('Очередь загрузки снята: 3 донора в обход не пойдут.'),
    ).toBeInTheDocument();
    expect(recorded.calls.filter((call) => call.method === 'DELETE')).toHaveLength(1);
  });
});

describe('«i» загрузки — какой файл подойдёт', () => {
  const clicked: HTMLAnchorElement[] = [];

  beforeEach(() => {
    clicked.length = 0;
    // В jsdom ссылок на объекты нет вовсе: подставляем их и ловим нажатие ссылки файла.
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:template'),
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

  it('у «Загрузить списком»: вид, колонки, валюта, пределы, пример и «Скачать шаблон»', async () => {
    const recorded = open({
      'GET /api/donors/import/template': {
        raw: 'домен,цена,валюта,откуда цена\n',
        headers: { 'content-type': 'text/csv' },
      },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Какой файл подойдёт' }));
    const card = await screen.findByRole('dialog', { name: 'Какой файл подойдёт' });

    expect(
      within(card).getByText('Таблица CSV или Excel (XLSX) — из Excel читается первый лист.'),
    ).toBeInTheDocument();
    expect(
      within(card).getByText(
        'Колонки — по заголовку или, без заголовка, по порядку: домен, цена, валюта, откуда ' +
          'цена. Серая цена и способы оплаты — колонками с такими заголовками, если они есть.',
      ),
    ).toBeInTheDocument();
    expect(
      within(card).getByText(/^Пустая валюта — USD\. До 10\s000 строк и 7 МБ\.$/),
    ).toBeInTheDocument();
    expect(within(card).getByText(/example\.com,150,USD,прайс агентства/)).toBeInTheDocument();
    expect(within(card).getByText(/example\.org,90,,сайт донора/)).toBeInTheDocument();

    await user.click(within(card).getByRole('button', { name: 'Скачать шаблон' }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(clicked[0]?.download).toBe('доноры-шаблон.csv');
    expect(
      recorded.calls.filter((call) => call.path === '/api/donors/import/template'),
    ).toHaveLength(1);
  });

  it('в окне — у поля файла, наведением, и уходит вместе с указателем', async () => {
    open({});
    const user = userEvent.setup();

    const dialog = await openWindow(user);
    const hint = within(dialog).getByRole('button', { name: 'Какой файл подойдёт' });
    await user.hover(hint);

    const card = await screen.findByRole('dialog', { name: 'Какой файл подойдёт' });
    expect(within(card).getByText(/До 10\s000 строк и 7 МБ/)).toBeInTheDocument();

    await user.unhover(hint);
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Какой файл подойдёт' })).toBeNull(),
    );
  });

  it('шаблон не скачался — уведомлением со словами сервера', async () => {
    open({
      'GET /api/donors/import/template': { status: 503, body: { detail: 'сервер занят' } },
    });
    const user = userEvent.setup();

    const dialog = await openWindow(user);
    await user.click(within(dialog).getByRole('button', { name: 'Скачать шаблон' }));

    expect(await screen.findByText('Шаблон не скачан')).toBeInTheDocument();
    expect(screen.getByText('сервер занят')).toBeInTheDocument();
    expect(clicked).toHaveLength(0);
  });
});

describe('слова загрузки', () => {
  it('итог — будущим до записи и прошедшим после', () => {
    expect(reportWords(REPORT, false)).toBe(
      'Новых доноров заведём: 1, цена обновится у 1, пропустим 1 строку.',
    );
    expect(reportWords({ ...REPORT, skipped: 5 }, true)).toBe(
      'Новых доноров заведено: 1, цена обновлена у 1, пропущено 5 строк.',
    );
  });

  it('отчёт файлом — CSV с меткой BOM и кавычками там, где они нужны', () => {
    const csv = reportCsv([row({ given: 'a,b', reason: 'сказал "нет"' })]);

    expect(csv.startsWith('﻿строка,домен в файле,итог,домен,почему\n')).toBe(true);
    expect(csv).toContain('2,"a,b",заведён,example.com,"сказал ""нет"""');
  });

  it.each([
    [600, '10 мин'],
    [3_600, 'час'],
    [5 * 3_600, '5 ч'],
    [3 * 86_400, '3 сут'],
  ])('срок %s с — «%s»', (seconds, said) => {
    expect(termWords(seconds)).toBe(said);
  });

  it('без обходчиков срока нет — и это сказано', () => {
    expect(etaWords({ waiting: 5, eta_sec: null, workers: 0 })).toBe(
      'обходчиков сейчас не видно — обход начнётся, когда они появятся',
    );
  });
});
