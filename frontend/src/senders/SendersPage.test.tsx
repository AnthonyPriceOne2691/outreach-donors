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

  it('оператор смотрит, но не переключает: кнопок нет, экран говорит, почему', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: OPERATOR }, 'GET /api/senders': { body: SENDERS } });
    renderWith(<AppRoutes />, '/senders');

    expect(await screen.findByText('mail-alpha.example.test')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Выключить' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Включить заново' })).toBeNull();
    expect(screen.getByText(/Только просмотр/)).toBeInTheDocument();
  });

  it('с правом «Домены рассылки» строки про просмотр нет', async () => {
    await openSenders();

    expect(screen.queryByText(/Только просмотр/)).toBeNull();
  });
});

describe('отправлено сегодня', () => {
  it('полоса — общий `Meter` сервиса, а не серая дорожка Mantine', async () => {
    await openSenders();

    const bar = screen.getByRole('progressbar', { name: 'Первых писем сегодня: 6 из 20' });
    // Дорожка общей полосы — светлая ступень цвета заполнения, заданная
    // классом `meter`; у голого `Progress` её нет, и дорожка серая.
    expect(bar.closest('.meter')).not.toBeNull();
  });

  it('счёт ящика — первые письма, как у капа: подпись так и говорит (A7)', async () => {
    await openSenders();

    // Сервер считает карточке первые письма — тем же счётом, что кап и разгон; добивки
    // в него не входят (свой часовой потолок), и «25 из 20» на экране не бывает.
    expect(screen.getByText('1 ящ. · 6 из 20 первых писем сегодня')).toBeInTheDocument();
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

  it('настройка лимита направления не прочитана — строка называет её, а не «0 из 0»', async () => {
    // Опечатка в .env: продажам ноль первых писем, и «0 из 0 первых писем сегодня» читалось
    // бы как выбранный лимит (ревью продаж 13.10.2026). Слова — по смыслу отказа отправки.
    const directions = [
      { stage: 'donors', daily_limit: null, sent_today: 6, unread: null },
      { stage: 'sales', daily_limit: 0, sent_today: 0, unread: 'OUTREACH_SALES_DAILY_LIMIT' },
    ];
    await openSenders({ 'GET /api/senders': { body: { ...BY_STAGE, directions } } });

    expect(screen.getByText(/^Лимит направления: настройка/)).toHaveTextContent(
      'Лимит направления: настройка OUTREACH_SALES_DAILY_LIMIT не прочитана — первых писем ноль, пока её не поправят в .env',
    );
    expect(screen.queryByText(/Лимит направления: 0 из 0/)).not.toBeInTheDocument();
  });

  it.each([
    ['без права «Продажи» ящиков продаж нет — даже присланных сервером', false],
    ['с правом «Продажи» раздел продаж на месте', true],
  ])('%s', async (_, sales) => {
    // Решение Anthony 10.10.2026 (П2б): сервер без права ящики продаж не отдаёт и переключить
    // не даст, а экран не показывает их и сам — ответ в кэше мог прийти до того, как право сняли.
    const permissions = ADMIN.permissions.filter((one) => sales || one !== 'sales');
    await openSenders({
      'GET /api/auth/me': { body: { ...ADMIN, permissions } },
      'GET /api/senders': { body: BY_STAGE },
    });

    expect(screen.queryByText(domain) !== null).toBe(sales);
    expect(screen.queryByRole('heading', { name: 'Продажи' }) !== null).toBe(sales);
    expect(screen.queryByText(/Лимит направления: 4 из 50/) !== null).toBe(sales);
  });

  it('у одних доноров экран прежний: без разделов и лимитов, «Отправлять нечем» — как было', async () => {
    const off = SENDERS.senders.map((box) => ({ ...box, enabled: false }));
    await openSenders({
      'GET /api/senders': { body: { ...SENDERS, senders: off, enabled_domains: 0 } },
    });

    expect(screen.getByText('Отправлять нечем')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Доноры' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Лимит направления/)).not.toBeInTheDocument();
    expect(screen.queryByText(/лимит домена/)).not.toBeInTheDocument();
  });

  describe('часть продаж — группа меню «Продажи» (11.10.2026)', () => {
    async function openPart(body: unknown) {
      localStorage.setItem(TOKEN_KEY, 'пропуск');
      serve({
        'GET /api/auth/me': { body: ADMIN },
        'GET /api/senders': { body },
        'GET /api/letters/forecast?stage=sales&days=1&back=0': {
          body: { stage: 'sales', senders: [] },
        },
      });
      renderWith(<AppRoutes />, '/senders?stage=sales');
      return screen.findByRole('heading', { name: 'Домены рассылки продаж' });
    }

    it('только домены продаж и их лимит, без донорских', async () => {
      expect(await openPart(BY_STAGE)).toBeInTheDocument();

      expect(screen.getByText(domain)).toBeInTheDocument();
      expect(screen.queryByText('mail-alpha.example.test')).not.toBeInTheDocument();
      expect(
        screen.getByText('Лимит направления: 4 из 50 первых писем сегодня'),
      ).toBeInTheDocument();
    });

    it('доменов продаж нет — так и сказано, а не «все выключены»', async () => {
      expect(await openPart(SENDERS)).toBeInTheDocument();

      expect(screen.getByText(/Доменов продаж пока нет/)).toBeInTheDocument();
      expect(screen.queryByText('Отправлять нечем')).not.toBeInTheDocument();
    });
  });

  describe('части этапов: ящики — под доменами (просьба Anthony 05.11.2026)', () => {
    const ADVERTISER_DOMAIN = 'mail-adv.example.test';
    const STAGES_BODY = {
      senders: [
        { ...SENDERS.senders[0], stage: 'donors' },
        {
          ...SENDERS.senders[0],
          id: 4,
          domain: ADVERTISER_DOMAIN,
          email: `hi@${ADVERTISER_DOMAIN}`,
          stage: 'advertisers',
        },
        SALES_BOX,
      ],
      enabled_domains: 3,
      domains: [],
      directions: [],
    };
    const FORECAST_OF = (stage: string) => `GET /api/letters/forecast?stage=${stage}&days=1&back=0`;
    const BOX = {
      id: 4,
      email: `hi@${ADVERTISER_DOMAIN}`,
      domain: ADVERTISER_DOMAIN,
      enabled: true,
      warmup_day: 3,
      warmup_finished: false,
      first_letter_at: '2026-11-02T09:00:00+00:00',
      daily_cap: 20,
      allowance_today: 12,
      sent_today: 5,
      cut: false,
      refused: null,
      next_step_on: null,
      next_step_allowance: null,
    };

    function openStage(stage: string, forecast: unknown) {
      localStorage.setItem(TOKEN_KEY, 'пропуск');
      serve({
        'GET /api/auth/me': { body: ADMIN },
        'GET /api/senders': { body: STAGES_BODY },
        [FORECAST_OF(stage)]: forecast as never,
      });
      renderWith(<AppRoutes />, `/senders?stage=${stage}`);
    }

    it('рекламодатели: свой заголовок, только их домены, ниже — ящики и разгон', async () => {
      openStage('advertisers', { body: { stage: 'advertisers', senders: [BOX] } });

      await screen.findByRole('heading', { name: 'Домены рассылки рекламодателей' });
      expect(await screen.findByText(ADVERTISER_DOMAIN)).toBeInTheDocument();
      expect(screen.queryByText('mail-alpha.example.test')).toBeNull();
      expect(screen.queryByText(domain)).toBeNull();
      expect(await screen.findByRole('region', { name: 'Ящики и разгон' })).toBeInTheDocument();
      // Этап уже в заголовке экрана: пометки у каждой карточки нет.
      expect(screen.queryByText('рекламодатель')).toBeNull();
    });

    it('доноры: свой заголовок и только их домены', async () => {
      openStage('donors', { body: { stage: 'donors', senders: [] } });

      await screen.findByRole('heading', { name: 'Домены рассылки доноров' });
      expect(await screen.findByText('mail-alpha.example.test')).toBeInTheDocument();
      expect(screen.queryByText(ADVERTISER_DOMAIN)).toBeNull();
    });

    it('ящиков этапа нет — блока нет, а строка экрана называет этап', async () => {
      localStorage.setItem(TOKEN_KEY, 'пропуск');
      serve({
        'GET /api/auth/me': { body: ADMIN },
        'GET /api/senders': { body: { ...STAGES_BODY, senders: [SALES_BOX] } },
        [FORECAST_OF('advertisers')]: { body: { stage: 'advertisers', senders: [] } },
      });
      renderWith(<AppRoutes />, '/senders?stage=advertisers');

      expect(await screen.findByText(/Доменов рекламодателей пока нет/)).toBeInTheDocument();
      expect(screen.queryByText('Отправлять нечем')).toBeNull();
      expect(screen.queryByRole('region', { name: 'Ящики и разгон' })).toBeNull();
    });

    it('отказ прогноза — в блоке ящиков, домены выше живут', async () => {
      openStage('advertisers', { status: 503, body: { detail: 'Прогноз не ответил' } });

      expect(await screen.findByText('Ящики не загрузились')).toBeInTheDocument();
      expect(screen.getByText(ADVERTISER_DOMAIN)).toBeInTheDocument();
    });
  });
});
