/**
 * Вход «Конкуренты» на экране прогона: цена — до кнопки, словами того же счёта.
 *
 * Как у ключей: без сметы, с устаревшей или непомещающейся — «Запустить» не нажимается;
 * смета называет списки отдельно от метрик сайтов, потому что списки платятся тем же
 * ключом Ahrefs; полей ключей в этом входе нет вовсе — они не отрисованы, а не спрятаны.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { history, openRun } from '../test/runFixtures';

/** Смета, которая помещается: два конкурента по сто сайтов, цена строки 14. */
const FITS = {
  lists: [
    { target: 'example.com', rows: 100, units: 1400 },
    { target: 'example.net', rows: 100, units: 1400 },
  ],
  depth: 100,
  row_units: 14,
  units_lists: 2800,
  expected_domains: 200,
  units_screen: 400,
  units_metrics: 5600,
  units_by_country: 11000,
  units_total: 19800,
  tokens: 360000,
  judge_mode: 'shadow',
  topic_tokens: 0,
  topic_tokens_per_site: 0,
  topic_measured: false,
  llm_refusal: null,
  model_refusal: null,
  units_left: 100000,
  units_cap: 100000,
  units_spent_in_cycle: 6416,
  cap_left: 93584,
  cycle_start: '2026-10-21T00:00:00+00:00',
  cycle_by_key: true,
  cap_unknown: null,
  next_reset: '2026-11-21T00:00:00+00:00',
  run_ceiling: null,
  budget: 93584,
  affordable: true,
  shortfall: 0,
  binding: 'cap',
  short_of: [],
};

/** Не помещается в лимит ключа Ahrefs: свободно меньше сметы. */
const SHORT = {
  ...FITS,
  units_left: 1000,
  budget: 1000,
  affordable: false,
  shortfall: 18800,
  binding: 'key',
  short_of: ['key'],
};

/** Поле конкурентов: у `TagsInput` подпись носят видимое и скрытое поле — берём видимое. */
async function rivalsField(): Promise<HTMLElement> {
  const fields = await screen.findAllByLabelText('Сайты конкурентов');
  const visible = fields.find((node) => node.getAttribute('data-type') === 'visible');
  if (visible === undefined) {
    throw new Error(`Видимое поле конкурентов не найдено, полей с подписью: ${fields.length}`);
  }
  return visible;
}

async function openRivals(routes: Record<string, unknown> = {}) {
  const recorded = await openRun(routes);
  const user = userEvent.setup();
  await user.click(screen.getByRole('radio', { name: 'Конкуренты' }));
  await user.type(await rivalsField(), 'example.com{enter}example.net{enter}');
  return { recorded, user };
}

describe('вход «Конкуренты»', () => {
  it('полей ключей и выдачи нет вовсе: они не отрисованы, а не спрятаны', async () => {
    await openRun();
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Конкуренты' }));

    expect(await rivalsField()).toBeInTheDocument();
    expect(screen.queryByLabelText('Ключевые слова')).toBeNull();
    expect(screen.queryByText('Режим выдачи')).toBeNull();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('цена — до кнопки: списки отдельно от метрик, всё в юнитах Ahrefs и модель словами', async () => {
    const { recorded, user } = await openRivals({
      'POST /api/runs/competitors/estimate': { body: FITS },
    });

    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));

    const lists = await screen.findByText('Списки конкурентов');
    const tile = lists.closest('.metricTile') ?? lists.parentElement;
    expect(tile).not.toBeNull();
    expect(within(tile as HTMLElement).getByText('до 2 800')).toBeInTheDocument();
    expect(screen.getByText('2 конкурента × до 100 сайтов · 14 юн. за строку')).toBeInTheDocument();
    expect(screen.getByText('до 19 800')).toBeInTheDocument();
    expect(
      screen.getByText(/списки 2 800 · просев 400 · метрики 5 600 · гео 11 000/),
    ).toBeVisible();
    expect(screen.getByText(/до 360 000 токенов модели/)).toBeInTheDocument();
    expect(screen.getByText('Помещается')).toBeInTheDocument();
    const sent = recorded.calls.find((call) => call.path === '/api/runs/competitors/estimate');
    expect(sent?.body).toEqual({
      competitors: ['example.com', 'example.net'],
      country: 'us',
      depth: 100,
    });
  });

  it('помещается — запуск уходит с тем же телом, что смета', async () => {
    const { recorded, user } = await openRivals({
      'POST /api/runs/competitors/estimate': { body: FITS },
      'POST /api/runs/competitors': {
        body: { run_id: 31, job_id: 'job-31', note: 'Прогон встал в очередь.' },
      },
    });

    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));
    const launch = screen.getByRole('button', { name: /Запустить/ });
    await waitFor(() => expect(launch).toBeEnabled());
    await user.click(launch);

    await waitFor(() =>
      expect(recorded.calls.some((call) => call.path === '/api/runs/competitors')).toBe(true),
    );
    const started = recorded.calls.find((call) => call.path === '/api/runs/competitors');
    expect(started?.body).toEqual({
      competitors: ['example.com', 'example.net'],
      country: 'us',
      depth: 100,
    });
  });

  it('не хватает лимита ключа — плашка называет лимит и что сократить, запуск закрыт', async () => {
    const { user } = await openRivals({ 'POST /api/runs/competitors/estimate': { body: SHORT } });

    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));

    expect(
      await screen.findByText('Не помещается: не хватает лимита ключа Ahrefs'),
    ).toBeInTheDocument();
    expect(screen.getByText('Или уберите конкурента, или уменьшите глубину списка.')).toBeVisible();
    expect(screen.queryByText('Помещается')).toBeNull();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('смена глубины после сметы — смета устарела, её чисел нет, запуск закрыт', async () => {
    const { user } = await openRivals({ 'POST /api/runs/competitors/estimate': { body: FITS } });
    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));
    await screen.findByText('Списки конкурентов');

    await user.click(screen.getByRole('textbox', { name: 'Глубина списка' }));
    await user.click(await screen.findByRole('option', { name: '200 сайтов на конкурента' }));

    expect(await screen.findByText('Смета устарела')).toBeInTheDocument();
    expect(screen.queryByText('Списки конкурентов')).toBeNull();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('отказ сервера виден его словами', async () => {
    const { user } = await openRivals({
      'POST /api/runs/competitors/estimate': {
        status: 422,
        body: {
          detail: [
            {
              msg: '«a_b.com» — не домен по правилам имён. Впишите домен конкурента, например example.com',
            },
          ],
        },
      },
    });

    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));

    expect(await screen.findByText('Смета не посчиталась')).toBeInTheDocument();
    expect(screen.getByText(/Впишите домен конкурента/)).toBeInTheDocument();
  });

  it('суд не помещается в потолок модели — плашка словами сервера, запуск закрыт', async () => {
    const refusal =
      'Сайты списков обойдутся модели до 360 000 токенов, а потолок модели на прогон — 100 000';
    const { user } = await openRivals({
      'POST /api/runs/competitors/estimate': { body: { ...FITS, llm_refusal: refusal } },
    });

    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));

    expect(await screen.findByText('Не помещается в потолок модели')).toBeInTheDocument();
    expect(screen.getByText(`${refusal}.`)).toBeVisible();
    expect(screen.queryByText('Помещается')).toBeNull();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('модель не готова — плашка словами сервера, запуск закрыт', async () => {
    // Слово владельца 20.10.2026: модель суда не ответила проверке — прогон не начинается.
    const words = 'у модели нет кредитов — пополните счёт OpenAI';
    const { user } = await openRivals({
      'POST /api/runs/competitors/estimate': { body: { ...FITS, model_refusal: words } },
    });

    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));

    const alert = (await screen.findByText('Модель не готова')).closest(
      '.mantine-Alert-root',
    ) as HTMLElement;
    expect(alert).toHaveTextContent(words);
    expect(screen.queryByText('Помещается')).toBeNull();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('тематика сайтов — своей строкой токенов, как у ключей', async () => {
    const { user } = await openRivals({
      'POST /api/runs/competitors/estimate': {
        body: { ...FITS, topic_tokens: 400000, topic_tokens_per_site: 2000 },
      },
    });

    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));

    expect(await screen.findByText(/Тематика сайтов — ещё до/)).toBeVisible();
    expect(screen.getByText(/2 000 на сайт, по оценке — замера ещё не было/)).toBeVisible();
  });

  it('судья выключен — так и сказано, а не «0 токенов»', async () => {
    const { user } = await openRivals({
      'POST /api/runs/competitors/estimate': { body: { ...FITS, tokens: 0, judge_mode: 'off' } },
    });

    await user.click(screen.getByRole('button', { name: /Посчитать смету/ }));

    expect(await screen.findByText(/Судья площадок выключен — модель не тратится/)).toBeVisible();
  });
});

describe('история прогонов', () => {
  it('у прогона по конкурентам в колонке ключей — число конкурентов, домены — подсказкой', async () => {
    const run = {
      id: 31,
      status: 'queued',
      country: 'us',
      keywords: 0,
      estimated_units: null,
      actual_units: null,
      estimate_error: null,
      stats: null,
      started_at: '2026-10-20T10:00:00Z',
      alive_at: '2026-10-20T10:00:00Z',
      hosts: null,
      reviewed: 0,
      disagreements: 0,
      queue: {},
      reason: null,
      started_by: null,
      competitors: ['example.com', 'example.net'],
    };
    await openRun({ 'GET /api/runs?page=1': history([run]) });

    const word = await screen.findByText('конкурента');
    expect(word.parentElement).toHaveAttribute('title', 'example.com, example.net');
  });

  it('судить было нечем — числом меткой, а не молча «посмотри»', async () => {
    const run = {
      id: 33,
      status: 'done',
      country: 'us',
      keywords: 0,
      estimated_units: 9900,
      actual_units: 6100,
      estimate_error: -0.38,
      stats: { judge: { mode: 'shadow', judged: 40, no_text: 3, would_cut: 0 } },
      started_at: '2026-10-20T10:00:00Z',
      alive_at: '2026-10-20T10:40:00Z',
      hosts: 43,
      reviewed: 0,
      disagreements: 0,
      queue: {},
      reason: null,
      started_by: null,
      competitors: ['example.com'],
    };
    await openRun({ 'GET /api/runs?page=1': history([run]) });

    const badge = await screen.findByText('судить нечем: 3');
    expect(badge.closest('[title]')).toHaveAttribute(
      'title',
      'Ни текста выдачи, ни главной — вердикта нет, следующий прогон судит снова',
    );
  });
});
