/**
 * Бизнесы ниши из выдачи на экране рекламодателей: откуда взялся, кто
 * сказал «продаёт своё», решение человека и сбор из старого прогона.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { NicheCard } from '../api/niche';
import { ADMIN, CRAWL_ROUTES, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const BOOKIE: NicheCard = {
  id: 7,
  host: 'bookie.example.test',
  run_id: 21,
  keywords: ['sports betting', 'odds'],
  country: 'de',
  quote: 'Place your bets on football',
  intent_by: 'judge',
  confirmed: null,
  decided_by: null,
  decided_at: null,
};

const PAGER = 'Страницы бизнесов ниши';

/** Страница ниши, какой её отдаёт сервер: `size` строк с номера `from`, всего `total`. */
function page(size: number, { from = 1, total = size, number = 1 } = {}) {
  const rows = Array.from({ length: size }, (_, index) => ({
    ...BOOKIE,
    id: from + index,
    host: `niche-${from + index}.example.test`,
  }));
  return { body: { rows, waiting: total, total, page: number, limit: 20 } };
}

async function openAdvertisers(
  routes: Record<string, unknown> = {},
  user: unknown = ADMIN,
  path = '/advertisers',
  first = 'bookie.example.test',
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    // Панели обхода и перевода на том же экране — пустые: проверяется карточка ниши.
    ...CRAWL_ROUTES,
    'GET /api/auth/me': { body: user },
    'GET /api/advertisers': { body: { rows: [], waiting: 0, counts: {} } },
    'GET /api/advertisers/niche?page=1': {
      body: { rows: [BOOKIE], waiting: 1, total: 1, page: 1, limit: 20 },
    },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(first);
  return recorded;
}

describe('бизнесы ниши из выдачи', () => {
  it('показывает, откуда бизнес и кто сказал «продаёт своё»', async () => {
    await openAdvertisers();

    expect(screen.getByText('прогон №21 · sports betting, odds · DE')).toBeInTheDocument();
    expect(screen.getByText('продаёт своё — судья')).toBeInTheDocument();
    expect(screen.getByText('«Place your bets on football»')).toBeInTheDocument();
    expect(screen.getByText(/Ждут решения: 1\./)).toBeInTheDocument();
  });

  it('«Пишем» уходит на сервер решением человека', async () => {
    const recorded = await openAdvertisers({
      'POST /api/advertisers/niche/7/decide': { body: { ...BOOKIE, confirmed: true } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Пишем' }));

    await waitFor(() => {
      const sent = recorded.calls.find((call) => call.path === '/api/advertisers/niche/7/decide');
      expect(sent?.body).toEqual({ write: true });
    });
  });

  it('сбор из старого прогона говорит, сколько нашлось', async () => {
    const recorded = await openAdvertisers({
      'POST /api/advertisers/niche/collect?run_id=18': {
        body: { run_id: 18, found: 4, added: 3 },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Собрать из прогона №'), '18');
    await user.click(screen.getByRole('button', { name: 'Собрать' }));

    expect(await screen.findByText('Прогон №18: бизнесов ниши 4, новых 3.')).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path.endsWith('collect?run_id=18'))).toBe(true);
  });

  it('«1.5» не склеивается в 15: в поле — как набрано, отказ словами, сбор закрыт', async () => {
    // Проверка QA 10.10.2026: поле выбрасывало точку, «1.5» становилось 15, и сервер
    // честно отвечал «Прогона №15 нет».
    const recorded = await openAdvertisers();
    const user = userEvent.setup();
    const field = screen.getByLabelText('Собрать из прогона №');

    await user.type(field, '1.5');

    expect(field).toHaveValue('1.5');
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Только целое число')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Собрать' })).toBeDisabled();
    expect(recorded.calls.some((call) => call.path.includes('/niche/collect'))).toBe(false);
  });

  it('одна страница — переключателя нет вовсе', async () => {
    await openAdvertisers();

    expect(screen.queryByRole('navigation', { name: PAGER, hidden: true })).not.toBeInTheDocument();
  });

  it('больше двадцати — переключатель, вторая страница спрашивается у сервера', async () => {
    // Слово Anthony 10.10.2026: полсотни «пишем / не пишем» одним списком — простыня,
    // пятьдесят первый бизнес с экрана было не достать (на проде ждали 53, видно 50).
    const recorded = await openAdvertisers(
      {
        'GET /api/advertisers/niche?page=1': page(20, { total: 53 }),
        'GET /api/advertisers/niche?page=2': page(20, { from: 21, total: 53, number: 2 }),
      },
      ADMIN,
      '/advertisers',
      'niche-1.example.test',
    );
    const user = userEvent.setup();

    expect(screen.getAllByRole('button', { name: 'Пишем' })).toHaveLength(20);
    const pager = screen.getByRole('navigation', { name: PAGER });
    // Страниц три — по размеру, который назвал сервер.
    expect(within(pager).getByRole('button', { name: 'Страница 3' })).toBeInTheDocument();
    await user.click(within(pager).getByRole('button', { name: 'Страница 2' }));

    expect(await screen.findByText('niche-21.example.test')).toBeInTheDocument();
    expect(screen.queryByText('niche-1.example.test')).not.toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path === '/api/advertisers/niche?page=2')).toBe(true);
    // Число ждущих — всех, а не страницы.
    expect(screen.getByText(/Ждут решения: 53\./)).toBeInTheDocument();
  });

  it('номер страницы — в адресе: вторая по ссылке открывается второй', async () => {
    const recorded = await openAdvertisers(
      { 'GET /api/advertisers/niche?page=2': page(13, { from: 21, total: 33, number: 2 }) },
      ADMIN,
      '/advertisers?niche_page=2',
      'niche-21.example.test',
    );

    expect(recorded.calls.some((call) => call.path === '/api/advertisers/niche?page=1')).toBe(
      false,
    );
    const pager = screen.getByRole('navigation', { name: PAGER });
    expect(within(pager).getByRole('button', { name: 'Страница 2' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('страница за концом уводит на последнюю, а не говорит «ждущих нет»', async () => {
    // Решён последний бизнес последней страницы — или ссылку открыли после разбора.
    const recorded = await openAdvertisers(
      {
        'GET /api/advertisers/niche?page=3': page(0, { total: 40, number: 3 }),
        'GET /api/advertisers/niche?page=2': page(20, { from: 21, total: 40, number: 2 }),
      },
      ADMIN,
      '/advertisers?niche_page=3',
      'niche-21.example.test',
    );

    expect(recorded.calls.some((call) => call.path === '/api/advertisers/niche?page=2')).toBe(true);
    expect(screen.queryByText(/Ждущих решения нет/)).not.toBeInTheDocument();
  });

  it('без права решать кнопок и сбора нет', async () => {
    await openAdvertisers({}, { ...OPERATOR, permissions: ['view'] });

    expect(screen.queryByRole('button', { name: 'Пишем' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Собрать из прогона №')).not.toBeInTheDocument();
  });
});
