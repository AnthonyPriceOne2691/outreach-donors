/**
 * Экран рассмотрения прогона: что видно и что уходит на сервер.
 *
 * Три обещания экрана: у каждого кандидата есть ярлык судьи (и «судья
 * не смотрел» — тоже ярлык), сомнительные скрыты со счётчиком, а не
 * выброшены, и решение человека уходит на сервер ровно тем списком,
 * который он отметил.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

/** Адрес запроса, как его видит заглушка сети. */
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.toString() : input.url;
}

const MACHINE = {
  intent: 'editorial_ads',
  recommendation: 'accept',
  decided_by: 'model',
  quote: 'Write for us: we accept guest posts on SaaS marketing',
  reason: null,
  source_url: 'https://martech.example.test/write-for-us',
  home_shop: [],
  home_reached: true,
  judged_at: '2026-09-23T17:00:00Z',
};

const SELLER = { answer: null, answered_at: null, price: null, currency: null };

function card(id: number, host: string, extra: Record<string, unknown> = {}) {
  return {
    candidate_id: id,
    domain_id: id * 10,
    host,
    status: 'pending',
    tier: 'likely',
    carried: false,
    decided_by: null,
    decided_at: null,
    note: null,
    dr: 55,
    org_traffic: 12000,
    geo: 'us',
    geo_top_share: 0.62,
    contact_status: null,
    found_by: ['saas blog write for us', 'martech blog submit article', 'saas seo strategy'],
    sells: null,
    sells_by: null,
    machine: MACHINE,
    seller: SELLER,
    ...extra,
  };
}

const VIEW = {
  run: { id: 18, country: 'us', keywords: 100, created_at: '2026-09-23T16:21:00Z' },
  rows: [
    card(1, 'martech.example.test'),
    card(2, 'unjudged.example.test', {
      tier: 'open',
      machine: { ...MACHINE, recommendation: null, intent: null, decided_by: null, quote: null },
    }),
  ],
  counts: { pending: 394, accepted: 0, rejected: 0 },
  hidden: 278,
  keywords: [
    {
      keyword: 'saas blog write for us',
      found: 12,
      queued: 5,
      accepted: 0,
      rejected: 0,
      pending: 5,
      runs: 1,
    },
  ],
};

const ACCURACY = {
  decided: 0,
  by_advice: {},
  by_layer: {},
  by_intent: {},
  unjudged: 0,
  asked_to_review: 0,
  auto_accept_ready: false,
  auto_accept_precision: 0.95,
  auto_accept_min_decisions: 200,
};

const PENDING = 'GET /api/review/runs/18?status=pending';

async function openReview(routes: Record<string, unknown> = {}, who = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    [PENDING]: { body: VIEW },
    'GET /api/review/accuracy': { body: ACCURACY },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/runs/18/review');
  // Заголовок стоит с первого кадра (номер — из адреса); очередь — когда
  // пришёл ответ: по вкладкам со счётчиками.
  await screen.findByRole('radio', { name: 'Предложены — 394' });
  return recorded;
}

describe('рассмотрение прогона', () => {
  it('у каждого кандидата ярлык судьи, и «не смотрел» — тоже ярлык', async () => {
    await openReview();

    const judged = screen.getByText('martech.example.test').closest('tr') as HTMLElement;
    expect(within(judged).getByText('площадка')).toBeInTheDocument();
    expect(within(judged).getByText('издание')).toBeInTheDocument();
    expect(within(judged).getByText(/we accept guest posts/)).toBeInTheDocument();

    const blind = screen.getByText('unjudged.example.test').closest('tr') as HTMLElement;
    expect(within(blind).getByText('судья не смотрел')).toBeInTheDocument();
  });

  it('продающий размещение сайт говорит, на чём держится его место наверху', async () => {
    await openReview({
      [PENDING]: {
        body: {
          ...VIEW,
          rows: [
            card(1, 'martech.example.test', {
              sells: 'меню главной: «Advertise»',
              sells_by: 'door',
            }),
            card(2, 'plain.example.test'),
          ],
        },
      },
    });

    const seller = screen.getByText('martech.example.test').closest('tr') as HTMLElement;
    expect(within(seller).getByText('продаёт размещение')).toBeInTheDocument();
    expect(within(seller).getByText('меню главной: «Advertise»')).toBeInTheDocument();
    const plain = screen.getByText('plain.example.test').closest('tr') as HTMLElement;
    expect(within(plain).queryByText('продаёт размещение')).not.toBeInTheDocument();
  });

  it('видно, по каким ключам нашёлся домен', async () => {
    await openReview();

    const row = screen.getByText('martech.example.test').closest('tr') as HTMLElement;
    expect(within(row).getByText('saas blog write for us')).toBeInTheDocument();
    expect(within(row).getByText('и ещё 1')).toBeInTheDocument();
  });

  it('сомнительные скрыты со счётчиком, а не выброшены', async () => {
    const recorded = await openReview({
      'GET /api/review/runs/18?status=pending&show_doubtful=true': { body: VIEW },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Показать сомнительные (скрыто 278)'));

    expect(recorded.calls.some((call: Call) => call.path.endsWith('show_doubtful=true'))).toBe(
      true,
    );
  });

  it('решение пачкой уходит ровно теми, кого отметил человек', async () => {
    const recorded = await openReview({
      'POST /api/review/runs/18/decide': {
        body: { changed: 2, accepted: 2, contacts_job_id: 'j' },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Выбрать martech.example.test'));
    await user.click(screen.getByLabelText('Выбрать unjudged.example.test'));
    await user.click(screen.getByRole('button', { name: 'Принять выбранные' }));

    const call = recorded.calls.find((one: Call) => one.path.endsWith('/decide'));
    expect(call?.body).toEqual({ candidate_ids: [1, 2], decision: 'accepted' });
  });

  it('отказ по одной строке уходит одним кандидатом', async () => {
    const recorded = await openReview({
      'POST /api/review/runs/18/decide': {
        body: { changed: 1, accepted: 0, contacts_job_id: null },
      },
    });
    const user = userEvent.setup();

    const row = screen.getByText('martech.example.test').closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Отклонить' }));

    const call = recorded.calls.find((one: Call) => one.path.endsWith('/decide'));
    expect(call?.body).toEqual({ candidate_ids: [1], decision: 'rejected' });
  });

  it('готовность к автоприёму названа вместе с условием', async () => {
    await openReview();

    expect(screen.getByText('рано')).toBeInTheDocument();
    expect(screen.getByText('нужно ≥ 95% на ≥ 200')).toBeInTheDocument();
  });

  it('без права решать кнопок нет, а очередь видна', async () => {
    const viewer = { ...ADMIN, permissions: ['view'] };
    await openReview({}, viewer as typeof ADMIN);

    expect(screen.queryByRole('button', { name: 'Принять' })).not.toBeInTheDocument();
    expect(screen.getAllByText('ждёт решения')).toHaveLength(2);
  });
});

describe('передумать', () => {
  const decided = (status: 'accepted' | 'rejected') => ({
    ...VIEW,
    rows: [card(1, 'martech.example.test', { status, decided_by: 'оператор@site.com' })],
    counts: {
      pending: 393,
      accepted: status === 'accepted' ? 1 : 0,
      rejected: status === 'rejected' ? 1 : 0,
    },
  });

  it.each(['accepted', 'rejected'] as const)(
    'со вкладки «%s» решение возвращается в «предложен»',
    async (status) => {
      const recorded = await openReview({
        [`GET /api/review/runs/18?status=${status}`]: { body: decided(status) },
        'POST /api/review/runs/18/decide': {
          body: { changed: 1, accepted: 0, contacts_job_id: null },
        },
      });
      const user = userEvent.setup();

      await user.click(
        screen.getByText(new RegExp(`^${status === 'accepted' ? 'Приняты' : 'Отклонены'} —`)),
      );
      // Пока вкладка едет, видны строки прежней — кнопки «Вернуть» у них нет.
      await user.click(await screen.findByRole('button', { name: 'Вернуть' }));

      const call = recorded.calls.find((one: Call) => one.path.endsWith('/decide'));
      expect(call?.body).toEqual({ candidate_ids: [1], decision: 'pending' });
    },
  );
});

describe('что дали ключи прогона', () => {
  it('прогон без разметки ключей: колонки нет, а сказано это наверху', async () => {
    // Аудит 25.09.2026: у прогона №18 колонка «Нашёлся по ключам» была
    // столбцом прочерков, а пояснение стояло внизу, после полусотни строк.
    await openReview({ [PENDING]: { body: { ...VIEW, keywords: null } } });

    expect(screen.queryByRole('columnheader', { name: 'Нашёлся по ключам' })).toBeNull();
    const said = screen.getByText(/этот прогон не хранит/);
    const table = screen.getByRole('table');
    // Пояснение — выше таблицы, а не под ней.
    expect(said.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Что дали ключи прогона' })).toBeNull();
  });

  it('прогон с разметкой ключей: колонка есть', async () => {
    await openReview();

    expect(screen.getByRole('columnheader', { name: 'Нашёлся по ключам' })).toBeInTheDocument();
    expect(screen.queryByText(/этот прогон не хранит/)).toBeNull();
  });

  it('сводка видна сразу, таблица — по кнопке', async () => {
    await openReview({
      [PENDING]: {
        body: {
          ...VIEW,
          keywords: [
            {
              keyword: 'saas blog write for us',
              found: 12,
              queued: 5,
              accepted: 2,
              rejected: 1,
              pending: 2,
              runs: 1,
            },
            {
              keyword: 'best crm software',
              found: 30,
              queued: 4,
              accepted: 0,
              rejected: 4,
              pending: 0,
              runs: 1,
            },
            {
              keyword: 'empty key',
              found: 0,
              queued: 0,
              accepted: 0,
              rejected: 0,
              pending: 0,
              runs: 1,
            },
          ],
        },
      },
    });
    const user = userEvent.setup();

    expect(
      screen.getByText('Принятых дали 1 из 3 ключей, только отказы — 1, ничего не нашли — 1.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('best crm software')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Что дали ключи прогона' }));

    expect(screen.getByText('best crm software')).toBeInTheDocument();
  });
});

describe('«продаёт размещение» — один раз в строке', () => {
  // Аудит 25.09.2026: значок у домена, пояснение «судья: продаёт размещение
  // у себя» и ярлык судьи — одно и то же трижды. Признак стоит в колонке
  // своего источника.
  const JUDGE_SELLS = { ...MACHINE, intent: 'sells_placement' };

  function times(host: string, text: string): number {
    const row = screen.getByText(host).closest('tr') as HTMLElement;
    return within(row).queryAllByText(text).length;
  }

  it('сказал судья — только ярлык судьи', async () => {
    await openReview({
      [PENDING]: {
        body: {
          ...VIEW,
          rows: [
            card(1, 'judged.example.test', {
              machine: JUDGE_SELLS,
              sells: 'судья: продаёт размещение у себя',
              sells_by: 'judge',
            }),
          ],
        },
      },
    });

    expect(times('judged.example.test', 'продаёт размещение')).toBe(1);
    expect(screen.queryByText(/судья: продаёт размещение у себя/)).toBeNull();
  });

  it('сказал сам сайт — только в «Донор ответил»', async () => {
    await openReview({
      [PENDING]: {
        body: {
          ...VIEW,
          rows: [
            card(1, 'answered.example.test', {
              seller: { answer: 'sells', answered_at: null, price: null, currency: null },
              sells: 'сам сказал: продаёт размещение',
              sells_by: 'answer',
            }),
          ],
        },
      },
    });

    expect(times('answered.example.test', 'продаёт размещение')).toBe(0);
    expect(times('answered.example.test', 'продаёт')).toBe(1);
    expect(screen.queryByText(/сам сказал/)).toBeNull();
  });

  it('решил человек — у домена, словами, без повтора', async () => {
    await openReview({
      [PENDING]: {
        body: {
          ...VIEW,
          rows: [
            card(1, 'human.example.test', {
              sells: 'человек: продаёт размещение у себя',
              sells_by: 'human',
            }),
          ],
        },
      },
    });

    expect(times('human.example.test', 'продаёт размещение')).toBe(1);
    expect(times('human.example.test', 'так решил человек')).toBe(1);
  });
});

describe('возврат к прогонам', () => {
  it('«К прогонам» стоит над заголовком и ведёт к истории', async () => {
    await openReview();

    const back = screen.getByRole('link', { name: 'К прогонам' });
    expect(back).toHaveAttribute('href', '/run');
    const title = screen.getByRole('heading', { name: 'Прогон №18: рассмотрение' });
    expect(back.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('пришли со второй страницы истории — на неё и возвращает', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const queued = {
      id: 18,
      status: 'done',
      country: 'us',
      keywords: 100,
      estimated_units: 1,
      actual_units: 1,
      estimate_error: 0,
      stats: null,
      started_at: '2026-09-23T16:21:00Z',
      alive_at: '2026-09-23T16:40:00Z',
      hosts: 535,
      reviewed: 0,
      disagreements: 0,
      queue: { pending: 394 },
      reason: null,
    };
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/runs/countries': { body: ['us'] },
      'GET /api/keywords/yield?country=us': { body: [] },
      'GET /api/runs?page=2': {
        body: { runs: [queued], total: 12, page: 2, limit: 10, workers: 1, queued: 0 },
      },
      [PENDING]: { body: VIEW },
      'GET /api/review/accuracy': { body: ACCURACY },
    });
    renderWith(<AppRoutes />, '/run?page=2');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('link', { name: 'Рассмотреть 394' }));
    const back = await screen.findByRole('link', { name: 'К прогонам' });
    expect(back).toHaveAttribute('href', '/run?page=2');
    await user.click(back);

    const pager = await screen.findByRole('navigation', { name: 'Страницы истории прогонов' });
    expect(within(pager).getByRole('button', { name: 'Страница 2' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(recorded.calls.some((call: Call) => call.path === '/api/runs?page=1')).toBe(false);
  });
});

describe('смена вкладки — не перезагрузка', () => {
  // Замечание 25.09.2026: переключение «Предложены / Приняты / Отклонены»
  // выглядело перезагрузкой — весь экран заменялся крутилкой. Верх экрана
  // должен стоять, прежние строки — быть видны до прихода новых.
  it('пока едет новая вкладка, верх экрана и прежние строки на месте', async () => {
    const accepted = {
      ...VIEW,
      rows: [card(5, 'taken.example.test', { status: 'accepted', decided_by: 'оператор' })],
      counts: { pending: 393, accepted: 1, rejected: 0 },
    };
    await openReview({ 'GET /api/review/runs/18?status=accepted': { body: accepted } });
    // Ответ по «Приняты» задерживается, пока тест его не отпустит.
    const served = vi.mocked(globalThis.fetch).getMockImplementation();
    if (served === undefined) throw new Error('сеть не подменена');
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (urlOf(input).includes('status=accepted')) await gate;
      return served(input, init);
    });
    const user = userEvent.setup();

    await user.click(screen.getByText('Приняты — 0'));

    // Ни крутилки вместо экрана, ни пропавшего верха.
    expect(screen.queryByLabelText('Загружаем очередь')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Прогон №18: рассмотрение' })).toBeInTheDocument();
    expect(screen.getByText('рано')).toBeInTheDocument();
    // Счётчики во вкладках не мигают — прежние числа стоят.
    expect(screen.getByRole('radio', { name: 'Предложены — 394' })).toBeInTheDocument();
    // Прежние строки видны, но приглушены и решать по ним нельзя.
    const row = screen.getByText('martech.example.test').closest('tr') as HTMLElement;
    expect(row.closest('[data-stale]')).not.toBeNull();
    expect(within(row).getByRole('button', { name: 'Принять' })).toBeDisabled();

    release();

    expect(await screen.findByText('taken.example.test')).toBeInTheDocument();
    expect(screen.queryByText('martech.example.test')).toBeNull();
    expect(document.querySelector('[data-stale]')).toBeNull();
    expect(screen.getByRole('radio', { name: 'Приняты — 1' })).toBeInTheDocument();
  });
});

describe('номер прогона из адреса', () => {
  it('не номер — «такого прогона нет» словами, без запроса с NaN', async () => {
    // Аудит 25.09.2026: `/runs/abc/review` уходил на сервер с `NaN`, и экран
    // показывал «Input should be a valid integer…».
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({ 'GET /api/auth/me': { body: ADMIN } });
    renderWith(<AppRoutes />, '/runs/abc/review');

    expect(await screen.findByRole('heading', { name: 'Такого прогона нет' })).toBeVisible();
    expect(screen.getByText(/«abc» в адресе — не номер прогона/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'К прогонам' })).toHaveAttribute('href', '/run');
    expect(recorded.calls.map((call: Call) => call.path)).toEqual(['/api/auth/me']);
  });

  it('номер больше, чем бывает, — так же, без запроса', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({ 'GET /api/auth/me': { body: ADMIN } });
    renderWith(<AppRoutes />, '/runs/99999999999/review');

    expect(await screen.findByRole('heading', { name: 'Такого прогона нет' })).toBeVisible();
    expect(recorded.calls.map((call: Call) => call.path)).toEqual(['/api/auth/me']);
  });

  it('такого нет в базе — словами сервера и со ссылкой назад', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/review/runs/999?status=pending': {
        status: 404,
        body: { detail: 'Прогона №999 нет' },
      },
      'GET /api/review/accuracy': { body: ACCURACY },
    });
    renderWith(<AppRoutes />, '/runs/999/review');

    expect(await screen.findByRole('heading', { name: 'Такого прогона нет' })).toBeVisible();
    expect(screen.getByText(/Прогона №999 нет\./)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'К прогонам' })).toBeInTheDocument();
  });
});
