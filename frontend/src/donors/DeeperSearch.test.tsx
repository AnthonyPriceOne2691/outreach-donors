/**
 * «Искать глубже» — в карточке донора и в строке списка (20.10.2026).
 *
 * Можно ли — решает сервер (`deeper.refusal`, `row.deeper`): кнопка есть, только когда
 * можно, а почему нельзя после прошлого раза — словами сервера. Причина «адреса нет»
 * у сайта, который нас не пустил, видна в строке «Искали …».
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { DonorCardWithReach } from '../api/deeper';
import type { DonorRowCard, JobCard, Me } from '../api/types';
import { CARD, CONTACTS_STATE, page, ROW } from '../test/donorFixtures';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Recorded } from '../test/server';

/** Записанные ответы — и функцией: ответ меняется посреди теста. */
type Routes = Parameters<typeof serve>[0];

const NOT_FOUND: DonorCardWithReach = {
  ...CARD,
  contact_status: 'not_found',
  contact_attempted_at: '2026-09-18T10:00:00+00:00',
  contact_refusal:
    'Повторный поиск — не раньше 17.03.2027: до тех пор исход прошлого считается свежим.',
  contact_reason: 'сайт не пустил нас (403)',
  deeper: { refusal: null, at: null, note: null },
};

function job(
  state: JobCard['state'],
  title: string,
  report: Record<string, unknown> | null = null,
): JobCard {
  return {
    job_id: 'contacts-deeper-7',
    kind: 'поиск адреса глубже',
    state,
    title,
    error: null,
    report,
    retries_left: 3,
    next_try_at: null,
    ended_at: null,
  };
}

async function openCard(
  card: DonorCardWithReach,
  routes: Routes = {},
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

describe('карточка донора: «адреса нет» и поиск глубже', () => {
  it('причина «адреса нет» — в строке «Искали», и кнопка глубже с тем, что она делает', async () => {
    await openCard(NOT_FOUND);

    expect(screen.getByText('Искали 18.09.2026: сайт не пустил нас (403).')).toBeVisible();
    expect(screen.getByText(/больше страниц сайта, разделы из его карты и браузер/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Искать глубже' })).toBeEnabled();
  });

  it('нажатие ставит задачу, и её итог виден словами сервера', async () => {
    let state: JobCard = job('running', 'идёт');
    const recorded = await openCard(NOT_FOUND, {
      'POST /api/contacts/donors/7/deeper': { body: { job_id: 'contacts-deeper-7', pending: 1 } },
      'GET /api/jobs/contacts-deeper-7': () => ({ body: state }),
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Искать глубже' }));

    expect(await screen.findByText('Поиск адреса глубже: идёт')).toBeVisible();
    expect(localStorage.getItem('donor:7:deeper-job')).toBe('contacts-deeper-7');
    state = job('done', 'готово', { note: 'адрес нашёлся на странице /info/contact-the-team/' });
    expect(
      await screen.findByText(
        'Адрес нашёлся на странице /info/contact-the-team/.',
        {},
        { timeout: 5000 },
      ),
    ).toBeVisible();
    await waitFor(() =>
      expect(recorded.calls.filter((call) => call.path === '/api/donors/7').length).toBeGreaterThan(
        1,
      ),
    );
    expect(localStorage.getItem('donor:7:deeper-job')).toBeNull();
  });

  it('отказ сервера — уведомлением его словами', async () => {
    await openCard(NOT_FOUND, {
      'POST /api/contacts/donors/7/deeper': {
        status: 409,
        body: { detail: 'Поиск глубже по этому донору уже идёт — дождитесь его итога у кнопки' },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Искать глубже' }));

    expect(await screen.findByText(/уже идёт — дождитесь его итога/)).toBeVisible();
  });

  it('после поиска глубже — итог прошлого раза, а кнопки нет', async () => {
    await openCard({
      ...NOT_FOUND,
      deeper: {
        refusal:
          'Глубже уже искали 20.10.2026: адреса нет. Снова — после следующего обычного поиска.',
        at: '2026-10-20T09:00:00+00:00',
        note: 'адреса нет; прочитано страниц 12, запросов к сайту 40',
      },
    });

    expect(
      screen.getByText(
        'Глубже искали 20.10.2026: адреса нет; прочитано страниц 12, запросов к сайту 40.',
      ),
    ).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Искать глубже' })).toBeNull();
  });

  it('без права «запускать прогоны» кнопки нет', async () => {
    await openCard(NOT_FOUND, {}, { ...OPERATOR, permissions: ['view'] });

    expect(screen.queryByRole('button', { name: 'Искать глубже' })).toBeNull();
  });

  it('у донора с найденным адресом блока глубже нет', async () => {
    await openCard({ ...NOT_FOUND, contact_status: 'found', contact_reason: null });

    expect(screen.queryByText(/больше страниц сайта/)).toBeNull();
  });

  it('«не спросили» — причина в строке «Искали», полное имя исхода, глубже не ищут', async () => {
    await openCard({
      ...NOT_FOUND,
      contact_status: 'paid_off',
      deeper: {
        refusal:
          'В прошлый раз платный поиск был недоступен — глубже ищут после обычного поиска вместе с ним.',
        at: null,
        note: null,
      },
    });

    expect(screen.getByText('Искали 18.09.2026: сайт не пустил нас (403).')).toBeVisible();
    expect(screen.getByText('платный поиск недоступен — не спросили')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Искать глубже' })).toBeNull();
  });

  it('у «только формы» ссылка ведёт на страницу формы', async () => {
    await openCard({
      ...NOT_FOUND,
      contact_status: 'form_only',
      contact_reason: null,
      form_url: 'https://card.example.test/contact/',
    });

    expect(screen.getByRole('link', { name: 'открыть форму' })).toHaveAttribute(
      'href',
      'https://card.example.test/contact/',
    );
  });
});

describe('список доноров: глубже — значком в колонке «Адреса»', () => {
  const READY: DonorRowCard & { deeper: boolean } = {
    ...ROW,
    id: 9,
    host: 'ready.example.test',
    contacts: 0,
    contact_status: 'not_found',
    deeper: true,
  };

  async function openDonors(routes: Routes = {}): Promise<Recorded> {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/contacts': { body: CONTACTS_STATE },
      'GET /api/donors?limit=20&offset=0': { body: page({ rows: [ROW, READY], total: 2 }) },
      ...routes,
    });
    renderWith(<AppRoutes />, '/donors');
    await screen.findByText('ready.example.test');
    return recorded;
  }

  it('значок — только у строки, где сервер разрешает, и не уводит в карточку', async () => {
    const recorded = await openDonors({
      'POST /api/contacts/donors/9/deeper': { body: { job_id: 'contacts-deeper-9', pending: 1 } },
      'GET /api/jobs/contacts-deeper-9': {
        body: { ...job('running', 'идёт'), job_id: 'contacts-deeper-9' },
      },
    });
    const user = userEvent.setup();

    expect(
      screen.queryByRole('button', { name: 'Искать адрес глубже: good.example.test' }),
    ).toBeNull();
    await user.click(
      screen.getByRole('button', { name: 'Искать адрес глубже: ready.example.test' }),
    );

    expect(
      await screen.findByLabelText('Ищем адрес глубже: ready.example.test'),
    ).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path === '/api/contacts/donors/9/deeper')).toBe(true);
    expect(screen.queryByRole('heading', { name: 'ready.example.test' })).toBeNull();
    const row = screen.getByText('ready.example.test').closest('tr')!;
    expect(within(row).getByText('адреса нет')).toBeVisible();
  });

  it('«не спросили» — коротким именем в колонке постоянной ширины, полное — подсказкой', async () => {
    const unasked = {
      ...READY,
      id: 11,
      host: 'unasked.example.test',
      contact_status: 'paid_off' as const,
      deeper: false,
    };
    await openDonors({
      'GET /api/donors?limit=20&offset=0': {
        body: page({ rows: [ROW, READY, unasked], total: 3 }),
      },
    });

    const row = (await screen.findByText('unasked.example.test')).closest('tr')!;
    expect(within(row).getByTitle('платный поиск недоступен — не спросили')).toHaveTextContent(
      'не спросили',
    );
  });
});
