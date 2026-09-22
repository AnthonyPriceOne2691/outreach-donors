/**
 * Ручная проверка кандидатов в рекламодатели.
 *
 * Экран сделан ради одного числа: допуск по ложным рекламодателям —
 * десять процентов, а скоринг судит по признакам, выведенным из замера
 * на одной нише. Поэтому проверяется не «рисуется ли таблица», а то,
 * ради чего она нарисована: видны ли причины балла, видна ли ссылка,
 * под которую будет написано письмо, и уходит ли решение на сервер.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

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

const QUEUE = {
  rows: [CANDIDATE],
  waiting: 1,
  counts: { bought: 6, pending: 1, skipped: 14, blocked: 12 },
};

async function openScreen(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/advertisers': { body: QUEUE },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/advertisers');
  await screen.findByText('advertiser.example');
  return recorded;
}

describe('ручная проверка рекламодателей', () => {
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
    await openScreen({}, { ...(OPERATOR as object), permissions: ['view'] });

    expect(screen.getByText('advertiser.example')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Пишем' })).not.toBeInTheDocument();
  });

  it('пустая очередь объясняет, кто сюда попадает', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/advertisers': { body: { rows: [], waiting: 0, counts: {} } },
    });
    renderWith(<AppRoutes />, '/advertisers');

    expect(await screen.findByText(/Спорных нет/)).toBeInTheDocument();
  });
});
