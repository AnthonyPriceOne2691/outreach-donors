/**
 * Резерв на экране: не прошедшие прогона с причиной, «кому не пишем» — словами вместо
 * кнопки, «Взять на рассмотрение» — в очередь своего прогона, фильтры — в адресе.
 * Данные выдуманы.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call } from '../test/server';
import type { ReserveCard, ReserveView } from './reserveApi';

const FIT = { place: null, share: null, top: null, top_share: null, asked: false, partial: false };

function card(overrides: Partial<ReserveCard>): ReserveCard {
  return {
    run_id: 30,
    country: 'ca',
    domain_id: 1,
    donor_id: 11,
    host: 'quiet-blog.example',
    group: 'weak',
    cause: 'org_traffic',
    reason: 'органический трафик 120 ниже 500',
    dr: 34,
    org_traffic: 120,
    refdomains: 400,
    keywords: 900,
    fit: FIT,
    queued: null,
    takeable: true,
    refusal: null,
    ...overrides,
  };
}

const ROWS: ReserveCard[] = [
  card({}),
  card({
    domain_id: 2,
    host: 'far-away.example',
    group: 'region',
    cause: 'geo',
    reason: 'Канада не входит в топ-5 и даёт меньше 20%',
    org_traffic: 10000,
    fit: { place: null, share: null, top: 'us', top_share: 0.6, asked: true, partial: false },
  }),
  card({
    domain_id: 3,
    donor_id: null,
    host: 'giant-news.example',
    group: 'not_writing',
    cause: 'big',
    reason: 'Кому не пишем: слишком крупный — DR выше 80',
    dr: 92,
    takeable: false,
    refusal: 'Не берём: кому не пишем',
  }),
];

const VIEW: ReserveView = {
  rows: ROWS,
  total: 3,
  limit: 20,
  groups: { weak: 1, region: 1, not_writing: 1 },
  countries: { ca: 3 },
  geos: { us: 1 },
  run: null,
};

function open(
  routes: Record<string, Answer | ((call: Call) => Answer)>,
  path = '/donors/reserve',
  me = ADMIN,
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({ 'GET /api/auth/me': { body: me }, ...routes });
  renderWith(<AppRoutes />, path);
  return recorded;
}

/** Строка таблицы по домену: имя — ссылкой на карточку или, у снятого гейтом, текстом. */
function rowOf(host: string): HTMLElement {
  const row = screen.getAllByRole('row').find((one) => (one.textContent ?? '').includes(host));
  if (row === undefined) throw new Error(`строки ${host} нет`);
  return row;
}

describe('резерв', () => {
  it('не прошедшие — с причиной, прогоном и местом страны; «кому не пишем» — словами вместо кнопки', async () => {
    open({ 'GET /api/review/reserve': { body: VIEW } });

    expect(await screen.findByRole('heading', { name: 'Резерв' })).toBeInTheDocument();
    expect(screen.getByText('всего 3 домена')).toBeInTheDocument();
    const far = rowOf('far-away.example');
    expect(within(far).getByText('Канада не входит в топ-5 и даёт меньше 20%')).toBeInTheDocument();
    expect(within(far).getByText('Канада не в топ-5')).toBeInTheDocument();
    expect(within(far).getByText('верхняя — США · 60%')).toBeInTheDocument();
    expect(within(far).getByRole('link', { name: '№30' })).toHaveAttribute('href', '/runs/30');
    // Отсеянному по метрикам стран не покупали — так и сказано.
    expect(
      within(rowOf('quiet-blog.example')).getByText('страны не спрашивали'),
    ).toBeInTheDocument();
    const giant = rowOf('giant-news.example');
    expect(within(giant).queryByRole('button')).not.toBeInTheDocument();
    expect(within(giant).getByText('Не берём: кому не пишем')).toBeInTheDocument();
    // Имя — на сайт, как в очереди рассмотрения; снятый гейтом донором не стал — текстом.
    expect(
      within(rowOf('quiet-blog.example')).getByRole('link', { name: 'quiet-blog.example' }),
    ).toHaveAttribute('href', 'https://quiet-blog.example');
    expect(within(giant).queryByRole('link', { name: 'giant-news.example' })).toBeNull();
    // Трафик — как у доноров и в очереди: коротко.
    expect(within(far).getByText('10 тыс.')).toBeInTheDocument();
  });

  it('«Взять на рассмотрение» — в очередь прогона, где домен в резерве, и итог словами', async () => {
    const taken = {
      ...VIEW,
      rows: [
        card({ queued: 'pending', takeable: false, refusal: 'уже на рассмотрении в этом прогоне' }),
      ],
      total: 1,
    };
    let answered = false;
    const recorded = open({
      'GET /api/review/reserve': () => ({ body: answered ? taken : VIEW }),
      'POST /api/review/reserve/take': () => {
        answered = true;
        return {
          body: { taken: 1, runs: { '30': ['quiet-blog.example'] }, already: [], refused: {} },
        };
      },
    });
    const user = userEvent.setup();

    const row = await screen.findByRole('link', { name: 'quiet-blog.example' });
    await user.click(
      within(row.closest('tr') as HTMLElement).getByRole('button', {
        name: 'Взять на рассмотрение',
      }),
    );

    expect(
      await screen.findByText('Взято на рассмотрение: 1 домен — в очереди прогона №30'),
    ).toBeInTheDocument();
    const post = recorded.calls.find((call) => call.method === 'POST');
    expect(post?.body).toEqual({ items: [{ run_id: 30, domain_id: 1 }] });
    expect(await screen.findByText('уже на рассмотрении в этом прогоне')).toBeInTheDocument();
  });

  it('фильтр причины — в адресе и в запросе, счётчики — в пунктах', async () => {
    const recorded = open({
      'GET /api/review/reserve': { body: VIEW },
      'GET /api/review/reserve?group=region': { body: { ...VIEW, rows: [ROWS[1]], total: 1 } },
    });
    const user = userEvent.setup();

    await screen.findByRole('heading', { name: 'Резерв' });
    await user.click(screen.getByRole('textbox', { name: 'Почему не прошёл' }));
    await user.click(await screen.findByRole('option', { name: 'Регион · 1' }));

    await waitFor(() =>
      expect(recorded.calls.some((call) => call.path === '/api/review/reserve?group=region')).toBe(
        true,
      ),
    );
    expect(await screen.findByText('найдено 1 домен')).toBeInTheDocument();
  });

  it('резерв прогона — его строка под заголовком и вход в его очередь', async () => {
    open(
      {
        'GET /api/review/reserve?run_id=30': {
          body: {
            ...VIEW,
            run: { id: 30, country: 'ca', keywords: 6, created_at: '2026-10-20T09:29:00Z' },
          },
        },
      },
      '/donors/reserve?run=30',
    );

    expect(
      await screen.findByText(/Прогон №30 · Канада, ключей 6 — его не прошедшие/),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Рассмотрение прогона' })).toHaveAttribute(
      'href',
      '/runs/30/review',
    );
    // Страны прогона у резерва одного прогона не выбирают — она одна.
    expect(screen.queryByRole('textbox', { name: 'Страна прогона' })).not.toBeInTheDocument();
  });

  it('резерв прогона по конкурентам называет конкурентов, а не «ключей 0»', async () => {
    open(
      {
        'GET /api/review/reserve?run_id=31': {
          body: {
            ...VIEW,
            run: {
              id: 31,
              country: 'us',
              keywords: 0,
              competitors: ['rival.example'],
              created_at: '2026-10-21T15:14:00Z',
            },
          },
        },
      },
      '/donors/reserve?run=31',
    );

    expect(
      await screen.findByText(/Прогон №31 · США, конкуренты rival\.example — его не прошедшие/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/ключей 0/)).not.toBeInTheDocument();
  });

  it('пустой резерв прогона говорит почему', async () => {
    open(
      {
        'GET /api/review/reserve?run_id=12': {
          body: {
            ...VIEW,
            rows: [],
            total: 0,
            run: { id: 12, country: 'us', keywords: 3, created_at: '2026-10-01T09:00:00Z' },
          },
        },
      },
      '/donors/reserve?run=12',
    );

    expect(await screen.findByText('В резерве прогона №12 никого')).toBeInTheDocument();
    expect(screen.getByText(/до 20\.10\.2026 вердикт по доменам не записывал/)).toBeInTheDocument();
  });

  it('без права решать в очереди — кнопки нет, сказано, какое право нужно', async () => {
    open({ 'GET /api/review/reserve': { body: VIEW } }, '/donors/reserve', {
      ...OPERATOR,
      permissions: ['view'],
    });

    const row = await screen.findByRole('link', { name: 'quiet-blog.example' });
    expect(within(row.closest('tr') as HTMLElement).queryByRole('button')).not.toBeInTheDocument();
    expect(
      within(row.closest('tr') as HTMLElement).getByText(/нужно право «подтверждать цены»/),
    ).toBeInTheDocument();
  });

  it('вкладки раздела: «Доноры» ведёт на доноров, резерв — выбран', async () => {
    open({ 'GET /api/review/reserve': { body: VIEW } });

    await screen.findByRole('heading', { name: 'Резерв' });
    const tabs = screen.getByRole('radiogroup', { name: 'Раздел доноров' });
    expect(within(tabs).getByRole('radio', { name: 'Резерв' })).toBeChecked();
  });
});
