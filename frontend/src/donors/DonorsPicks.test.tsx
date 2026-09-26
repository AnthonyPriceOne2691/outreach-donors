/**
 * Отметка доноров и выгрузка: одна кнопка, и её подпись — то, что в файле.
 *
 * Замечание 26.09.2026: «заведи возможность отмечать, какие именно доноры
 * можно выгрузить, и синхронизируй кнопку „выгрузить все“ с отмеченными
 * и с отфильтрованными». Проверяется, что отметки переживают смену
 * страницы, фильтра и заход в карточку, что отмеченные уходят номерами
 * в теле запроса, и — главное — содержимое файла, а не факт скачивания
 * (урок 25.09.2026: десять колонок из двенадцати были пустыми при зелёных
 * тестах).
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { CARD, CONTACTS_STATE, page, ROW, ROWS } from '../test/donorFixtures';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';

const SECOND_PAGE = [{ ...ROW, id: 21, host: 'second.example.test' }];

async function openDonors(
  routes: Record<string, Answer> = {},
  path = '/donors',
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/contacts': { body: CONTACTS_STATE },
    'GET /api/donors?limit=20&offset=0': { body: page({ total: 21 }) },
    'GET /api/donors?limit=20&offset=20': { body: page({ rows: SECOND_PAGE, total: 21 }) },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText('good.example.test');
  return recorded;
}

function pickBox(host: string): HTMLElement {
  return screen.getByRole('checkbox', { name: `Отметить ${host}` });
}

function exportButton(): HTMLElement {
  return screen.getByRole('button', { name: /^Выгрузить/ });
}

/** Текст файла — теми же двумя путями, что в `DonorsPage.test.tsx`:
 *  `Blob` из Node читается `text()`, из jsdom — `FileReader`. */
function textOf(blob: Blob): Promise<string> {
  if (typeof (blob as Partial<Blob>).text === 'function') return blob.text();
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

describe('отметка доноров', () => {
  it('отмеченный — в шапке числом, и кнопка выгружает отмеченных', async () => {
    await openDonors();
    const user = userEvent.setup();
    expect(exportButton()).toHaveTextContent('Выгрузить всех · 21');
    // Ничего не отмечено — строки «отмечено» нет, а не «отмечено: 0».
    expect(screen.queryByText(/отмечено:/)).toBeNull();

    await user.click(pickBox('weak.example.test'));

    expect(screen.getByText(/отмечено:/)).toHaveTextContent('отмечено: 1');
    expect(exportButton()).toHaveTextContent('Выгрузить отмеченных · 1');
  });

  it('флажок шапки — все на странице; часть — промежуточное состояние', async () => {
    await openDonors();
    const user = userEvent.setup();
    const all = screen.getByRole('checkbox', { name: 'Отметить всех на этой странице' });

    await user.click(all);
    expect(screen.getByText(/отмечено:/)).toHaveTextContent('отмечено: 3');
    expect(all).toBeChecked();

    await user.click(pickBox('blank.example.test'));
    expect(all).not.toBeChecked();
    expect(all).toHaveAttribute('data-indeterminate', 'true');

    await user.click(all);
    expect(screen.getByText(/отмечено:/)).toHaveTextContent('отмечено: 3');
  });

  it('отметки переживают смену страницы и фильтра, снимаются одной кнопкой', async () => {
    const recorded = await openDonors({
      'GET /api/donors?status=unsuitable&limit=20&offset=0': {
        body: page({ rows: [ROWS[1]!], total: 1 }),
      },
    });
    const user = userEvent.setup();

    await user.click(pickBox('good.example.test'));
    await user.click(screen.getByRole('button', { name: 'Страница 2' }));
    await user.click(await screen.findByRole('checkbox', { name: 'Отметить second.example.test' }));
    expect(screen.getByText(/отмечено:/)).toHaveTextContent('отмечено: 2');

    const verdict = screen.getByRole('textbox', { name: 'Вердикт' });
    await user.click(verdict);
    const list = within(document.getElementById(verdict.getAttribute('aria-controls') ?? '')!);
    await user.click(await list.findByRole('option', { name: 'не подходит · 1', hidden: true }));
    await screen.findByText('weak.example.test');
    // Под другим фильтром отмеченные на других страницах — всё ещё отмечены.
    expect(screen.getByText(/отмечено:/)).toHaveTextContent('отмечено: 2');
    expect(exportButton()).toHaveTextContent('Выгрузить отмеченных · 2');
    expect(recorded.calls.at(-1)?.path).toBe('/api/donors?status=unsuitable&limit=20&offset=0');

    await user.click(screen.getByRole('button', { name: 'снять отметку' }));
    expect(screen.queryByText(/отмечено:/)).toBeNull();
    expect(exportButton()).toHaveTextContent('Выгрузить найденных · 1');
  });

  it('флажок не открывает карточку, а отметки переживают заход в неё', async () => {
    const withCard = [...ROWS, { ...ROW, id: CARD.id, host: CARD.host }];
    const recorded = await openDonors({
      'GET /api/donors?limit=20&offset=0': { body: page({ rows: withCard, total: 4 }) },
      [`GET /api/donors/${CARD.id}`]: { body: CARD },
    });
    const user = userEvent.setup();

    await user.click(pickBox('good.example.test'));
    // Щелчок по флажку — не щелчок по строке: карточку никто не спрашивал.
    expect(recorded.calls.some((call) => /^\/api\/donors\/\d+$/.test(call.path))).toBe(false);

    await user.click(screen.getByRole('link', { name: CARD.host }));
    await user.click(await screen.findByRole('link', { name: 'К списку' }));

    expect(await screen.findByText(/отмечено:/)).toHaveTextContent('отмечено: 1');
    expect(pickBox('good.example.test')).toBeChecked();
  });
});

describe('выгрузка отмеченных и найденных', () => {
  const created: Blob[] = [];
  const clicked: HTMLAnchorElement[] = [];

  beforeEach(() => {
    created.length = 0;
    clicked.length = 0;
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn((blob: Blob) => {
        created.push(blob);
        return 'blob:donors';
      }),
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

  const FILE = 'домен;вердикт\r\ngood.example.test;подходит\r\nweak.example.test;не подходит\r\n';

  it('отмеченные уходят номерами в теле запроса, в файле — они, уведомление — сколько', async () => {
    const recorded = await openDonors({
      'POST /api/donors/export': {
        raw: FILE,
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'content-disposition': 'attachment; filename="donors-2026-09-26.csv"',
          'x-export-rows': '2',
          'x-export-asked': '2',
          'x-export-not-donors': '0',
          'x-export-missing': '0',
        },
      },
    });
    const user = userEvent.setup();
    await user.click(pickBox('good.example.test'));
    await user.click(pickBox('weak.example.test'));

    await user.click(exportButton());

    await waitFor(() => expect(clicked).toHaveLength(1));
    const call = recorded.calls.find((sent) => sent.method === 'POST');
    expect(call?.path).toBe('/api/donors/export');
    expect(call?.token).toBe('Bearer пропуск');
    expect(call?.body).toEqual({ ids: [1, 2] });
    expect(await textOf(created[0]!)).toBe(FILE);
    expect(clicked[0]?.download).toBe('donors-2026-09-26.csv');
    expect(await screen.findByText('Выгружено отмеченных: 2.')).toBeInTheDocument();
  });

  it('отмеченный перестал быть донором — в файле его нет, и сказано почему', async () => {
    await openDonors({
      'POST /api/donors/export': {
        raw: 'домен;вердикт\r\ngood.example.test;подходит\r\n',
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'x-export-rows': '1',
          'x-export-asked': '2',
          'x-export-not-donors': '1',
          'x-export-missing': '0',
        },
      },
    });
    const user = userEvent.setup();
    await user.click(pickBox('good.example.test'));
    await user.click(pickBox('weak.example.test'));

    await user.click(exportButton());

    expect(
      await screen.findByText(
        'В файле 1 из 2 отмеченных: 1 уже не доноры — решение человека сменилось.',
      ),
    ).toBeInTheDocument();
  });

  it('найденных больше потолка — подпись и уведомление не обещают всех', async () => {
    const recorded = await openDonors({
      'GET /api/donors?limit=20&offset=0': {
        body: page({ total: 12_345, export_limit: 10_000 }),
      },
      'GET /api/donors/export': {
        raw: 'домен\r\ngood.example.test\r\n',
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'x-export-rows': '10000',
          'x-export-asked': '12345',
        },
      },
    });
    const user = userEvent.setup();
    expect(exportButton().textContent?.replace(/\s/g, ' ')).toBe('Выгрузить 10 000 из 12 345');

    await user.click(exportButton());

    expect(await screen.findByText(/^В файле 10\s000 из 12\s345: больше за раз/)).toBeVisible();
    expect(recorded.calls.find((call) => call.path.startsWith('/api/donors/export'))?.method).toBe(
      'GET',
    );
  });
});
