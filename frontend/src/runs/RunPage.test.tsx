/**
 * Экран прогона: кнопка, которая не нажимается без сметы.
 *
 * Это единственный экран, где человек тратит деньги, и проверяется здесь
 * ровно то, ради чего он такой: нельзя запустить, не увидев цену, и нельзя
 * запустить, если цена не помещается в остаток.
 */

import { notifications } from '@mantine/notifications';
import { screen, waitFor, within } from '@testing-library/react';
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
  reason: null,
};

/** Причина, как её отдаёт сервер: без имени класса — его убирает сервер,
 *  и из записей до 25.09.2026 тоже. */
const STOP_REASON =
  'остановлен разбором: задача упала: Прогон обойдётся в 3480 юнитов, доступно 3000. ' +
  'Новых доменов 61 из 62; сократите список ключей или поднимите кап., продолжений 2 из 2, ' +
  'молчание 901 с';

const STOPPED = {
  ...QUEUED,
  id: 6,
  status: 'stopped',
  estimated_units: 300,
  actual_units: 120,
  estimate_error: -0.6,
  hosts: 42,
  stats: { причина: 'сырой текст из базы — экран его не показывает' },
  reason: STOP_REASON,
};

/** Ответ истории: страница, её размер и сколько прогонов всего. */
function history(runs: unknown[], extra: Record<string, unknown> = {}) {
  return {
    body: { runs, total: runs.length, page: 1, limit: 10, workers: 1, queued: 0, ...extra },
  };
}

async function openRun(routes: Record<string, unknown> = {}, path = '/run') {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/runs/countries': { body: ['us', 'de'] },
    'GET /api/runs?page=1': history([]),
    'GET /api/keywords/yield?country=us': { body: [] },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, path);
  await screen.findByLabelText('Ключевые слова');
  return recorded;
}

/** Прогоны с номерами от `from` вниз — законченные, без причин. */
function finished(from: number, count: number) {
  return Array.from({ length: count }, (_, index) => ({
    ...QUEUED,
    id: from - index,
    status: 'done',
  }));
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
    // Словами экрана, а не так, как язык называет сервер для модели.
    expect(screen.getByText('Языки рынка: английский, французский')).toBeInTheDocument();
    expect(screen.queryByText(/English/)).not.toBeInTheDocument();
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
    await openRun({ 'GET /api/runs?page=1': history([QUEUED]) });

    expect(await screen.findByText('в очереди')).toBeInTheDocument();
    // Смета и домены появятся позже — но сам прогон на экране уже есть.
    expect(screen.getByText('№7')).toBeInTheDocument();
  });

  it('очередь без воркера — это не работающий сервис', async () => {
    await openRun({ 'GET /api/runs?page=1': history([QUEUED], { workers: 0 }) });

    expect(await screen.findByText('Задачу некому взять')).toBeInTheDocument();
  });

  it('пока воркер жив, про него ничего не говорят', async () => {
    await openRun({ 'GET /api/runs?page=1': history([QUEUED]) });
    await screen.findByText('в очереди');

    expect(screen.queryByText('Задачу некому взять')).not.toBeInTheDocument();
  });

  it('у остановленного прогона причина — за «!», целиком и по нажатию', async () => {
    // Замечание 25.09.2026: причина в три-пять строк раздувала колонку
    // состояния. В ячейке — значок, текст целиком — в поповере.
    await openRun({ 'GET /api/runs?page=1': history([STOPPED]) });
    const user = userEvent.setup();

    const hint = await screen.findByRole('button', { name: 'Почему остановлен' });
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.queryByText(/Прогон обойдётся/)).not.toBeInTheDocument();

    await user.click(hint);

    const told = await screen.findByRole('dialog', { name: 'Почему остановлен' });
    // «Остановлен» уже сказано заголовком — текст начинается с того, кто
    // и почему остановил, а не с повтора (аудит 25.09.2026).
    expect(told).toHaveTextContent(
      'Закрыт разбором зависших прогонов: ' + STOP_REASON.slice('остановлен разбором: '.length),
    );
    expect(told.textContent).not.toMatch(/остановлен разбором/);
    // Сырой текст из базы экран не берёт: причину готовит сервер.
    expect(screen.queryByText(/сырой текст/)).not.toBeInTheDocument();
  });

  it('«!» есть только там, где есть причина, — не спрятан, а не отрисован', async () => {
    await openRun({ 'GET /api/runs?page=1': history([STOPPED, { ...QUEUED, id: 5 }]) });

    await screen.findByText('№5');
    expect(screen.getAllByRole('button', { name: 'Почему остановлен', hidden: true })).toHaveLength(
      1,
    );
    expect(screen.queryAllByRole('button', { name: 'Что случилось', hidden: true })).toHaveLength(
      0,
    );
  });

  it('прерывавшийся, но продолженный прогон спрашивает «что случилось»', async () => {
    // Остановленный — отказ: «почему остановлен». Продолженный после сбоя —
    // не отказ, а повод посмотреть: имя и цвет у значка другие.
    const resumed = {
      ...QUEUED,
      id: 8,
      status: 'done',
      reason: 'продолжен после сбоя (1 раз): задача упала: техническая ошибка (ReadTimeout)',
    };
    await openRun({ 'GET /api/runs?page=1': history([resumed]) });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Что случилось' }));

    expect(await screen.findByRole('dialog', { name: 'Что случилось' })).toHaveTextContent(
      'техническая ошибка (ReadTimeout)',
    );
  });

  it('Esc закрывает причину и возвращает фокус на «!»', async () => {
    await openRun({ 'GET /api/runs?page=1': history([STOPPED]) });
    const user = userEvent.setup();
    const hint = await screen.findByRole('button', { name: 'Почему остановлен' });

    await user.click(hint);
    await screen.findByRole('dialog', { name: 'Почему остановлен' });
    await user.keyboard('{Escape}');

    await waitFor(() => expect(hint).toHaveAttribute('aria-expanded', 'false'));
    expect(hint).toHaveFocus();
  });

  it('исключённые доменом стоят рядом с числом доменов, а не вместо него', async () => {
    const gated = {
      ...STOPPED,
      id: 5,
      status: 'done',
      stats: { excluded: 12, excluded_by_reason: { 'в стоп-листе': 12 } },
      reason: null,
    };
    await openRun({ 'GET /api/runs?page=1': history([gated]) });

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
    await openRun({ 'GET /api/runs?page=1': history([judged, QUEUED]) });

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
    await openRun({ 'GET /api/runs?page=1': history([silent]) });

    const badge = await screen.findByText('модель не ответила: 44');
    expect(badge.closest('[title]')).toHaveAttribute(
      'title',
      'модель, запрос (чинить): LLM_API_KEY не задан',
    );
  });

  it('ключи, выдачу по которым провайдер не отдал, — меткой, а не «ничего не нашлось»', async () => {
    // Выдача оплачена, а не пришла: провайдер не успел. Без метки прогон
    // выглядел прогоном по неудачным ключам.
    const short = {
      ...STOPPED,
      id: 30,
      status: 'done',
      keywords: 10,
      stats: { keywords_lost: ['ключ один', 'ключ два'], keywords_without_results: ['пусто'] },
      reason: null,
    };
    await openRun({ 'GET /api/runs?page=1': history([short, QUEUED]) });

    expect(await screen.findByText('без выдачи: 2 из 10')).toBeInTheDocument();
    // У прогона без потерь метки нет.
    expect(screen.getAllByText(/без выдачи/)).toHaveLength(1);
  });
});

describe('история прогонов по страницам', () => {
  // Замечание 25.09.2026: «пагинация, максимум 10 прогонов на странице».
  // Страницу считает сервер; экран спрашивает номер и показывает ответ.
  const PAGER = 'Страницы истории прогонов';

  it('десять прогонов — одна страница, и переключателя нет вовсе', async () => {
    await openRun({ 'GET /api/runs?page=1': history(finished(10, 10)) });

    await screen.findByText('№10');
    expect(screen.queryByRole('navigation', { name: PAGER, hidden: true })).not.toBeInTheDocument();
  });

  it('больше десяти — переключатель, и вторая страница спрашивается у сервера', async () => {
    const recorded = await openRun({
      'GET /api/runs?page=1': history(finished(12, 10), { total: 12 }),
      'GET /api/runs?page=2': history(finished(2, 2), { total: 12, page: 2 }),
    });
    const user = userEvent.setup();

    const pager = await screen.findByRole('navigation', { name: PAGER });
    await user.click(within(pager).getByRole('button', { name: 'Страница 2' }));

    expect(await screen.findByText('№1')).toBeInTheDocument();
    expect(screen.queryByText('№12')).not.toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path === '/api/runs?page=2')).toBe(true);
    // Экран не режет список сам: на странице ровно то, что прислал сервер.
    expect(screen.getAllByRole('row')).toHaveLength(1 + 2);
  });

  it('номер страницы — в адресе: вторая по ссылке открывается второй', async () => {
    const recorded = await openRun(
      { 'GET /api/runs?page=2': history(finished(2, 2), { total: 12, page: 2 }) },
      '/run?page=2',
    );

    expect(await screen.findByText('№2')).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path === '/api/runs?page=1')).toBe(false);
    const pager = screen.getByRole('navigation', { name: PAGER });
    expect(within(pager).getByRole('button', { name: 'Страница 2' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('страница за концом уводит на последнюю, а не показывает «прогонов нет»', async () => {
    const recorded = await openRun(
      {
        'GET /api/runs?page=5': history([], { total: 12, page: 5 }),
        'GET /api/runs?page=2': history(finished(2, 2), { total: 12, page: 2 }),
      },
      '/run?page=5',
    );

    expect(await screen.findByText('№2')).toBeInTheDocument();
    expect(recorded.calls.map((call) => call.path)).toContain('/api/runs?page=2');
    expect(screen.queryByText(/Прогонов ещё не было/)).not.toBeInTheDocument();
  });

  it('негодный номер в адресе — первая страница, а не отказ сервера', async () => {
    const recorded = await openRun({}, '/run?page=abc');

    expect(await screen.findByText(/Прогонов ещё не было/)).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path === '/api/runs?page=1')).toBe(true);
  });

  it('после запуска со второй страницы новый прогон виден на первой', async () => {
    const recorded = await openRun(
      {
        'GET /api/runs?page=2': history(finished(2, 2), { total: 12, page: 2 }),
        'GET /api/runs?page=1': history(finished(13, 10), { total: 13 }),
        'POST /api/runs/estimate': { body: FITS },
        'POST /api/runs': { body: { run_id: 13, job_id: 'abc', note: 'Прогон встал в очередь.' } },
      },
      '/run?page=2',
    );
    const user = userEvent.setup();
    await screen.findByText('№2');

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await screen.findByText('до 177');
    await user.click(screen.getByRole('button', { name: /Запустить/ }));

    expect(await screen.findByText('№13')).toBeInTheDocument();
    const launched = recorded.calls.findIndex(
      (call) => call.method === 'POST' && call.path === '/api/runs',
    );
    expect(recorded.calls.slice(launched).map((call) => call.path)).toContain('/api/runs?page=1');
  });
});

describe('рассмотрение прогона', () => {
  it('прогон с очередью ведёт к рассмотрению и говорит, сколько ждёт решения', async () => {
    const queued = { ...QUEUED, id: 18, status: 'done', queue: { pending: 394, accepted: 3 } };
    await openRun({ 'GET /api/runs?page=1': history([queued]) });

    const link = await screen.findByRole('link', { name: 'Рассмотреть 394' });
    expect(link).toHaveAttribute('href', '/runs/18/review');
    expect(screen.getByText('принято 3 · отклонено 0')).toBeInTheDocument();
  });

  it('прогон до очереди так и называется, а не показывает нули', async () => {
    await openRun({ 'GET /api/runs?page=1': history([QUEUED]) });

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

/** Пункты того выпадающего списка, в котором есть пункт `known`. Пункты всех
 *  списков экрана лежат в документе сразу (в jsdom они `display: none`),
 *  и общий поиск по роли смешал бы страны с глубиной. */
async function optionsNear(known: string): Promise<(string | null)[]> {
  const option = await screen.findByRole('option', { name: known, hidden: true });
  const list = option.closest('[role="listbox"]');
  if (!(list instanceof HTMLElement)) throw new Error(`у пункта «${known}» нет списка`);
  return within(list)
    .getAllByRole('option', { hidden: true })
    .map((one) => one.textContent);
}

describe('глубина выдачи', () => {
  // Замечание 25.09.2026: «Глубина, страниц» (1–5) стала выпадающим списком
  // «Глубина выдачи» на 10, 20, 30, 50 и 100 результатов, по умолчанию 10.
  // На сервер глубина уходит страницами по десять — так её считает смета.
  // Выпадающий список Mantine в jsdom остаётся `display: none` — раскладки
  // здесь нет, и без `hidden` его пункты не видны запросу. Клик настоящий.
  async function pickDepth(user: ReturnType<typeof userEvent.setup>, title: string) {
    await user.click(screen.getByRole('textbox', { name: 'Глубина выдачи' }));
    await user.click(await screen.findByRole('option', { name: title, hidden: true }));
  }

  it('по умолчанию 10 результатов, и в списке ровно пять глубин', async () => {
    await openRun();
    const user = userEvent.setup();

    const field = screen.getByRole('textbox', { name: 'Глубина выдачи' });
    expect(field).toHaveValue('10 результатов');
    await user.click(field);

    expect(await optionsNear('10 результатов')).toEqual([
      '10 результатов',
      '20 результатов',
      '30 результатов',
      '50 результатов',
      '100 результатов',
    ]);
  });

  it('сто результатов уходят на сервер десятью страницами — и в смету, и в запуск', async () => {
    const deep = { ...FITS, depth_pages: 10, expected_results: 200 };
    const recorded = await openRun({
      'POST /api/runs/estimate': { body: deep },
      'POST /api/runs': { body: { run_id: 7, job_id: 'abc', note: 'Прогон встал в очередь.' } },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await pickDepth(user, '100 результатов');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    // Подпись сметы — теми же словами, что поле.
    expect(await screen.findByText('2 ключа × 100 результатов')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Запустить/ }));

    await waitFor(() =>
      expect(recorded.calls.filter((call) => call.method === 'POST')).toHaveLength(2),
    );
    const sent = recorded.calls.filter((call) => call.method === 'POST').map((call) => call.body);
    expect(sent).toEqual([
      { keywords: ['ремонт', 'дизайн'], country: 'us', depth_pages: 10 },
      { keywords: ['ремонт', 'дизайн'], country: 'us', depth_pages: 10 },
    ]);
  });

  it('смена глубины после сметы — смета устарела, запуск закрыт', async () => {
    // На ста результатах выдача вдесятеро дороже: обещать цену топ-10 нельзя.
    await openRun({ 'POST /api/runs/estimate': { body: FITS } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await screen.findByText('до 177');
    await pickDepth(user, '50 результатов');

    expect(await screen.findByText('Смета устарела')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('отказ сервера за пределом глубины виден его словами', async () => {
    // Уведомления живут в общем хранилище Mantine и копятся от теста к тесту:
    // сверх пяти новые встают в очередь и не видны. Этому тесту нужно своё.
    notifications.clean();
    notifications.cleanQueue();
    const refusal =
      'Глубина выдачи — от 10 до 100 результатов на ключ, то есть от 1 до 10 страниц по 10; пришло 20.';
    await openRun({
      'POST /api/runs/estimate': {
        status: 422,
        body: { detail: [{ type: 'depth_out_of_range', msg: refusal }] },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText(refusal)).toBeInTheDocument();
    expect(screen.getByText('Смета не посчиталась')).toBeInTheDocument();
  });
});

describe('режим сборки моделью', () => {
  const MODEL = {
    'GET /api/keywords/presets': { body: ['guest', 'guides', 'media', 'reviews', 'wide'] },
    'GET /api/keywords/languages?country=us': { body: ['English'] },
  };

  it('наборы углов — словами, по умолчанию широкий охват, а на сервер уходит код', async () => {
    const recorded = await openRun({ ...MODEL, 'POST /api/keywords': { body: POOL } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Собрать моделью' }));
    const preset = await screen.findByRole('textbox', { name: 'Набор углов' });
    expect(preset).toHaveValue('широкий охват');
    await user.click(preset);
    // Ни одного кода: аудит 25.09.2026 нашёл здесь wide/guest/guides/media/reviews.
    expect(await optionsNear('широкий охват')).toEqual([
      'гостевые посты',
      'инструкции и правила',
      'новости и издания',
      'обзоры и подборки',
      'широкий охват',
    ]);
    await user.click(screen.getByRole('option', { name: 'гостевые посты', hidden: true }));
    await user.click(screen.getByRole('button', { name: 'Собрать' }));

    await waitFor(() =>
      expect(recorded.calls.some((call) => call.path === '/api/keywords')).toBe(true),
    );
    const call = recorded.calls.find((item) => item.path === '/api/keywords');
    expect(call?.body).toMatchObject({ preset: 'guest' });
    expect(screen.getByText('Язык рынка: английский')).toBeInTheDocument();
  });

  it('незнакомый набор назван общими словами с кодом, а не голым кодом', async () => {
    await openRun({ ...MODEL, 'GET /api/keywords/presets': { body: ['wide', 'podcasts'] } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Собрать моделью' }));
    await user.click(await screen.findByRole('textbox', { name: 'Набор углов' }));

    expect(
      await screen.findByRole('option', { name: 'другой набор (podcasts)', hidden: true }),
    ).toBeInTheDocument();
  });

  it('свои ключи — поля сборки не отрисованы вовсе, а не спрятаны', async () => {
    // jsdom считает спрятанное невидимым, и тест на `hidden` был бы зелёным
    // при полях, стоящих на экране (урок 21.09.2026). Проверяется документ.
    await openRun(MODEL);
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Собрать моделью' }));
    expect(await screen.findByRole('textbox', { name: 'Набор углов' })).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: 'Свои ключи' }));

    await waitFor(() => expect(document.querySelector('[data-unfold]')).not.toBeInTheDocument());
    expect(screen.queryByLabelText('Набор углов', { selector: 'input' })).toBeNull();
    expect(screen.queryByText(/Сборка ничего платного не тратит/)).toBeNull();
  });
});

describe('задачу некому взять', () => {
  it('предупреждение видно и со второй страницы истории', async () => {
    // Прогон в очереди — на первой странице, а смотрят вторую. До 25.09.2026
    // экран искал его только среди своих строк и молчал.
    await openRun(
      {
        'GET /api/runs?page=2': history(finished(2, 2), {
          total: 12,
          page: 2,
          workers: 0,
          queued: 1,
        }),
      },
      '/run?page=2',
    );

    expect(await screen.findByText('Задачу некому взять')).toBeInTheDocument();
  });
});
