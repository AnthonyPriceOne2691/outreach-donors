/**
 * Переадресация в «Диалогах» (16.11.2026): ответ рекламодателя «пишите на partners@…» — то же
 * письмо на этот адрес в той же переписке. Сервер говорит, что решено, словами (`RedirectCard`),
 * экран показывает их как есть: в шапке переписки — строкой с меткой, в ленте — письмо названо
 * тем, что оно есть, с адресом, куда ушло, в списке — метка с подсказкой. Без переадресации
 * (продажи, доноры, рекламодатель без такого ответа) — ничего нового. Данные — выдуманные.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { REDIRECT_STEP } from '../api/redirectTypes';
import type { RedirectCard } from '../api/redirectTypes';
import type { ThreadCard } from '../api/types';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { letter, reply, threadWith } from '../test/threadFixtures';
import { letterTitle } from './ThreadFeed';
import { redirectLabel } from './RedirectLine';

const PARTNERS = 'partners@ads-partner.example.test';

const SENT: RedirectCard = {
  state: 'sent',
  words: `Переслали на ${PARTNERS} по ответу 16.11.2026 — ждём ответа нового адресата`,
  address: PARTNERS,
  reply_id: 61,
  letter_id: 77,
  note: 'адрес не на домене рекламодателя',
};

/** Рекламодатель ответил «пишите партнёрам», и наше письмо ушло на их адрес. */
const ASKED = reply({
  id: 61,
  attachments: [],
  from_email: 'editor@ads-site.example.test',
  raw_body: `Please write to ${PARTNERS}, they handle advertising.`,
  price_white: null,
  currency: null,
  placement: null,
  subject: 'Re: Advertising',
  received_at: '2026-11-16T08:00:00+00:00',
});
const FORWARDED = letter({
  id: 77,
  step: REDIRECT_STEP,
  answers_reply_id: 61,
  subject: 'Advertising on your site',
  body: 'Hello, we would like to place an advert.',
  sent_at: '2026-11-16T09:00:00+00:00',
  status: 'sent',
});

function card(redirect: RedirectCard | null): ThreadCard {
  return {
    ...threadWith().card,
    id: 9,
    host: 'ads-site.example.test',
    contact_email: 'editor@ads-site.example.test',
    stage: 'advertisers',
    state: 'waiting',
    redirect,
  };
}

async function openThread(redirect: RedirectCard | null) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/threads/9': {
      body: threadWith({
        card: card(redirect),
        letters: [letter({ sent_at: '2026-11-10T09:00:00+00:00' }), FORWARDED],
        incoming: [ASKED],
      }),
    },
  });
  renderWith(<AppRoutes />, '/threads/9');
  await screen.findByRole('heading', { name: 'ads-site.example.test' });
}

describe('переадресация — метка и слова сервера', () => {
  it('у каждого состояния своя метка; незнакомое слово — им самим, серой меткой', () => {
    expect(
      (['checking', 'waiting', 'sent', 'bounced', 'stopped', 'refused'] as const).map((state) =>
        Object.values(redirectLabel(state)).join(' · '),
      ),
    ).toEqual([
      'проверяем · gray',
      'перешлём · gray',
      'переслали · green',
      'не дошло · yellow',
      'не переслали · yellow',
      'не переслали · yellow',
    ]);
    expect(redirectLabel('later')).toEqual({ title: 'later', color: 'gray' });
  });

  it('письмо на адрес из ответа названо так, даже если у него есть ответ, на который оно идёт', () => {
    expect(letterTitle(FORWARDED)).toBe('письмо на адрес из ответа');
    expect(letterTitle(letter({ step: 1, answers_reply_id: 61 }))).toBe('наш ответ');
  });
});

describe('переадресация в переписке', () => {
  it('шапка — метка и слова сервера, пометка — в «i»; в ленте — письмо, тема и адрес', async () => {
    await openThread(SENT);

    const line = document.querySelector<HTMLElement>('[data-redirect="sent"]')!;
    expect(within(line).getByText('переслали')).toHaveClass('mantine-Badge-label');
    expect(within(line).getByText(SENT.words)).toBeInTheDocument();
    await userEvent.hover(within(line).getByRole('button', { name: 'Пометка к переадресации' }));
    expect(await screen.findByText('адрес не на домене рекламодателя')).toBeInTheDocument();

    const bubble = screen.getByRole('article', { name: 'Наше письмо: письмо на адрес из ответа' });
    expect(within(bubble).getByText(`→ ${PARTNERS}`)).toBeInTheDocument();
    expect(within(bubble).getByText('Advertising on your site')).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Наше письмо: наш ответ' })).toBeNull();
  });

  it('письмо ещё ждёт — слова приглушены; пометки нет — нет и «i»', async () => {
    await openThread({
      ...SENT,
      state: 'waiting',
      words: `Перешлём на ${PARTNERS}: письмо ждёт отправки`,
      letter_id: null,
      note: null,
    });

    const line = document.querySelector<HTMLElement>('[data-redirect="waiting"]')!;
    expect(within(line).getByText('перешлём')).toBeInTheDocument();
    const words = within(line).getByText(`Перешлём на ${PARTNERS}: письмо ждёт отправки`);
    expect(words.getAttribute('style') ?? '').toContain('dimmed');
    expect(within(line).queryByRole('button', { name: 'Пометка к переадресации' })).toBeNull();
    // Письма на новый адрес ещё нет — стрелки с адресом нет ни у одного письма ленты.
    expect(screen.queryByText(`→ ${PARTNERS}`)).toBeNull();
  });

  it('без переадресации шапка и лента прежние', async () => {
    await openThread(null);

    expect(document.querySelector('[data-redirect]')).toBeNull();
    expect(screen.queryByText(/^→ /)).toBeNull();
  });
});

describe('переадресация в списке', () => {
  function openList(threads: ThreadCard[]) {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/threads': { body: threads },
      'GET /api/replies/calibration': { body: { versions: [] } },
      'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
    });
    renderWith(<AppRoutes />, '/threads');
  }

  it('метка — после метки состояния, слова — в подсказке; у диалога без переадресации её нет', async () => {
    const plain = { ...card(null), id: 10, host: 'plain-site.example.test' };
    openList([card(SENT), plain]);

    const row = (await screen.findByText('ads-site.example.test')).closest('a')!;
    const badge = within(row).getByLabelText('Переадресация: переслали');
    const state = row.querySelector('.threadRowState')!;
    // Метка состояния — по-прежнему соседка адреса; переадресация — за ней.
    expect(row.querySelector('.threadRowWho')!.nextElementSibling).toBe(state);
    expect(state.nextElementSibling).toBe(badge);
    await userEvent.hover(badge);
    expect(await screen.findByText(SENT.words)).toBeInTheDocument();

    const other = screen.getByText('plain-site.example.test').closest('a')!;
    expect(other.querySelector('.threadRowRedirect')).toBeNull();
  });
});
