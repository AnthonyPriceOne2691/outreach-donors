/**
 * Доноры списком на панели «Обход доноров» — «Загрузить списком» и очередь загрузки обхода.
 *
 * Проверяется то, ради чего окно: файл уходит на сервер сначала на предпросмотр, и до записи
 * видно, сколько заведётся, у кого обновится цена и кого пропустят — с номером строки и
 * причиной словами сервера; «Завести» уходит с галочкой обхода; без права запуска кнопки нет.
 * Строка очереди загрузки говорит, сколько ждёт и сколько займёт, и снимается с вопросом.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

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

  it('отказ файла — словами сервера, записывать нечего', async () => {
    open({
      'POST /api/donors/import/preview': {
        status: 400,
        body: { detail: 'не нашлось колонки с ценой: назовите её «цена» — как в шаблоне' },
      },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Загрузить списком' }));
    const dialog = await screen.findByRole('dialog', { name: 'Загрузить доноров списком' });
    await user.upload(fileInput(), new File(['домен\n'], 'x.csv', { type: 'text/csv' }));

    expect(await within(dialog).findByText(/не нашлось колонки с ценой/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /Завести/ })).toBeDisabled();
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
