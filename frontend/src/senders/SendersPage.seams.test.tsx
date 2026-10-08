/**
 * Ревью стыков (A8): домен рассылки на паузе, на выдержке или записанный за другим
 * направлением не пишет — фильтр отправки (`outreach/limits.screen`) отсеивает его ящики,
 * и «Сегодня писать некому» говорит почему. Экран при этом рисовал зелёное «отправляет» и
 * считал домен в «Отправлять могут N из M»: строка лимита домена ниже говорила обратное.
 *
 * Правка — PR «общее» (экран доменов и ящиков): `domainShut` — то же правило, что у фильтра
 * (`outreach/limits.domain_shut`); пометки `it.fails` сняты.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const SHUT = 'mail-shut.example.test';
const OPEN = 'mail-open.example.test';

function box(id: number, domain: string) {
  return {
    id,
    domain,
    email: `hi@${domain}`,
    stage: 'sales',
    enabled: true,
    status: 'free',
    sent_today: 0,
    daily_cap: 20,
    warmup_day: 0,
    warmup_allowance: 20,
    warmup_finished: true,
    paused_at: null,
    pause_reason: null,
  };
}

const ROW = {
  domain: SHUT,
  stage: 'sales',
  daily_limit: 30,
  sent_today: 0,
  young_until: null,
  paused_at: null,
  pause_reason: null,
};

/** Чем закрыт домен `SHUT`: строка `sending_domains` — пауза, выдержка, чужое направление. */
const SHUT_BY: Record<string, Record<string, unknown>> = {
  пауза: { paused_at: '2026-10-07T10:00:00+00:00', pause_reason: 'жалоба' },
  выдержка: { young_until: '2099-10-14T09:00:00+00:00' },
  'чужое направление': { stage: 'donors' },
};

async function open(view: Record<string, unknown>, shown = SHUT) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/senders': { body: view } });
  renderWith(<AppRoutes />, '/senders');
  await screen.findByText(shown);
}

async function openWith(shut: Record<string, unknown>) {
  await open({
    senders: [box(1, SHUT), box(2, OPEN)],
    enabled_domains: 2,
    domains: [{ ...ROW, ...shut }],
    directions: [{ stage: 'sales', daily_limit: null, sent_today: 0 }],
  });
}

function cardOf(domain: string): HTMLElement {
  const card = screen.getByText(domain).closest<HTMLElement>('.mantine-Card-root');
  if (card === null) throw new Error(`карточки ${domain} нет`);
  return card;
}

describe('домен, который не пишет (A8)', () => {
  for (const [why, shut] of Object.entries(SHUT_BY)) {
    it(`${why}: карточка не говорит «отправляет»`, async () => {
      await openWith(shut);

      expect(within(cardOf(SHUT)).queryByText('отправляет')).not.toBeInTheDocument();
      expect(within(cardOf(OPEN)).getByText('отправляет')).toBeInTheDocument();
    });

    it(`${why}: «Отправлять могут» его не считает`, async () => {
      await openWith(shut);

      expect(screen.getByText(/Отправлять могут/)).toHaveTextContent(
        'Отправлять могут 1 из 2 доменов.',
      );
    });
  }
});

/** Что говорит значок закрытого домена — словами фильтра отправки (`outreach/limits.py`);
 *  чужое направление — короче, чтобы значок не обрезался на телефоне. */
const SAYS: Record<string, string> = {
  пауза: 'домен на паузе',
  выдержка: 'домен на выдержке',
  'чужое направление': 'домен другого направления',
};

describe('чем закрыт домен — словами (A8)', () => {
  for (const [why, says] of Object.entries(SAYS)) {
    it(`${why}: вместо «отправляет» — «${says}»`, async () => {
      await openWith(SHUT_BY[why] ?? {});

      expect(within(cardOf(SHUT)).getByText(says)).toBeInTheDocument();
    });
  }

  it('строка лимита называет направление, за которым записан домен', async () => {
    await openWith(SHUT_BY['чужое направление'] ?? {});

    expect(
      within(cardOf(SHUT)).getByText(
        'лимит домена: 0 из 30 первых писем сегодня · записан за направлением «Доноры»',
      ),
    ).toBeInTheDocument();
  });

  it('все включённые закрыты — «Отправлять нечем» говорит почему, а не «все выключены»', async () => {
    await open({
      senders: [box(1, SHUT), box(2, OPEN)],
      enabled_domains: 2,
      domains: [
        { ...ROW, ...SHUT_BY['пауза'] },
        { ...ROW, domain: OPEN, ...SHUT_BY['выдержка'] },
      ],
      directions: [{ stage: 'sales', daily_limit: null, sent_today: 0 }],
    });

    expect(screen.getByText('Отправлять нечем')).toBeInTheDocument();
    expect(screen.getByText(/^Включённые домены закрыты/)).toBeInTheDocument();
    expect(screen.queryByText(/Все домены выключены/)).not.toBeInTheDocument();
  });

  it('строка своего направления, выдержка прошла, лимит дня выбран — домен пишет', async () => {
    await openWith({ young_until: '2026-01-05T09:00:00+00:00', sent_today: 30 });

    expect(within(cardOf(SHUT)).getByText('отправляет')).toBeInTheDocument();
    expect(screen.getByText(/Отправлять могут/)).toHaveTextContent(
      'Отправлять могут 2 из 2 доменов.',
    );
  });

  it('доноры: своя строка домена и домен без строки — «отправляет», счёт как было', async () => {
    await open({
      senders: [
        { ...box(1, SHUT), stage: 'donors' },
        { ...box(2, OPEN), stage: 'donors' },
      ],
      enabled_domains: 2,
      domains: [{ ...ROW, stage: 'donors' }],
      directions: [{ stage: 'donors', daily_limit: null, sent_today: 0 }],
    });

    expect(within(cardOf(SHUT)).getByText('отправляет')).toBeInTheDocument();
    expect(within(cardOf(OPEN)).getByText('отправляет')).toBeInTheDocument();
    expect(screen.getByText(/Отправлять могут/)).toHaveTextContent(
      'Отправлять могут 2 из 2 доменов.',
    );
  });

  it('один домен — «из 1 домена», а не «из 1 доменов»', async () => {
    await open({ senders: [{ ...box(1, OPEN), stage: 'donors' }], enabled_domains: 1 }, OPEN);

    expect(screen.getByText(/Отправлять могут/)).toHaveTextContent(
      'Отправлять могут 1 из 1 домена.',
    );
  });
});
