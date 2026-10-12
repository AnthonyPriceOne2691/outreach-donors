/**
 * «Все рекламодатели» — список с фильтрами и выгрузкой (сверка ТЗ 11.10.2026).
 *
 * Проверяется то, ради чего вид: строка говорит, под какую ссылку письмо, есть ли
 * адрес и что с оффером; фильтр уходит на сервер параметром и живёт в адресе;
 * выгрузка — тем же фильтром, и что легло в файл, сказано словами.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AdvertiserCard, AdvertiserFacets, AdvertiserListView } from '../api/advertiserList';
import { ADMIN, CRAWL_ROUTES, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { addressBadge } from './AdvertiserListTable';

/** Где сейчас экран: адрес целиком — по нему видно, что живёт в адресе. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

const LINKED: AdvertiserCard = {
  id: 1,
  host: 'shop.example.test',
  source: 'links',
  run_id: null,
  donor_host: 'donor.example.test',
  page_url: 'https://donor.example.test/best-tools',
  anchor: 'best tools for teams',
  points: 7,
  reasons: ['rel=sponsored +5', 'коммерческий анкор +1'],
  rescored: 6,
  donors: 2,
  contact_status: 'found',
  address: 'found',
  letter: 'replied',
};

const NICHE: AdvertiserCard = {
  ...LINKED,
  id: 2,
  host: 'casino.example.test',
  source: 'niche',
  run_id: 12,
  donor_host: null,
  page_url: null,
  anchor: null,
  points: 0,
  reasons: [],
  rescored: null,
  donors: 0,
  contact_status: 'form_only',
  address: 'form',
  letter: 'none',
};

function listOf(overrides: Partial<AdvertiserListView> = {}): AdvertiserListView {
  return {
    rows: [LINKED, NICHE],
    total: 2,
    page: 1,
    limit: 20,
    export_limit: 10_000,
    ...overrides,
  };
}

const FACETS: AdvertiserFacets = {
  sources: { links: 1, niche: 1 },
  addresses: { found: 1, form: 1 },
  letters: { replied: 1, none: 1 },
  donors: { 'donor.example.test': 1 },
};

const NO_ONE: AdvertiserFacets = { sources: {}, addresses: {}, letters: {}, donors: {} };

async function openList(
  routes: Record<string, Answer> = {},
  path = '/advertisers?view=list',
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/advertisers/list': { body: listOf() },
    'GET /api/advertisers/list/facets': { body: FACETS },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByRole('heading', { name: 'Все рекламодатели' });
  return recorded;
}

/** Запросы страниц и выгрузки — без счётчиков: у тех свой запрос. */
function listCalls(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => /^\/api\/advertisers\/list(\?|\/export|$)/.test(call.path))
    .map((call: Call) => call.path);
}

/** Пункты списка одного поля: на строке фильтров списков несколько. */
async function choose(fieldName: string, option: string) {
  const user = userEvent.setup();
  const field = screen.getByRole('textbox', { name: fieldName });
  await user.click(field);
  const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
  await user.click(await list.findByRole('option', { name: option, hidden: true }));
}

describe('все рекламодатели списком', () => {
  it('строка: донор, страница и анкор, балл, адрес, письмо', async () => {
    await openList();

    const row = screen.getByText('shop.example.test').closest('tr')!;
    expect(within(row).getByText('donor.example.test')).toBeInTheDocument();
    expect(within(row).getByText('доноров: 2')).toBeInTheDocument();
    expect(within(row).getByRole('link', { name: 'страница' })).toHaveAttribute(
      'href',
      'https://donor.example.test/best-tools',
    );
    expect(within(row).getByText(/best tools for teams/)).toBeInTheDocument();
    expect(within(row).getByText('7')).toBeInTheDocument();
    expect(within(row).getByText('адрес найден')).toBeInTheDocument();
    expect(within(row).getByText('ответил')).toBeInTheDocument();
    expect(screen.getByText('всего 2')).toBeInTheDocument();
  });

  it('за что балл — причинами строкой, и балл после пересчёта рядом', async () => {
    await openList();

    const row = screen.getByText('shop.example.test').closest('tr')!;
    expect(within(row).getByText('rel=sponsored +5')).toBeInTheDocument();
    expect(within(row).getByText('коммерческий анкор +1')).toBeInTheDocument();
    expect(within(row).getByText('после пересчёта — 6')).toBeInTheDocument();
    const labels = [...row.querySelectorAll('td[data-label]')].map((cell) =>
      cell.getAttribute('data-label'),
    );
    expect(labels).toEqual(['Донор', 'Балл', 'За что', 'Ссылка', 'Адрес', 'Письмо']);
  });

  it('счётчики фильтров спрашиваются раз — не на каждую страницу и фильтр', async () => {
    const recorded = await openList({
      'GET /api/advertisers/list?letter=replied': { body: listOf({ rows: [LINKED], total: 1 }) },
    });

    await choose('Письмо', 'ответил · 1');
    await screen.findByText('найдено 1 из 2');

    const facets = recorded.calls.filter((call: Call) => call.path.endsWith('/list/facets'));
    expect(facets).toHaveLength(1);
  });

  it('у бизнеса ниши — откуда он, а не донор, и балла нет', async () => {
    await openList();

    const row = screen.getByText('casino.example.test').closest('tr')!;
    expect(within(row).getByText('бизнес ниши')).toBeInTheDocument();
    expect(within(row).getByText('прогон №12')).toBeInTheDocument();
    expect(within(row).getByText('только форма')).toBeInTheDocument();
    expect(within(row).getByText('не собрано')).toBeInTheDocument();
    // Балла, причин и ссылки у бизнеса ниши нет — прочерки, а не «0».
    expect(within(row).getAllByText('—')).toHaveLength(3);
  });

  it('фильтр уходит на сервер параметром и встаёт в адрес', async () => {
    const recorded = await openList({
      'GET /api/advertisers/list?letter=replied': {
        body: listOf({ rows: [LINKED], total: 1 }),
      },
    });

    await choose('Письмо', 'ответил · 1');

    await waitFor(() =>
      expect(listCalls(recorded).at(-1)).toBe('/api/advertisers/list?letter=replied'),
    );
    expect(await screen.findByText('найдено 1 из 2')).toBeInTheDocument();
    expect(screen.queryByText('casino.example.test')).toBeNull();
  });

  it('из адреса — в запрос и в поля; незнакомое значение не сужает', async () => {
    const recorded = await openList(
      {
        'GET /api/advertisers/list?donor=donor.example.test&address=form': {
          body: listOf({ rows: [], total: 0 }),
        },
      },
      '/advertisers?view=list&donor=Donor.example.test&address=form&letter=gone',
    );

    expect(listCalls(recorded)).toEqual([
      '/api/advertisers/list?donor=donor.example.test&address=form',
    ]);
    expect(screen.getByRole('textbox', { name: 'Донор' })).toHaveValue('donor.example.test');
    expect(screen.getByRole('textbox', { name: 'Адрес' })).toHaveValue('только форма');
    expect(await screen.findByText('Под фильтр ничего не попало.')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Условия: донор donor.example.test, только форма. Всего рекламодателей — 2.',
      ),
    ).toBeInTheDocument();
  });

  it('рекламодателей нет вовсе — откуда они берутся', async () => {
    await openList({
      'GET /api/advertisers/list': { body: listOf({ rows: [], total: 0 }) },
      'GET /api/advertisers/list/facets': { body: NO_ONE },
    });

    expect(await screen.findByText('Рекламодателей пока нет.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Выгрузить всех · 0' })).toBeDisabled();
  });

  it('выгрузка — тем же фильтром, и что легло в файл, сказано словами', async () => {
    const recorded = await openList({
      'GET /api/advertisers/list?source=links': { body: listOf({ rows: [LINKED], total: 1 }) },
      'GET /api/advertisers/list/export?source=links': {
        raw: 'рекламодатель;откуда\r\n',
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'X-Export-Rows': '1',
          'X-Export-Asked': '1',
        },
      },
    });
    const user = userEvent.setup();
    URL.createObjectURL = () => 'blob:advertisers';
    URL.revokeObjectURL = () => undefined;

    await choose('Откуда', 'по ссылке · 1');
    const button = await screen.findByRole('button', { name: 'Выгрузить найденных · 1' });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);

    expect(await screen.findByText('Выгружено: 1 рекламодатель.')).toBeInTheDocument();
    expect(listCalls(recorded)).toContain('/api/advertisers/list/export?source=links');
  });

  it('файл короче найденного — сказано, сколько из скольких и что делать', async () => {
    await openList({
      'GET /api/advertisers/list': { body: listOf({ total: 12_000 }) },
      'GET /api/advertisers/list/export': {
        raw: 'рекламодатель;откуда\r\n',
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'X-Export-Rows': '10000',
          'X-Export-Asked': '12000',
        },
      },
    });
    const user = userEvent.setup();
    URL.createObjectURL = () => 'blob:advertisers';
    URL.revokeObjectURL = () => undefined;

    // Кнопка не обещает больше потолка: «10 000 из 12 000».
    await user.click(await screen.findByRole('button', { name: /^Выгрузить 10\s000 из 12\s000$/ }));

    expect(
      await screen.findByText(
        /^В файле 10\s000 из 12\s000: больше за раз не выгружается — сузьте фильтр/,
      ),
    ).toBeInTheDocument();
  });
});

describe('переключатель видов', () => {
  it('проверка — по умолчанию; «Все рекламодатели» — свой адрес', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      ...CRAWL_ROUTES,
      'GET /api/auth/me': { body: ADMIN },
      // Вторая страница бизнесов ниши за концом — экран уходит на первую сам.
      'GET /api/advertisers/niche?page=2': {
        body: { rows: [], waiting: 0, total: 0, page: 2, limit: 20 },
      },
      'GET /api/advertisers/niche?page=1': {
        body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
      },
      'GET /api/advertisers': { body: { rows: [], waiting: 0, counts: {} } },
      'GET /api/advertisers/list': { body: listOf() },
      'GET /api/advertisers/list/facets': { body: FACETS },
    });
    renderWith(
      <>
        <AppRoutes />
        <Where />
      </>,
      '/advertisers?niche_page=2',
    );
    const user = userEvent.setup();

    expect(await screen.findByText(/Спорных нет/)).toBeInTheDocument();

    await user.click(screen.getByText('Все рекламодатели'));

    expect(await screen.findByText('shop.example.test')).toBeInTheDocument();
    // Смена вида — чистый адрес: страница бизнесов ниши списку ничего не говорит.
    expect(screen.getByTestId('where')).toHaveTextContent('/advertisers?view=list');
    expect(screen.queryByText(/Спорных нет/)).toBeNull();
  });
});

describe('адрес строки словами', () => {
  it.each([
    [{ address: 'found' as const, contact_status: 'not_found' as const }, 'адрес найден'],
    [{ address: 'form' as const, contact_status: 'form_only' as const }, 'только форма'],
    [{ address: 'none' as const, contact_status: null }, 'не искали'],
    [{ address: 'none' as const, contact_status: 'no_answer' as const }, 'сайт не ответил'],
    // Исход поиска называет адрес, которого в базе уже нет.
    [{ address: 'none' as const, contact_status: 'found' as const }, 'адреса нет'],
  ])('%o → %s', (overrides, said) => {
    expect(addressBadge({ ...LINKED, ...overrides }).title).toBe(said);
  });
});
