/**
 * Карточка донора: адреса, откуда они, чем кончился поиск — и поиск адреса
 * для этого донора, если адреса нет.
 *
 * Проверяется главное из замечания 25.09.2026: поиск ставится с карточки
 * одного донора, кнопка — только без адреса, а почему поиск не ставится,
 * видно до нажатия; отказ сервера — его словами.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { DonorFullCard, JobCard, Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';

const CARD: DonorFullCard = {
  id: 7,
  host: 'card.example.test',
  status: 'suitable',
  reject_reason: null,
  dr: 55,
  org_traffic: 120_000,
  geo: 'us',
  geo_top_share: 0.62,
  geo_breakdown: null,
  geo_partial: false,
  metrics: null,
  metrics_refreshed_at: '2026-09-18T10:00:00+00:00',
  expires_at: '2027-03-17T10:00:00+00:00',
  fresh: true,
  contact_status: null,
  contact_attempted_at: null,
  last_price: null,
  last_price_currency: null,
  last_price_at: null,
  contacts: [],
  contact_refusal: null,
};

function job(state: JobCard['state'], title: string): JobCard {
  return {
    job_id: 'job-7',
    kind: 'поиск контактов',
    state,
    title,
    error: null,
    report: null,
    retries_left: 3,
    next_try_at: null,
    ended_at: null,
  };
}

async function openCard(
  card: DonorFullCard = CARD,
  routes: Record<string, Answer> = {},
  me: Me = ADMIN,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: me },
    [`GET /api/donors/${card.id}`]: { body: card },
    ...routes,
  });
  renderWith(<AppRoutes />, `/donors/${card.id}`);
  await screen.findByRole('heading', { name: card.host });
  return recorded;
}

function addresses(): HTMLElement {
  return screen.getByRole('heading', { name: 'Адреса' }).closest('.mantine-Card-root')!;
}

describe('карточка донора: адреса', () => {
  it('адрес есть — виден со ступенью, которая его дала, и кнопки поиска нет', async () => {
    await openCard({
      ...CARD,
      contact_status: 'found',
      contact_attempted_at: '2026-09-21T10:00:00+00:00',
      contacts: [
        {
          id: 1,
          email: 'editor@card.example.test',
          source: 'provider',
          last_contacted_at: null,
          last_replied_at: null,
        },
      ],
    });

    const section = addresses();
    expect(within(section).getByText('editor@card.example.test')).toBeInTheDocument();
    // До 25.09.2026 фронт не знал значения `provider`, и колонка была пустой.
    expect(within(section).getByText('платный сервис')).toBeInTheDocument();
    expect(within(section).getByText('адрес найден')).toBeInTheDocument();
    expect(within(section).getByText('Искали 21.09.2026.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Найти адрес' })).toBeNull();
  });

  it('адреса нет, правило пускает — поиск ставится этому донору', async () => {
    const recorded = await openCard(CARD, {
      'POST /api/contacts/donors/7': { body: { job_id: 'job-7', pending: 1 } },
      'GET /api/jobs/job-7': { body: job('queued', 'в очереди') },
    });
    const user = userEvent.setup();

    expect(within(addresses()).getByText('Адрес ещё не искали.')).toBeInTheDocument();
    expect(within(addresses()).getByText('не искали')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Найти адрес' }));

    await screen.findByText('Поиск адреса поставлен в очередь');
    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent.map((call) => call.path)).toEqual(['/api/contacts/donors/7']);
    expect(sent[0]?.token).toBe('Bearer пропуск');
    expect(await screen.findByRole('status')).toHaveTextContent('Поиск контактов: в очереди');
    // Пока задача идёт, второй поиск не предлагается.
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Найти адрес' })).toBeNull());
  });

  it('сервер отказал — отказ показан его словами', async () => {
    const refusal =
      'Повторный поиск — не раньше 20.03.2027: до тех пор исход прошлого считается свежим.';
    await openCard(CARD, {
      'POST /api/contacts/donors/7': { status: 409, body: { detail: refusal } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Найти адрес' }));

    expect(await screen.findByText(refusal)).toBeInTheDocument();
    expect(screen.getByText('Не поставили')).toBeInTheDocument();
  });

  it('правило не пускает — причина видна до нажатия, а кнопки нет', async () => {
    await openCard({
      ...CARD,
      contact_refusal:
        'Адрес ищут после решения человека: донора сначала принимают на рассмотрении прогона.',
    });

    expect(within(addresses()).getByText(/Адрес ищут после решения человека/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Найти адрес' })).toBeNull();
  });

  it('без права на запуск — объяснение на месте, а не кнопка', async () => {
    await openCard(CARD, {}, { ...OPERATOR, permissions: ['view'] });

    expect(
      within(addresses()).getByText(/Поиск ставит сотрудник с правом «запускать прогоны»/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Найти адрес' })).toBeNull();
  });

  it('поиск, поставленный раньше, виден и после возврата в карточку', async () => {
    localStorage.setItem('donor:7:contacts-job', 'job-7');
    await openCard(CARD, { 'GET /api/jobs/job-7': { body: job('running', 'идёт') } });

    expect(await screen.findByRole('status')).toHaveTextContent('Поиск контактов: идёт');
    expect(screen.queryByRole('button', { name: 'Найти адрес' })).toBeNull();
  });

  it('кончился поиск — карточка перечитывается, удачный исход не висит до завтра', async () => {
    localStorage.setItem('donor:7:contacts-job', 'job-7');
    const recorded = await openCard(CARD, {
      'GET /api/jobs/job-7': { body: job('done', 'готово') },
    });

    await waitFor(() =>
      expect(recorded.calls.filter((call) => call.path === '/api/donors/7')).toHaveLength(2),
    );
    expect(localStorage.getItem('donor:7:contacts-job')).toBeNull();
  });

  it('упавший поиск помнится с причиной и после возврата', async () => {
    localStorage.setItem('donor:7:contacts-job', 'job-7');
    await openCard(CARD, {
      'GET /api/jobs/job-7': {
        body: {
          ...job('failed', 'упала'),
          error: 'техническая ошибка (ConnectError)',
          retries_left: 0,
        },
      },
    });

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Поиск контактов: упала — техническая ошибка (ConnectError)',
    );
    expect(localStorage.getItem('donor:7:contacts-job')).toBe('job-7');
  });
});

describe('карточка донора: возврат к списку', () => {
  it('«К списку» возвращает на ту же страницу с теми же фильтрами', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/contacts': { body: { pending: 0, running: false, job_id: null, last: null } },
      'GET /api/donors?status=suitable&limit=20&offset=20': {
        body: {
          rows: [{ ...CARD, contacts: 0 }],
          total: 25,
          counts: { suitable: 25, unsuitable: 3 },
        },
      },
      'GET /api/donors/7': { body: CARD },
    });
    renderWith(<AppRoutes />, '/donors?status=suitable&page=2');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('link', { name: 'card.example.test' }));
    await screen.findByRole('heading', { name: 'card.example.test' });
    await user.click(screen.getByRole('button', { name: 'К списку' }));

    expect(await screen.findByRole('textbox', { name: 'Вердикт' })).toHaveValue('подходит · 25');
    const lists = recorded.calls.filter((call) => call.path.startsWith('/api/donors?'));
    expect(new Set(lists.map((call) => call.path))).toEqual(
      new Set(['/api/donors?status=suitable&limit=20&offset=20']),
    );
  });
});
