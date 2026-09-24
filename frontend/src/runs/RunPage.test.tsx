/**
 * Экран прогона: кнопка, которая не нажимается без сметы.
 *
 * Это единственный экран, где человек тратит деньги, и проверяется здесь
 * ровно то, ради чего он такой: нельзя запустить, не увидев цену, и нельзя
 * запустить, если цена не помещается в остаток.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const FITS = {
  keywords: 2,
  depth_pages: 1,
  expected_results: 20,
  expected_domains: 3,
  units_screen: 50,
  units_metrics: 72,
  units_by_country: 55,
  units_total: 177,
  units_left: 100000,
  units_cap: 100000,
  units_spent_this_month: 6416,
  cap_left: 93584,
  run_ceiling: null,
  budget: 93584,
  affordable: true,
  shortfall: 0,
  serp_cost_usd: 0.0012,
};

const TOO_MUCH = { ...FITS, units_left: 100, budget: 100, affordable: false, shortfall: 77 };

const CAP_EATEN = {
  ...FITS,
  units_spent_this_month: 99_950,
  cap_left: 50,
  budget: 50,
  affordable: false,
  shortfall: 127,
};

const QUEUED = {
  id: 7,
  status: 'queued',
  country: 'us',
  keywords: 2,
  estimated_units: null,
  actual_units: null,
  estimate_error: null,
  stats: null,
  started_at: '2026-09-21T10:00:00Z',
  alive_at: '2026-09-21T10:00:00Z',
  hosts: null,
  reviewed: 0,
  disagreements: 0,
  queue: {},
};

const STOPPED = {
  ...QUEUED,
  id: 6,
  status: 'stopped',
  estimated_units: 300,
  actual_units: 120,
  estimate_error: -0.6,
  hosts: 42,
  stats: { причина: 'остановлен разбором: воркер умер, продолжений 2 из 2' },
};

async function openRun(routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/runs/countries': { body: ['us', 'de'] },
    'GET /api/runs': { body: { runs: [], workers: 1 } },
    'GET /api/keywords/yield?country=us': { body: [] },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/run');
  await screen.findByLabelText('Ключевые слова');
  return recorded;
}

/** Поле тем: у Mantine `TagsInput` подпись носят два поля — видимое
 *  и скрытое, — и поиск по подписи находит оба. Берём то, в которое
 *  человек печатает. */
async function topicsField(): Promise<HTMLElement> {
  const fields = await screen.findAllByLabelText('Про что');
  const visible = fields.find((node) => node.getAttribute('data-type') === 'visible');
  if (visible === undefined) {
    // Падаем вслух: молчаливый выбор «первого попавшегося» однажды
    // подсунет скрытое поле, и тест станет зелёным про другое.
    throw new Error(`Видимое поле тем не найдено, полей с такой подписью: ${fields.length}`);
  }
  return visible;
}

const POOL = {
  keywords: ['best betting sites south africa', 'top bookmakers sa'],
  languages: ['English'],
  asked: 9,
  received: 6,
  rejected: 0,
  near_duplicates: 1,
  refusals: [],
  tokens: 1313,
  model: 'gpt-5',
};

describe('сборка ключей моделью', () => {
  it('фразы падают в то же поле, а не уходят в прогон', async () => {
    // Ключи по требованиям приносит оператор, и поле остаётся главным:
    // собранное он видит и правит до сметы.
    const recorded = await openRun({
      'GET /api/keywords/presets': { body: ['guides', 'media', 'reviews', 'wide'] },
      'GET /api/keywords/languages?country=us': { body: ['English'] },
      'POST /api/keywords': { body: POOL },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Собрать моделью' }));
    await user.type(await topicsField(), 'ставки{enter}');
    await user.click(screen.getByRole('button', { name: 'Собрать' }));

    await waitFor(() =>
      expect(screen.getByLabelText('Ключевые слова')).toHaveValue(
        'best betting sites south africa\ntop bookmakers sa',
      ),
    );
    // Сборка ничего платного не трогает: ни сметы, ни запуска.
    expect(recorded.calls.some((call) => call.path === '/api/runs/estimate')).toBe(false);
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('темы доезжают списком, а языки экран не шлёт — их выводит рынок', async () => {
    const recorded = await openRun({
      'GET /api/keywords/presets': { body: ['reviews', 'wide'] },
      'GET /api/keywords/languages?country=us': { body: ['English', 'French'] },
      'POST /api/keywords': { body: POOL },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Собрать моделью' }));
    await user.type(await topicsField(), 'ставки{enter}кроссовки{enter}');
    await user.click(screen.getByRole('button', { name: 'Собрать' }));

    await waitFor(() =>
      expect(recorded.calls.some((call) => call.path === '/api/keywords')).toBe(true),
    );
    const call = recorded.calls.find((item) => item.path === '/api/keywords');
    expect(call?.body).toMatchObject({ topics: ['ставки', 'кроссовки'], country: 'us' });
    // Язык экран не выбирает и не шлёт: его задаёт рынок.
    expect(call?.body).not.toHaveProperty('language');
    // И показывает до сборки, на чём соберётся, — на двух языках пул дороже вдвое.
    expect(screen.getByText(/Языки рынка: English, French/)).toBeInTheDocument();
  });

  it('неполный пул из-за отказов модели назван вслух', async () => {
    // Пул, собранный наполовину из-за отказов, внешне неотличим от пула,
    // который модель честно не набрала.
    await openRun({
      'GET /api/keywords/presets': { body: ['reviews'] },
      'GET /api/keywords/languages?country=us': { body: ['English'] },
      'POST /api/keywords': {
        body: { ...POOL, refusals: ['модель, отказ (чинить): HTTP 401: ключ не принят'] },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Собрать моделью' }));
    await user.click(screen.getByRole('button', { name: 'Собрать' }));

    expect(await screen.findByText(/отказала 1 раз/)).toBeInTheDocument();
  });
});

describe('прогон', () => {
  it('без сметы запускать нечего', async () => {
    await openRun();

    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('смета показывает, во что обойдётся, и открывает запуск', async () => {
    const recorded = await openRun({ 'POST /api/runs/estimate': { body: FITS } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText('до 177')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeEnabled();
    // Смета — отдельный запрос, который ничего не тратит.
    expect(recorded.calls.some((call) => call.path === '/api/runs/estimate')).toBe(true);
  });

  it('смета называет стоимость выдачи — это другой счёт, не юниты', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: FITS } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    // До этого среза расход на выдачу не показывался нигде, хотя это
    // вторая статья после Ahrefs.
    expect(await screen.findByText(/0,00 \$/)).toBeInTheDocument();
    expect(screen.getByText(/Потрачено нами юнитов с начала месяца/)).toBeInTheDocument();
  });

  it('бюджет считается от остатка по капу, а не от самого капа', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: CAP_EATEN } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText(/по капу 50 из 100\s000/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('свой потолок уходит на сервер вместе с ключами', async () => {
    const recorded = await openRun({ 'POST /api/runs/estimate': { body: FITS } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.type(screen.getByLabelText('Потолок юнитов'), '5000');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    await screen.findByText('до 177');
    const sent = recorded.calls.find((call) => call.path === '/api/runs/estimate');
    expect(sent?.body).toMatchObject({ cap: 5000 });
  });

  it('со своим потолком бюджет считается по нему, а не по капу', async () => {
    await openRun({
      'POST /api/runs/estimate': {
        body: { ...FITS, run_ceiling: 5000, budget: 5000 },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.type(screen.getByLabelText('Потолок юнитов'), '5000');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    // Свой потолок — про один прогон, кап — про месяц. Живая проверка
    // поймала ровно эту путаницу: месячная трата вычиталась из потолка.
    expect(await screen.findByText(/ваш потолок 5\s000/)).toBeInTheDocument();
    expect(screen.getByText(/по капу 93\s584 из 100\s000/)).toBeInTheDocument();
  });

  it('не помещается — кнопка не нажимается и сказано, чего не хватает', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: TOO_MUCH } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText('Не помещается в бюджет')).toBeInTheDocument();
    expect(screen.getByText(/Не хватает 77 юнитов/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('смета устаревает вместе со списком ключей', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: FITS } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await screen.findByText('до 177');

    await user.type(screen.getByLabelText('Ключевые слова'), '\nтретий ключ');

    expect(await screen.findByText('Смета устарела')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('прогон в очереди виден до первой траты', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [QUEUED], workers: 1 } } });

    expect(await screen.findByText('в очереди')).toBeInTheDocument();
    // Смета и домены появятся позже — но сам прогон на экране уже есть.
    expect(screen.getByText('№7')).toBeInTheDocument();
  });

  it('очередь без воркера — это не работающий сервис', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [QUEUED], workers: 0 } } });

    expect(await screen.findByText('Задачу некому взять')).toBeInTheDocument();
  });

  it('пока воркер жив, про него ничего не говорят', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [QUEUED], workers: 1 } } });
    await screen.findByText('в очереди');

    expect(screen.queryByText('Задачу некому взять')).not.toBeInTheDocument();
  });

  it('у остановленного прогона видна причина, а не пустая ячейка', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [STOPPED], workers: 1 } } });

    expect(await screen.findByText(/воркер умер/)).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('исключённые доменом стоят рядом с числом доменов, а не вместо него', async () => {
    const gated = {
      ...STOPPED,
      id: 5,
      status: 'done',
      stats: { excluded: 12, excluded_by_reason: { 'в стоп-листе': 12 } },
    };
    await openRun({ 'GET /api/runs': { body: { runs: [gated], workers: 1 } } });

    expect(await screen.findByText('42')).toBeInTheDocument();
    expect(screen.getByText('исключено 12')).toBeInTheDocument();
  });

  it('запуск кладёт задачу в очередь, а не ждёт прогона', async () => {
    const recorded = await openRun({
      'POST /api/runs/estimate': { body: FITS },
      'POST /api/runs': {
        body: { run_id: 7, job_id: 'abc-123', note: 'Прогон встал в очередь.' },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await screen.findByText('до 177');
    await user.click(screen.getByRole('button', { name: /Запустить/ }));

    await waitFor(() => {
      expect(screen.getByText('Прогон в очереди')).toBeInTheDocument();
    });
    // По пути мало: историю прогонов читает GET по тому же адресу.
    const launch = recorded.calls.find(
      (call) => call.path === '/api/runs' && call.method === 'POST',
    );
    expect(launch?.body).toEqual({
      keywords: ['ремонт', 'дизайн'],
      country: 'us',
      depth_pages: 1,
    });
  });

  it('показывает, как часто человек разошёлся с судьёй', async () => {
    // Доля расхождений — единственная проверка судьи, как расхождение
    // сметы и факта — проверка сметы. Считается при чтении: решают после.
    const judged = {
      ...STOPPED,
      id: 9,
      status: 'done',
      stats: { judge: { mode: 'shadow', would_cut: 26 } },
      reviewed: 4,
      disagreements: 1,
    };
    await openRun({ 'GET /api/runs': { body: { runs: [judged, QUEUED], workers: 1 } } });

    expect(await screen.findByText('отрезал бы 26')).toBeInTheDocument();
    expect(screen.getByText(/расходится 1 из 4/)).toBeInTheDocument();
    // У выключенного судьи нуля нет — «выключен» и «никого не нашёл» разные новости.
    expect(screen.getByText('выключен')).toBeInTheDocument();
    expect(screen.getByText('человек не смотрел')).toBeInTheDocument();
    // Модель отвечала — метки о молчании нет.
    expect(screen.queryByText(/модель не ответила/)).not.toBeInTheDocument();
  });

  it('говорит, что модель не ответила судье, а не «отрезал бы 0»', async () => {
    // Прогон №21, 24.09.2026: ключа модели на сервере не было, а таблица
    // показывала «отрезал бы 0» — как у судьи, который никого не нашёл.
    const silent = {
      ...STOPPED,
      id: 21,
      status: 'done',
      stats: {
        judge: {
          mode: 'shadow',
          would_cut: 0,
          unanswered: 44,
          unanswered_reason: 'модель, запрос (чинить): LLM_API_KEY не задан',
        },
      },
    };
    await openRun({ 'GET /api/runs': { body: { runs: [silent], workers: 1 } } });

    const badge = await screen.findByText('модель не ответила: 44');
    expect(badge.closest('[title]')).toHaveAttribute(
      'title',
      'модель, запрос (чинить): LLM_API_KEY не задан',
    );
  });
});

describe('рассмотрение прогона', () => {
  it('прогон с очередью ведёт к рассмотрению и говорит, сколько ждёт решения', async () => {
    const queued = { ...QUEUED, id: 18, status: 'done', queue: { pending: 394, accepted: 3 } };
    await openRun({ 'GET /api/runs': { body: { runs: [queued], workers: 1 } } });

    const link = await screen.findByRole('link', { name: 'Рассмотреть 394' });
    expect(link).toHaveAttribute('href', '/runs/18/review');
    expect(screen.getByText('принято 3 · отклонено 0')).toBeInTheDocument();
  });

  it('прогон до очереди так и называется, а не показывает нули', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [QUEUED], workers: 1 } } });

    expect(await screen.findByText('очереди нет')).toBeInTheDocument();
  });
});

const PROVEN = [
  {
    keyword: 'saas blog write for us',
    found: 40,
    queued: 12,
    accepted: 5,
    rejected: 3,
    pending: 4,
    runs: 2,
  },
  {
    keyword: 'martech guest post',
    found: 22,
    queued: 6,
    accepted: 2,
    rejected: 1,
    pending: 3,
    runs: 1,
  },
];

describe('ключи, дававшие доноров', () => {
  it('видны у поля ключей и добавляются одной кнопкой', async () => {
    await openRun({ 'GET /api/keywords/yield?country=us': { body: PROVEN } });
    const user = userEvent.setup();

    expect(await screen.findByText(/Ключи, дававшие принятых доноров/)).toBeInTheDocument();
    expect(screen.getByText('saas blog write for us · принято 5')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Ключевые слова'), 'martech guest post');
    await user.click(screen.getByRole('button', { name: 'Добавить в список — 1' }));

    // Свой список оператора остаётся его списком: дописано только недостающее.
    expect(screen.getByLabelText('Ключевые слова')).toHaveValue(
      'martech guest post\nsaas blog write for us',
    );
  });

  it('когда таких ключей нет, блока нет вовсе', async () => {
    await openRun();

    await screen.findByLabelText('Ключевые слова');
    expect(screen.queryByText(/Ключи, дававшие принятых доноров/)).not.toBeInTheDocument();
  });
});
