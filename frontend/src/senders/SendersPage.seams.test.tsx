/**
 * Ревью стыков (A8): домен рассылки на паузе, на выдержке или записанный за другим
 * направлением не пишет — фильтр отправки (`outreach/limits.screen`) отсеивает его ящики,
 * и «Сегодня писать некому» говорит почему. Экран при этом рисовал зелёное «отправляет» и
 * считал домен в «Отправлять могут N из M»: строка лимита домена ниже говорила обратное.
 *
 * Экран — общий код: тесты помечены `it.fails` (как `xfail(strict=True)`): заработает
 * правка — тест покраснеет, и пометку снимают. Правка — PR «общее» (ревью стыков R1, A8).
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

async function openWith(shut: Record<string, unknown>) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/senders': {
      body: {
        senders: [box(1, SHUT), box(2, OPEN)],
        enabled_domains: 2,
        domains: [{ ...ROW, ...shut }],
        directions: [{ stage: 'sales', daily_limit: null, sent_today: 0 }],
      },
    },
  });
  renderWith(<AppRoutes />, '/senders');
  await screen.findByText(SHUT);
}

function cardOf(domain: string): HTMLElement {
  const card = screen.getByText(domain).closest<HTMLElement>('.mantine-Card-root');
  if (card === null) throw new Error(`карточки ${domain} нет`);
  return card;
}

describe('домен, который не пишет (A8)', () => {
  for (const [why, shut] of Object.entries(SHUT_BY)) {
    it.fails(`${why}: карточка не говорит «отправляет»`, async () => {
      await openWith(shut);

      expect(within(cardOf(SHUT)).queryByText('отправляет')).not.toBeInTheDocument();
      expect(within(cardOf(OPEN)).getByText('отправляет')).toBeInTheDocument();
    });

    it.fails(`${why}: «Отправлять могут» его не считает`, async () => {
      await openWith(shut);

      expect(screen.getByText(/Отправлять могут/)).toHaveTextContent(
        'Отправлять могут 1 из 2 доменов.',
      );
    });
  }
});
