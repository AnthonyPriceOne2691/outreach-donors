/**
 * «Загрузить план работ» — превью книги и сохранение плана. Ответы сервера — записанные
 * (`serve()`); имена и числа выдуманы.
 *
 * Проверяется то, ради чего окно: книга не .xlsx не уходит на сервер — причина у поля; выбрал
 * книгу — превью: листы с гипотезой, рынками, целью, глубиной, ключами и сметой, смета плана
 * диапазоном; почему не сохранить — плашкой, и «Сохранить план» закрыт; поменял поле после
 * проверки — превью устарело, кнопка закрыта до «Проверить заново»; сохранил — уведомление и
 * список планов; отказ сервера — плашкой словами.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { PlanCard, PlanPreview, PlanSheet, PlanSpend } from '../api/salesPlans';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer } from '../test/server';
import { PlanUpload, PlanUploadButton, planFileRefusal } from './PlanUpload';
import { rangeWords } from './PlanPreviewView';

const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const PREVIEW_URL = 'POST /api/sales/plans/preview';
const SAVE_URL = 'POST /api/sales/plans';

function spend(usd: number, sites: number): PlanSpend {
  return {
    queries: 3,
    results: 30,
    sites,
    serp_usd: usd / 10,
    tokens: 1000,
    tokens_usd: usd - usd / 10,
    hunter_searches: 0,
    hunter_checks: 0,
    usd,
  };
}

function sheet(overrides: Partial<PlanSheet>): PlanSheet {
  return {
    sheet: 'Пилот 1',
    name: 'Логистика — пример',
    segment: 'Транспортные компании со своим сайтом.',
    markets: ['kz'],
    language: 'ru',
    other_countries: 'accept',
    excluded_countries: ['UA'],
    target: 5000,
    depth_pages: 5,
    keys: {
      taken: 12_000,
      blank: 0,
      repeats: 3,
      rejected: 2,
      examples: ['грузоперевозки алматы'],
      rejected_examples: { 'site:example.test': 'поисковый оператор' },
    },
    issues: [],
    warnings: [],
    low: spend(1.5, 26),
    high: spend(7.25, 130),
    goal: 5000,
    ...overrides,
  };
}

function preview(overrides: Partial<PlanPreview> = {}): PlanPreview {
  return {
    file: 'План.xlsx',
    sheets: [sheet({})],
    skipped: { 'Как заполнять': 'инструкция — не гипотеза' },
    serp_mode: 'standard',
    low: spend(1.5, 26),
    high: spend(7.25, 130),
    share: 0.85,
    share_runs: 0,
    tokens_per_site: 3400,
    tokens_per_site_runs: 0,
    notes: [],
    refusals: [],
    digest: 'отпечаток-превью',
    ...overrides,
  };
}

const PLAN: PlanCard = {
  id: 7,
  name: 'План',
  mode: 'economy',
  target_default: null,
  cap_usd: 50,
  status: 'running',
  problem: null,
  created_by: 'admin@ours.example.test',
  created_at: '2026-11-16T12:00:00+00:00',
  hypotheses: [
    {
      id: 1,
      hypothesis_id: 11,
      name: 'Логистика — пример',
      position: 1,
      sheet: 'Пилот 1',
      markets: ['kz'],
      language: 'ru',
      target: 5000,
      max_pages: 5,
      cap_usd: 10,
      stage: 'waiting',
      found: 0,
      spent_usd: 0,
      keywords: 12_000,
      bought: 0,
    },
  ],
};

function book(name = 'План.xlsx', type = XLSX): File {
  return new File(['PK'], name, { type });
}

function setUp(routes: Record<string, Answer | (() => Answer)>) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  return serve({ 'GET /api/auth/me': { body: ADMIN }, ...routes });
}

function fileInput(): HTMLInputElement {
  const found = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (found === null) throw new Error('поля книги нет');
  return found;
}

function saveButton(): HTMLElement {
  return screen.getByRole('button', { name: 'Сохранить план' });
}

async function fill(label: string, value: string) {
  const user = userEvent.setup();
  const field = screen.getByLabelText(label);
  await user.clear(field);
  await user.type(field, value);
}

describe('книга до отправки', () => {
  it('не .xlsx, пустая и больше предела — причина у поля без запроса', () => {
    expect(planFileRefusal(book('план.csv', 'text/csv'))).toBe(
      'Файл «план.csv» — не книга Excel (.xlsx): план читается только из .xlsx.',
    );
    expect(planFileRefusal(book('старый.xls'))).toBe(
      'Старый формат Excel (.xls) — сохраните книгу как .xlsx.',
    );
    expect(planFileRefusal(new File([], 'пусто.xlsx'))).toBe('Файл «пусто.xlsx» пуст.');
    expect(planFileRefusal(book())).toBeNull();
  });

  it('смета — диапазоном, одна глубина — одним числом', () => {
    expect(rangeWords(spend(1.5, 1), spend(7.25, 5))).toBe('1,50 $ — 7,25 $');
    expect(rangeWords(spend(1.5, 1), spend(1.5, 1))).toBe('1,50 $');
    expect(rangeWords(null, null)).toBe('—');
  });
});

describe('превью и сохранение', () => {
  it('выбрал книгу — листы, ключи, смета; без отказов «Сохранить план» открыт', async () => {
    const recorded = setUp({ [PREVIEW_URL]: { body: preview() } });
    renderWith(<PlanUpload onDone={vi.fn()} onCancel={vi.fn()} />);

    await userEvent.setup().upload(fileInput(), book());

    const table = await screen.findByRole('table', { name: 'Листы книги' });
    const row = within(table).getAllByRole('row')[1];
    expect(row).toHaveTextContent('Логистика — пример');
    expect(row).toHaveTextContent('KZ');
    expect(row).toHaveTextContent('топ-50');
    expect(row).toHaveTextContent('12 000 · отсеяно 2');
    expect(screen.getByLabelText('Смета плана')).toHaveTextContent('1,50 $ — 7,25 $');
    expect(screen.getByText(/Пропущены: «Как заполнять»/)).toBeInTheDocument();
    expect(saveButton()).toBeEnabled();
    expect(recorded.calls.filter((call) => call.path === '/api/sales/plans/preview')).toHaveLength(
      1,
    );
  });

  it('почему не сохранить — плашкой, и кнопка закрыта; ошибки листа — у имени листа', async () => {
    setUp({
      [PREVIEW_URL]: {
        body: preview({
          sheets: [sheet({ issues: [{ field: 'language', words: '«Язык писем»: «немецкий»' }] })],
          refusals: ['потолок плана обязателен — задайте его в долларах, больше нуля'],
        }),
      },
    });
    renderWith(<PlanUpload onDone={vi.fn()} onCancel={vi.fn()} />);

    await userEvent.setup().upload(fileInput(), book());

    expect(await screen.findByText('План не сохранить')).toBeInTheDocument();
    expect(screen.getByText(/потолок плана обязателен/)).toBeInTheDocument();
    expect(screen.getByText('«Язык писем»: «немецкий»')).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
  });

  it('поле после проверки — превью устарело, кнопка закрыта до «Проверить заново»', async () => {
    const recorded = setUp({ [PREVIEW_URL]: { body: preview() } });
    renderWith(<PlanUpload onDone={vi.fn()} onCancel={vi.fn()} />);
    await userEvent.setup().upload(fileInput(), book());
    await screen.findByRole('table', { name: 'Листы книги' });

    await fill('Потолок плана, $', '60');

    expect(screen.getByText('Поля изменились после проверки — проверьте заново.')).toBeVisible();
    expect(saveButton()).toBeDisabled();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Проверить заново' }));
    await waitFor(() => expect(saveButton()).toBeEnabled());
    expect(recorded.calls.filter((call) => call.path === '/api/sales/plans/preview')).toHaveLength(
      2,
    );
  });

  it('сохранил — план уходит на сервер, окно сообщает и закрывается', async () => {
    const done = vi.fn();
    const recorded = setUp({
      [PREVIEW_URL]: { body: preview() },
      [SAVE_URL]: { status: 201, body: PLAN },
    });
    renderWith(<PlanUpload onDone={done} onCancel={vi.fn()} />);
    await userEvent.setup().upload(fileInput(), book());
    await screen.findByRole('table', { name: 'Листы книги' });

    await userEvent.setup().click(saveButton());

    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(await screen.findByText('План «План» сохранён: 1 гипотеза.')).toBeInTheDocument();
    expect(recorded.calls.map((call) => `${call.method} ${call.path}`)).toContain(
      'POST /api/sales/plans',
    );
  });

  it('отказ сервера — плашкой словами сервера', async () => {
    setUp({
      [PREVIEW_URL]: {
        status: 400,
        body: { detail: 'файл «План.xlsx» пуст — в нём нет ни одного листа' },
      },
    });
    renderWith(<PlanUpload onDone={vi.fn()} onCancel={vi.fn()} />);

    await userEvent.setup().upload(fileInput(), book());

    expect(await screen.findByText('Книга не прочитана')).toBeInTheDocument();
    expect(
      screen.getByText('Файл «План.xlsx» пуст — в нём нет ни одного листа.'),
    ).toBeInTheDocument();
  });
});

describe('кнопки на вкладке', () => {
  it('«Планы работ» — список планов по запросу', async () => {
    setUp({ 'GET /api/sales/plans': { body: { plans: [PLAN] } } });
    renderWith(<PlanUploadButton />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Планы работ' }));

    const table = await screen.findByRole('table', { name: 'Гипотезы плана «План»' });
    expect(table).toHaveTextContent('Логистика — пример');
    expect(table).toHaveTextContent('12 000');
    expect(screen.getByText(/План · экономный · идёт · потолок 50,00 \$/)).toBeInTheDocument();
  });
});
