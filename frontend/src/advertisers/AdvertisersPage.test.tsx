/**
 * Ручная проверка кандидатов в рекламодатели.
 *
 * Экран сделан ради одного числа: допуск по ложным рекламодателям —
 * десять процентов, а скоринг судит по признакам, выведенным из замера
 * на одной нише. Поэтому проверяется не «рисуется ли таблица», а то,
 * ради чего она нарисована: видны ли причины балла, видна ли ссылка,
 * под которую будет написано письмо, и уходит ли решение на сервер.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, CRAWL_ROUTES, OPERATOR, reviewQueue, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

/** Адрес запроса из того, что отдали в `fetch`. */
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  return input instanceof URL ? input.href : input.url;
}
const CANDIDATE = {
  id: 12,
  donor_host: 'donor.example.test',
  target_root: 'advertiser.example',
  points: 3,
  verdict: 'pending' as const,
  reasons: ['коммерческий анкор +1', 'коммерческий анкор под nofollow +2'],
  links: 2,
  pages: 2,
  best_page_url: 'https://donor.example.test/post/1',
  best_anchor: 'Bet now',
  confirmed: null,
  decided_by: null,
  decided_at: null,
};

const QUEUE = reviewQueue({
  rows: [CANDIDATE],
  waiting: 1,
  counts: { bought: 6, pending: 1, skipped: 14, blocked: 12 },
});

async function openScreen(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    ...CRAWL_ROUTES,
    'GET /api/auth/me': { body: who },
    'GET /api/advertisers/niche?page=1': {
      body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
    },
    'GET /api/advertisers': { body: QUEUE },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/advertisers?view=review');
  await screen.findByText('advertiser.example');
  return recorded;
}

describe('ручная проверка рекламодателей', () => {
  it('на телефоне строка — карточкой: «Пишем» и «Не пишем» под доменом', async () => {
    await openScreen();

    // Кнопки решения стояли последней колонкой — на телефоне за краем экрана.
    const decide = screen.getByRole('button', { name: 'Пишем' }).closest('td')!;
    expect(decide).toHaveClass('cellDecide');
    expect(decide.closest('.phoneCards')).not.toBeNull();
    const labels = [...decide.closest('tr')!.querySelectorAll('td[data-label]')];
    expect(labels.map((cell) => cell.getAttribute('data-label'))).toEqual([
      'Донор',
      'Балл',
      'За что',
      'Ссылка',
    ]);
  });

  it('показывает, из чего сложился балл', async () => {
    await openScreen();

    // Человек решает не по числу «3», а по тому, из чего оно сложилось:
    // «ссылки с двадцати страниц» и «анкор под nofollow» — разные
    // основания, и одно из них бывает ошибкой.
    expect(screen.getByText('коммерческий анкор под nofollow +2')).toBeInTheDocument();
    expect(screen.getByText('коммерческий анкор +1')).toBeInTheDocument();
  });

  it('ссылка, под которую напишут письмо, видна рядом с решением', async () => {
    await openScreen();

    expect(screen.getByRole('link', { name: 'страница' })).toHaveAttribute(
      'href',
      'https://donor.example.test/post/1',
    );
    expect(screen.getByText(/Bet now/)).toBeInTheDocument();
  });

  it('совет судьи — только под решением человека', async () => {
    // Сервер даёт совет только решённой строке (`crawl/judge_advice.py`): до решения он тянул
    // бы решение за собой, а по решениям меряется и скоринг, и сам судья.
    const said = {
      advice: 'skip' as const,
      words: 'ссылка — источник факта, раскрытия рекламы нет',
      link_quote: 'according to the report by the agency',
      target_quote: '',
      disclosure: '',
      prompt_version: 'advertiser-judge-v1',
      model: 'gpt-5-mini',
      judged_at: '2026-11-03T12:00:00Z',
    };
    const decided = { ...CANDIDATE, confirmed: false, decided_by: 'оператор@site.com' };
    await openScreen({
      'GET /api/advertisers': {
        body: {
          ...QUEUE,
          total: 2,
          rows: [
            { ...decided, judge: said },
            { ...CANDIDATE, id: 13, target_root: 'open.example', judge: null },
          ],
        },
      },
    });

    expect(
      screen.getByText(
        'судья: не пишем — ссылка — источник факта, раскрытия рекламы нет · «according to the report by the agency»',
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/^судья:/)).toHaveLength(1);
  });

  it('счётчики показывают и отсеянных', async () => {
    await openScreen();

    // По отсеянным видно, что список «кому не пишем» работает, а не молчит.
    expect(screen.getByText(/кому не пишем: 12/)).toBeInTheDocument();
    expect(screen.getByText(/куплена: 6/)).toBeInTheDocument();
  });

  it('«пишем» уходит на сервер', async () => {
    const recorded = await openScreen({
      'POST /api/advertisers/12/decide': { body: { ...CANDIDATE, confirmed: true } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Пишем' }));

    await screen.findByText('advertiser.example: пишем ему');
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent[0]?.body).toEqual({ confirmed: true, force: false });
  });

  it('«не пишем» уходит тем же путём', async () => {
    const recorded = await openScreen({
      'POST /api/advertisers/12/decide': { body: { ...CANDIDATE, confirmed: false } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не пишем' }));

    // Матчер точный: «не пишем» есть и на кнопке, и в счётчике
    // «кому не пишем» — по общему тексту тест нашёл бы три места.
    await screen.findByText('advertiser.example: не пишем');
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent[0]?.body).toEqual({ confirmed: false, force: false });
  });

  it('отказ сервера виден человеку, а не тонет', async () => {
    await openScreen({
      'POST /api/advertisers/12/decide': { status: 409, body: { detail: 'решение уже принято' } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Пишем' }));

    expect(await screen.findByText(/Не записали/)).toBeInTheDocument();
  });

  it('оператор решает — это его работа', async () => {
    await openScreen({}, OPERATOR);

    expect(screen.getByRole('button', { name: 'Пишем' })).toBeInTheDocument();
  });

  it('без права решения видно только список', async () => {
    await openScreen({}, { ...(OPERATOR as object), permissions: ['advertisers', 'view'] });

    expect(screen.getByText('advertiser.example')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Пишем' })).not.toBeInTheDocument();
  });

  it('пустая очередь объясняет, кто сюда попадает', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      ...CRAWL_ROUTES,
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/advertisers/niche?page=1': {
        body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
      },
      'GET /api/advertisers': { body: reviewQueue() },
    });
    renderWith(<AppRoutes />, '/advertisers?view=review');

    expect(await screen.findByText(/Спорных нет/)).toBeInTheDocument();
  });
});

describe('числа и текст сходятся', () => {
  const DECIDED = { ...CANDIDATE, confirmed: true, decided_by: 'админ@site.com' };

  /** Все спорные решены: в очереди пусто, решённый — только под «Показывать решённые». */
  function openAllDecided() {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      ...CRAWL_ROUTES,
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/advertisers/niche?page=1': {
        body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
      },
      'GET /api/advertisers': { body: reviewQueue({ counts: QUEUE.counts }) },
      'GET /api/advertisers?include_decided=true': { body: { ...QUEUE, rows: [DECIDED] } },
    });
    renderWith(<AppRoutes />, '/advertisers?view=review');
  }

  it('«спорно: 1» при решённом спорном — не «спорных нет», а сколько решено и где их видно', async () => {
    openAllDecided();
    const user = userEvent.setup();

    expect(await screen.findByText('Все спорные решены — 1.')).toBeInTheDocument();
    expect(screen.queryByText(/Спорных нет/)).not.toBeInTheDocument();
    expect(screen.getByText(/спорно: 1/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Показать решённые' }));

    expect(await screen.findByText('advertiser.example')).toBeInTheDocument();
    expect(screen.getByText(/пишем — админ@site.com/)).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Показывать решённые' })).toBeChecked();
  });

  it('развёрнутые решённые сворачиваются там же, где развернули', async () => {
    openAllDecided();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Показать решённые' }));
    await user.click(await screen.findByRole('button', { name: 'Скрыть решённые' }));

    expect(await screen.findByText('Все спорные решены — 1.')).toBeInTheDocument();
    expect(screen.queryByText('advertiser.example')).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Показывать решённые' })).not.toBeChecked();
  });

  it('причины — строками, без маркеров списка', async () => {
    await openScreen();

    const reason = screen.getByText('коммерческий анкор +1');
    expect(reason.closest('li')).toBeNull();
  });
});

describe('переключатель решённых', () => {
  it('не перерисовывает экран: шапка стоит, прежняя таблица — приглушённой до ответа', async () => {
    await openScreen();
    const answered = vi.mocked(globalThis.fetch).getMockImplementation()!;
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (urlOf(input).includes('include_decided=true')) {
        await gate;
        return new Response(JSON.stringify(QUEUE), {
          headers: { 'content-type': 'application/json' },
        });
      }
      return answered(input, init);
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Показывать решённые' }));

    expect(screen.getByRole('heading', { name: 'Рекламодатели: спорные' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Загружаем очередь проверки')).not.toBeInTheDocument();
    expect(screen.getByText('advertiser.example').closest('[data-stale]')).not.toBeNull();

    release();

    await waitFor(() => expect(document.querySelector('[data-stale]')).toBeNull());
    expect(screen.getByText('advertiser.example')).toBeInTheDocument();
  });
});

describe('слова при числах', () => {
  it('«1 ссылка с 1 страницы», а не «ссылок 1 с 1 страниц»', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      ...CRAWL_ROUTES,
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/advertisers/niche?page=1': {
        body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
      },
      'GET /api/advertisers': {
        body: { ...QUEUE, rows: [{ ...CANDIDATE, links: 1, pages: 1 }] },
      },
    });
    renderWith(<AppRoutes />, '/advertisers?view=review');

    expect(await screen.findByText('1 ссылка с 1 страницы')).toBeInTheDocument();
  });
});

describe('порядок блоков — порядок работы (слово владельца 13.10.2026)', () => {
  it('бизнесы ниши — под очередью спорных, «К письму» — последним', async () => {
    await openScreen();

    const queue = screen.getByRole('heading', { name: 'Рекламодатели: спорные' });
    const niche = await screen.findByRole('heading', { name: 'Бизнесы ниши из выдачи' });
    const letter = await screen.findByRole('heading', { name: 'К письму' });
    // Перевод и поиск адресов — после всех решений выше: и по спорным, и по бизнесам ниши.
    expect(queue.compareDocumentPosition(niche) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(niche.compareDocumentPosition(letter) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

/** Где сейчас экран: адрес целиком — по нему видно, что живёт в адресе. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

/** Строка очереди с номером `id`: домен — по номеру. */
function candidate(id: number) {
  return { ...CANDIDATE, id, target_root: `row-${id}.example` };
}

/** Страница очереди, какой её отдаёт сервер: `size` строк с номера `from`, всего `total`. */
function queuePage(size: number, { from = 1, total = size, number = 1 } = {}) {
  const rows = Array.from({ length: size }, (_, index) => candidate(from + index));
  return {
    body: {
      ...QUEUE,
      rows,
      total,
      page: number,
      waiting: total,
      facets: FACETS,
    },
  };
}

const FACETS = {
  listed: 40,
  points: {
    total: 40,
    choices: [
      { key: '2', title: '2', count: 28 },
      { key: '3', title: '3', count: 12 },
    ],
  },
  features: {
    total: 40,
    choices: [
      { key: 'anchor', title: 'коммерческий анкор', count: 31 },
      { key: 'pages', title: 'на многих страницах донора', count: 9 },
    ],
  },
};

const PAGER = 'Страницы очереди проверки';

async function openQueue(
  routes: Record<string, unknown>,
  path = '/advertisers?view=review',
  first = 'row-1.example',
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    ...CRAWL_ROUTES,
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/advertisers/niche?page=1': {
      body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
    },
    ...(routes as Record<string, never>),
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
  await screen.findByText(first);
  return recorded;
}

/** Запросы очереди — без соседних карточек: у тех свои адреса. */
function queueCalls(recorded: { calls: Call[] }): string[] {
  return recorded.calls
    .filter((call) => call.method === 'GET' && /^\/api\/advertisers(\?|$)/.test(call.path))
    .map((call) => call.path);
}

/** Пункт списка одного поля: на строке фильтров их два. */
async function choose(fieldName: string, option: string) {
  const user = userEvent.setup();
  const field = screen.getByRole('textbox', { name: fieldName });
  await user.click(field);
  const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
  await user.click(await list.findByRole('option', { name: option, hidden: true }));
}

describe('страницы очереди', () => {
  it('одна страница — переключателя нет вовсе', async () => {
    await openQueue({ 'GET /api/advertisers': queuePage(10) });

    expect(screen.queryByRole('navigation', { name: PAGER })).not.toBeInTheDocument();
  });

  it('следующая страница спрашивается у сервера, а номер встаёт в адрес', async () => {
    const recorded = await openQueue({
      'GET /api/advertisers': queuePage(10, { total: 25 }),
      'GET /api/advertisers?page=2': queuePage(10, { from: 11, total: 25, number: 2 }),
    });
    const user = userEvent.setup();

    // Страниц три — по размеру, который назвал сервер.
    const pager = screen.getByRole('navigation', { name: PAGER });
    expect(within(pager).getByRole('button', { name: 'Страница 3' })).toBeInTheDocument();
    await user.click(within(pager).getByRole('button', { name: 'Страница 2' }));

    expect(await screen.findByText('row-11.example')).toBeInTheDocument();
    expect(screen.queryByText('row-1.example')).not.toBeInTheDocument();
    expect(queueCalls(recorded)).toEqual(['/api/advertisers', '/api/advertisers?page=2']);
    expect(screen.getByTestId('where')).toHaveTextContent('/advertisers?view=review&page=2');
  });

  it('страница по ссылке открывается своей', async () => {
    const recorded = await openQueue(
      { 'GET /api/advertisers?page=2': queuePage(5, { from: 11, total: 15, number: 2 }) },
      '/advertisers?view=review&page=2',
      'row-11.example',
    );

    expect(queueCalls(recorded)).toEqual(['/api/advertisers?page=2']);
    expect(
      within(screen.getByRole('navigation', { name: PAGER })).getByRole('button', {
        name: 'Страница 2',
      }),
    ).toHaveAttribute('data-active', 'true');
  });

  it('решена последняя строка последней страницы — на новую последнюю, а не пустой экран', async () => {
    let decided = false;
    const recorded = await openQueue(
      {
        'GET /api/advertisers': queuePage(10, { total: 10 }),
        'GET /api/advertisers?page=2': () =>
          decided
            ? // Строка ушла из очереди: сервер отдаёт страницу пустой и называет, сколько всего.
              queuePage(0, { total: 10, number: 2 })
            : queuePage(1, { from: 11, total: 11, number: 2 }),
        'POST /api/advertisers/11/decide': () => {
          decided = true;
          return { body: { ...candidate(11), confirmed: false } };
        },
      },
      '/advertisers?view=review&page=2',
      'row-11.example',
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не пишем' }));

    expect(await screen.findByText('row-1.example')).toBeInTheDocument();
    expect(queueCalls(recorded).at(-1)).toBe('/api/advertisers');
    expect(screen.getByTestId('where')).toHaveTextContent(/view=review$/);
    expect(screen.queryByText(/Все спорные решены|Спорных нет/)).not.toBeInTheDocument();
  });

  it('пока едет новая страница, прежние строки приглушены, решать по ним нельзя', async () => {
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await openQueue({ 'GET /api/advertisers': queuePage(10, { total: 25 }) });
    const answered = vi.mocked(globalThis.fetch).getMockImplementation()!;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (!urlOf(input).endsWith('/api/advertisers?page=2')) return answered(input, init);
      await gate;
      return new Response(JSON.stringify(queuePage(10, { from: 11, total: 25, number: 2 }).body), {
        headers: { 'content-type': 'application/json' },
      });
    });
    const user = userEvent.setup();

    await user.click(
      within(screen.getByRole('navigation', { name: PAGER })).getByRole('button', {
        name: 'Страница 2',
      }),
    );

    const row = screen.getByText('row-1.example').closest('tr')!;
    expect(row.closest('[data-stale]')).not.toBeNull();
    expect(within(row).getByRole('button', { name: 'Пишем' })).toBeDisabled();
    expect(within(row).getByRole('button', { name: 'Не пишем' })).toBeDisabled();
    // Шапка с фильтрами и переключатель — не приглушены: строки ещё едут, а поля на месте.
    expect(screen.getByRole('textbox', { name: 'Балл' }).closest('[data-stale]')).toBeNull();
    expect(screen.getByRole('navigation', { name: PAGER }).closest('[data-stale]')).toBeNull();

    release();

    expect(await screen.findByText('row-11.example')).toBeInTheDocument();
    expect(document.querySelector('[data-stale]')).toBeNull();
  });

  it('страницы бизнесов ниши листание очереди не теряет', async () => {
    await openQueue(
      {
        'GET /api/advertisers': queuePage(10, { total: 25 }),
        'GET /api/advertisers?page=2': queuePage(10, { from: 11, total: 25, number: 2 }),
        'GET /api/advertisers/niche?page=2': {
          body: { rows: [], waiting: 40, total: 40, page: 2, limit: 20 },
        },
      },
      '/advertisers?view=review&niche_page=2',
    );
    const user = userEvent.setup();

    await user.click(
      within(screen.getByRole('navigation', { name: PAGER })).getByRole('button', {
        name: 'Страница 2',
      }),
    );

    await screen.findByText('row-11.example');
    expect(screen.getByTestId('where')).toHaveTextContent(
      '/advertisers?view=review&page=2&niche_page=2',
    );
  });
});

describe('смена условий — первая страница', () => {
  const ROUTES = {
    'GET /api/advertisers?page=3': queuePage(5, { from: 21, total: 25, number: 3 }),
  };
  const THIRD = '/advertisers?view=review&page=3';

  it('«Показывать решённые»', async () => {
    const recorded = await openQueue(
      { ...ROUTES, 'GET /api/advertisers?include_decided=true': queuePage(10, { total: 40 }) },
      THIRD,
      'row-21.example',
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('switch', { name: 'Показывать решённые' }));

    expect(await screen.findByText('row-1.example')).toBeInTheDocument();
    expect(queueCalls(recorded).at(-1)).toBe('/api/advertisers?include_decided=true');
    expect(screen.getByTestId('where')).toHaveTextContent(
      '/advertisers?view=review&include_decided=true',
    );
  });

  it('фильтр', async () => {
    const recorded = await openQueue(
      { ...ROUTES, 'GET /api/advertisers?points=3': queuePage(10, { total: 12 }) },
      THIRD,
      'row-21.example',
    );

    await choose('Балл', '3 · 12');

    expect(await screen.findByText('row-1.example')).toBeInTheDocument();
    expect(queueCalls(recorded).at(-1)).toBe('/api/advertisers?points=3');
    expect(screen.getByTestId('where')).toHaveTextContent('/advertisers?view=review&points=3');
  });

  it('список: «куплено» — первая страница и без фильтров спорных', async () => {
    const recorded = await openQueue(
      {
        'GET /api/advertisers?points=3&page=2': queuePage(2, { from: 11, total: 12, number: 2 }),
        'GET /api/advertisers?verdict=bought': queuePage(10, { total: 30 }),
      },
      '/advertisers?view=review&points=3&page=2',
      'row-11.example',
    );
    const user = userEvent.setup();

    await user.click(screen.getByText(/^Куплено ·/));

    expect(await screen.findByText('row-1.example')).toBeInTheDocument();
    expect(queueCalls(recorded).at(-1)).toBe('/api/advertisers?verdict=bought');
    expect(screen.getByRole('heading', { name: 'Рекламодатели: куплено' })).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent(
      '/advertisers?view=review&verdict=bought',
    );
  });
});

describe('фильтры под колонками', () => {
  it('поля — под своими заголовками: «Балл» и «За что»', async () => {
    await openQueue({ 'GET /api/advertisers': queuePage(1, { total: 1 }) });

    const heads = screen.getAllByRole('columnheader').slice(0, 6);
    expect(heads.map((head) => head.textContent)).toEqual([
      'Кому ссылается',
      'Донор',
      'Балл',
      'За что',
      'Ссылка',
      '',
    ]);
    const filters = screen.getByRole('textbox', { name: 'Балл' }).closest('tr')!;
    expect(filters).toHaveClass('filterRow');
    const cells = [...filters.querySelectorAll('th')];
    // Те же места, что у заголовков: пустые — у домена, донора, ссылки и кнопок.
    expect(cells.map((cell) => cell.getAttribute('data-label'))).toEqual([
      null,
      null,
      'Балл',
      'За что',
      null,
      null,
    ]);
    expect(within(cells[3]!).getByRole('textbox', { name: 'За что' })).toBeInTheDocument();
  });

  it('варианты и числа — от сервера, слова признаков — те же, что у разбивки', async () => {
    const recorded = await openQueue({
      'GET /api/advertisers': queuePage(10, { total: 40 }),
      'GET /api/advertisers?feature=pages': queuePage(9, { total: 9 }),
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('textbox', { name: 'За что' }));
    const field = screen.getByRole('textbox', { name: 'За что' });
    const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
    // Выбранный пункт — с галочкой, как во всех списках: иначе пункты стоят лесенкой.
    expect(
      list.getByRole('option', { name: 'все · 40', hidden: true }).querySelector('svg'),
    ).not.toBeNull();
    expect(
      list.getByRole('option', { name: 'коммерческий анкор · 31', hidden: true }),
    ).toBeInTheDocument();
    await user.click(
      list.getByRole('option', { name: 'на многих страницах донора · 9', hidden: true }),
    );

    await waitFor(() => expect(queueCalls(recorded).at(-1)).toBe('/api/advertisers?feature=pages'));
    expect(await screen.findByText('найдено 9 из 40')).toBeInTheDocument();
    // В поле — только слово: число меняется с соседним фильтром.
    expect(screen.getByRole('textbox', { name: 'За что' })).toHaveValue(
      'на многих страницах донора',
    );
    expect(screen.getByTestId('where')).toHaveTextContent('feature=pages');
  });

  it('числа в шапке от фильтра не меняются', async () => {
    await openQueue({
      'GET /api/advertisers': queuePage(10, { total: 40 }),
      'GET /api/advertisers?points=3': {
        // Шапка — по всем: сервер отдаёт те же числа, сузилась только таблица.
        body: { ...queuePage(10, { total: 12 }).body, waiting: 40 },
      },
    });

    await choose('Балл', '3 · 12');

    expect(await screen.findByText('найдено 12 из 40')).toBeInTheDocument();
    expect(screen.getByText(/Ждут человека/)).toHaveTextContent('Ждут человека: 40');
    expect(screen.getByText(/куплена: 6/)).toBeInTheDocument();
  });

  it('под фильтром пусто — поля на месте, сказано почему и как сбросить', async () => {
    const recorded = await openQueue(
      {
        'GET /api/advertisers': queuePage(10, { total: 40 }),
        'GET /api/advertisers?points=2&feature=pages': queuePage(0, { total: 0 }),
      },
      '/advertisers?view=review&points=2&feature=pages',
      'Под фильтр ничего не попало.',
    );
    const user = userEvent.setup();

    expect(
      screen.getByText('Условия: балл 2, за что: на многих страницах донора. Всего в списке — 40.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Балл' })).toHaveValue('2');
    expect(screen.queryByText(/Спорных нет/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));

    expect(await screen.findByText('row-1.example')).toBeInTheDocument();
    expect(queueCalls(recorded).at(-1)).toBe('/api/advertisers');
    expect(screen.getByTestId('where')).toHaveTextContent(/view=review$/);
  });

  it('сброс фильтра: пока едет весь список, «спорных нет» и «ничего не попало» не мелькают', async () => {
    await openQueue(
      {
        'GET /api/advertisers?points=2&feature=pages': queuePage(0, { total: 0 }),
        'GET /api/advertisers': queuePage(10, { total: 40 }),
      },
      '/advertisers?view=review&points=2&feature=pages',
      'Под фильтр ничего не попало.',
    );
    const answered = vi.mocked(globalThis.fetch).getMockImplementation()!;
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (urlOf(input).endsWith('/api/advertisers')) await gate;
      return answered(input, init);
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));

    expect(screen.getByTestId('where')).toHaveTextContent(/view=review$/);
    expect(screen.queryByText(/Спорных нет|Все спорные решены/)).not.toBeInTheDocument();
    expect(screen.queryByText('Под фильтр ничего не попало.')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Балл' })).toHaveValue('все');

    release();

    expect(await screen.findByText('row-1.example')).toBeInTheDocument();
  });

  it('адрес — чужой ввод: кривое значение не сужает и на сервер не уходит', async () => {
    const recorded = await openQueue(
      { 'GET /api/advertisers': queuePage(10, { total: 40 }) },
      '/advertisers?view=review&points=abc&feature=%3Cb%3E&verdict=zzz&page=-4',
    );

    expect(queueCalls(recorded)).toEqual(['/api/advertisers']);
    expect(screen.getByRole('textbox', { name: 'Балл' })).toHaveValue('все');
    expect(screen.getByRole('heading', { name: 'Рекламодатели: спорные' })).toBeInTheDocument();
  });

  it('значение из адреса, которого нет в списке, видно в поле: иначе «все» при суженном списке', async () => {
    await openQueue(
      { 'GET /api/advertisers?points=7': queuePage(0, { total: 0 }) },
      '/advertisers?view=review&points=7',
      'Под фильтр ничего не попало.',
    );

    expect(screen.getByRole('textbox', { name: 'Балл' })).toHaveValue('7');
  });

  it('без права решать у строки фильтров нет лишней ячейки', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      ...CRAWL_ROUTES,
      'GET /api/auth/me': {
        body: { ...(OPERATOR as object), permissions: ['advertisers', 'view'] },
      },
      'GET /api/advertisers/niche?page=1': {
        body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
      },
      'GET /api/advertisers': queuePage(1, { total: 1 }),
    });
    renderWith(<AppRoutes />, '/advertisers?view=review');
    await screen.findByText('row-1.example');

    const filters = screen.getByRole('textbox', { name: 'Балл' }).closest('tr')!;
    expect(filters.querySelectorAll('th')).toHaveLength(5);
  });
});
