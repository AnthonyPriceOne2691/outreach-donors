/**
 * Домены рассылки: переключение и то, что на нём написано.
 *
 * Главное здесь — что включение обещает разгон с начала. Обещание
 * на кнопке важнее строчки в документации: тот, кто включает домен
 * после жалобы, документацию в этот момент не читает.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Call } from '../test/server';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const SENDERS = {
  senders: [
    {
      id: 1,
      domain: 'mail-alpha.example.test',
      email: 'outreach1@mail-alpha.example.test',
      enabled: true,
      status: 'free',
      sent_today: 6,
      daily_cap: 20,
      warmup_day: 0,
      warmup_allowance: 20,
      warmup_finished: true,
      paused_at: null,
      pause_reason: null,
    },
    {
      id: 2,
      domain: 'mail-delta.example.test',
      email: 'outreach1@mail-delta.example.test',
      enabled: false,
      status: 'paused',
      sent_today: 0,
      daily_cap: 20,
      warmup_day: 0,
      warmup_allowance: 20,
      warmup_finished: true,
      paused_at: '2026-09-17T10:00:00+00:00',
      pause_reason: 'доля отказов 7% — парковка',
    },
  ],
  enabled_domains: 1,
};

async function openSenders(routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/senders': { body: SENDERS },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/senders');
  await screen.findByText('mail-alpha.example.test');
  return recorded;
}

describe('домены рассылки', () => {
  it('карточка выключенного домена обещает разгон с начала', async () => {
    await openSenders();

    expect(screen.getByRole('button', { name: 'Включить заново' })).toBeInTheDocument();
    expect(screen.getByText(/начинает разгон с начала/)).toBeInTheDocument();
  });

  it('причина парковки видна без раскрытия карточки', async () => {
    await openSenders();

    // По ней решают, включать домен обратно, — прятать её за раскрытием
    // значит требовать лишнего щелчка ровно в тот момент, когда некогда.
    expect(screen.getByText('доля отказов 7% — парковка')).toBeInTheDocument();
  });

  it('ящики домена прячутся за раскрытием', async () => {
    await openSenders();
    const user = userEvent.setup();

    const toggle = screen.getByLabelText('Подробности mail-alpha.example.test');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    await user.click(toggle);

    expect(screen.getByLabelText('Свернуть mail-alpha.example.test')).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(screen.getByText('outreach1@mail-alpha.example.test')).toBeInTheDocument();
  });

  it('включение уходит на сервер по каждому ящику домена', async () => {
    const recorded = await openSenders({
      'POST /api/senders/2/enable': { body: { ...SENDERS.senders[1], enabled: true } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Включить заново' }));

    await screen.findByText(/разгон начался заново/);
    const calls = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(calls).toHaveLength(1);
    expect(calls[0]?.path).toBe('/api/senders/2/enable');
  });

  it('оператору раздел не открывается', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: OPERATOR } });
    renderWith(<AppRoutes />, '/senders');

    expect(await screen.findByText('Раздел недоступен')).toBeInTheDocument();
    expect(screen.getByText(/«домены рассылки»/)).toBeInTheDocument();
  });
});

describe('отправлено сегодня', () => {
  it('полоса — общий `Meter` сервиса, а не серая дорожка Mantine', async () => {
    await openSenders();

    const bar = screen.getByRole('progressbar', { name: 'Отправлено сегодня 6 из 20' });
    // Дорожка общей полосы — светлая ступень цвета заполнения, заданная
    // классом `meter`; у голого `Progress` её нет, и дорожка серая.
    expect(bar.closest('.meter')).not.toBeNull();
  });
});

describe('домены по этапу (4.5a)', () => {
  const domain = 'mail-sales.example.test';
  const SALES_BOX = { ...SENDERS.senders[0], id: 3, domain, email: `hi@${domain}`, stage: 'sales' };
  const BY_STAGE = {
    senders: [{ ...SENDERS.senders[0], stage: 'donors' }, SALES_BOX],
    enabled_domains: 2,
    domains: [
      {
        domain,
        stage: 'sales',
        daily_limit: 30,
        sent_today: 4,
        paused_at: null,
        pause_reason: null,
        young_until: '2099-10-14T09:00:00+00:00',
      },
    ],
    directions: [
      { stage: 'donors', daily_limit: null, sent_today: 6 },
      { stage: 'sales', daily_limit: 50, sent_today: 4 },
    ],
  };

  it('разделы этапов, пометка продаж, лимит домена и направления', async () => {
    await openSenders({ 'GET /api/senders': { body: BY_STAGE } });

    expect(screen.getByRole('heading', { name: 'Доноры' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Продажи' })).toBeInTheDocument();
    expect(screen.getByText('продажи')).toBeInTheDocument();
    expect(
      screen.getByText(/лимит домена: 4 из 30 первых писем сегодня · на выдержке до 14\.10\.2099/),
    ).toBeInTheDocument();
    expect(screen.getByText('Лимит направления: 4 из 50 первых писем сегодня')).toBeInTheDocument();
  });

  it('у одних доноров экран прежний: без разделов и лимитов', async () => {
    await openSenders();

    expect(screen.queryByRole('heading', { name: 'Доноры' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Лимит направления/)).not.toBeInTheDocument();
    expect(screen.queryByText(/лимит домена/)).not.toBeInTheDocument();
  });
});
