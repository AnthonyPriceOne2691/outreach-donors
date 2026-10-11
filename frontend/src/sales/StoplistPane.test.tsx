/**
 * Вкладка «Стоп-лист» продаж: свой список, вставка разом, снятие с подтверждением, общий список —
 * только виден. Ответы сервера записанные (`serve()`), тексты и адреса выдуманы.
 *
 * Проверяется то, ради чего экран: список уходит формой (текст и причина), непринятые строки
 * остаются в поле с замечанием, а принятые — нет; снятие называет, сколько лидов вернулось.
 * Без права отправки — смотреть: кнопки закрыты, почему — строкой.
 */

import { notifications } from '@mantine/notifications';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { SalesStoplistAdded, SalesStoplistView } from '../api/salesStoplist';
import type { HypothesesView, KbView } from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };

const HYPOTHESES: HypothesesView = { rows: [], total: 0, module_enabled: true };
const KB: KbView = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
  gaps: [],
};

const STOPLIST: SalesStoplistView = {
  rows: [
    {
      id: 5,
      host: 'partner.example.test',
      email: null,
      note: 'партнёры',
      created_by: 'seller@ours.example.test',
      created_at: '2026-10-06T08:30:00+00:00',
    },
  ],
  total: 1,
  general: [
    {
      id: 41,
      target: 'gone@x.example.test',
      reason: 'unsubscribed',
      stage: 'donors',
      created_at: '2026-10-04T11:00:00+00:00',
    },
  ],
  general_total: 1,
  note_max: 255,
};

const ADD = 'POST /api/sales/stoplist';

async function openStoplist(routes: Record<string, Answer> = {}, who: Me = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    'GET /api/sales/stoplist': { body: STOPLIST },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=stoplist');
  await screen.findByText('partner.example.test', {}, SCREEN_WAIT);
  return { recorded, user: userEvent.setup() };
}

/** Формы, ушедшие на адрес пополнения: заглушка сети тело формы не разбирает. */
function formsSent(): FormData[] {
  return vi
    .mocked(globalThis.fetch)
    .mock.calls.filter(([input]) =>
      String(input instanceof Request ? input.url : input).endsWith('/api/sales/stoplist'),
    )
    .map(([, init]) => init?.body)
    .filter((body): body is FormData => body instanceof FormData);
}

function reads(recorded: Recorded): number {
  return recorded.calls.filter(
    (call) => call.method === 'GET' && call.path === '/api/sales/stoplist',
  ).length;
}

describe('стоп-лист продаж', () => {
  it('свой список — кому, причина, кто и когда; общий — только виден', async () => {
    await openStoplist();

    const own = within(screen.getByRole('table', { name: 'Стоп-лист продаж' }));
    expect(own.getByText('партнёры')).toBeInTheDocument();
    expect(
      own.getByRole('button', { name: 'Снять partner.example.test со стоп-листа продаж' }),
    ).toBeEnabled();
    const general = within(
      screen.getByRole('table', { name: 'Общий стоп-лист, который держит продажи' }),
    );
    expect(general.getByText('отписался')).toBeInTheDocument();
    expect(general.queryByRole('button')).toBeNull();
    expect(screen.getByText('Общий стоп-лист тоже держит продажи — 1')).toBeInTheDocument();
  });

  it('вставка разом: всё легло — окно закрывается, итог словами, список перечитан', async () => {
    const added: SalesStoplistAdded = { added: 2, known: 1, closed: 3, remarks: [] };
    const { recorded, user } = await openStoplist({ [ADD]: { body: added } });
    const show = vi.spyOn(notifications, 'show');

    await user.click(screen.getByRole('button', { name: 'Добавить…' }));
    const dialog = await screen.findByRole('dialog', {}, SCREEN_WAIT);
    await user.type(
      within(dialog).getByRole('textbox', { name: 'Адреса и домены — по строке' }),
      'acme.example.test{enter}boss@beta.example.test',
    );
    await user.type(within(dialog).getByRole('textbox', { name: 'Причина' }), 'клиенты');
    await user.click(within(dialog).getByRole('button', { name: 'Добавить' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), SCREEN_WAIT);
    const [form] = formsSent();
    expect(form?.get('text')).toBe('acme.example.test\nboss@beta.example.test');
    expect(form?.get('note')).toBe('клиенты');
    expect(form?.get('file')).toBeNull();
    expect(show).toHaveBeenCalledWith(
      expect.objectContaining({
        message: 'Стоп-лист продаж: добавлено 2, уже было 1; лидов снято: 3.',
      }),
    );
    await waitFor(() => expect(reads(recorded)).toBeGreaterThan(1), SCREEN_WAIT);
  });

  it('кривые строки — замечание у каждой, в поле остаются только они', async () => {
    const partly: SalesStoplistAdded = {
      added: 1,
      known: 0,
      closed: 0,
      remarks: [{ line: 2, reason: 'не домен и не адрес', cell: 'не домен' }],
    };
    const { user } = await openStoplist({ [ADD]: { body: partly } });

    await user.click(screen.getByRole('button', { name: 'Добавить…' }));
    const dialog = await screen.findByRole('dialog', {}, SCREEN_WAIT);
    const field = within(dialog).getByRole('textbox', { name: 'Адреса и домены — по строке' });
    await user.type(field, 'acme.example.test{enter}не домен');
    await user.click(within(dialog).getByRole('button', { name: 'Добавить' }));

    const alert = await within(dialog).findByText('Не все строки легли', {}, SCREEN_WAIT);
    expect(alert.closest('[role="alert"]')).toHaveTextContent(
      'строка 2: не домен и не адрес — «не домен»',
    );
    expect(field).toHaveValue('не домен');
  });

  it('снятие — с подтверждением, итог называет вернувшихся лидов', async () => {
    const { recorded, user } = await openStoplist({
      'POST /api/sales/stoplist/5/remove': {
        body: { id: 5, target: 'partner.example.test', released: 2 },
      },
    });
    const show = vi.spyOn(notifications, 'show');

    await user.click(
      screen.getByRole('button', { name: 'Снять partner.example.test со стоп-листа продаж' }),
    );
    const dialog = await screen.findByRole('dialog', {}, SCREEN_WAIT);
    expect(dialog).toHaveTextContent('вернутся в «новые» — до писем их проверит очистка');
    await user.click(within(dialog).getByRole('button', { name: 'Снять запись' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), SCREEN_WAIT);
    expect(recorded.calls.some((call) => call.path === '/api/sales/stoplist/5/remove')).toBe(true);
    expect(show).toHaveBeenCalledWith(
      expect.objectContaining({
        message: 'partner.example.test снят со стоп-листа продаж. Лидов вернулось на очистку: 2.',
      }),
    );
  });

  it('без права отправки — смотреть: кнопки закрыты, почему — строкой', async () => {
    await openStoplist({}, OPERATOR);

    expect(screen.getByRole('button', { name: 'Добавить…' })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Снять partner.example.test со стоп-листа продаж' }),
    ).toBeDisabled();
    expect(
      screen.getByText(
        'Правит стоп-лист тот, у кого есть право отправки писем. Смотреть можно всем.',
      ),
    ).toBeInTheDocument();
  });
});
