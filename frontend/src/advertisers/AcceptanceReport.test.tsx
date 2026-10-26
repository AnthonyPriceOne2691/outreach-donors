/**
 * «Приёмка по донорам» (сверка ТЗ 11.10.2026: «по донору 100 рекламодателей с контактами,
 * письма ушли 70% из них»).
 *
 * Проверяется то, ради чего вид: у каждого донора видно, пройден ли порог, а если нет —
 * чего не хватает, словами и с десятой у доли; обход — тем же видом, что на панели
 * обхода; все доноры вместе — своей строкой.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { AcceptanceView, Tally } from '../api/acceptance';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { shortOf } from './AcceptanceReport';

const SHORT: Tally = {
  found: 40,
  promoted: 38,
  with_address: 34,
  sent: 20,
  sent_share: 20 / 34,
  accepted: false,
};

const DONE: Tally = {
  found: 130,
  promoted: 130,
  with_address: 120,
  sent: 90,
  sent_share: 0.75,
  accepted: true,
};

function report(overrides: Partial<AcceptanceView> = {}): AcceptanceView {
  return {
    donors: [
      {
        host: 'big.example.test',
        crawl: {
          id: 3,
          host: 'big.example.test',
          status: 'done',
          outcome: 'ok',
          stop_reason: 'max_pages',
          pages: 1000,
          max_pages: 1000,
          articles: 900,
          links: 5000,
          resumes: 0,
          requested_by: null,
          created_at: '2026-10-10T10:00:00Z',
          finished_at: '2026-10-10T10:30:00Z',
          reason: null,
          blocked_share: 0.05,
          requests: 1100,
        },
        tally: DONE,
        supplier: false,
      },
      { host: 'small.example.test', crawl: null, tally: SHORT, supplier: false },
    ],
    total: 2,
    overall: { ...DONE, found: 160, accepted: true },
    target_with_address: 100,
    target_sent_share: 0.7,
    ...overrides,
  };
}

async function openReport(view: AcceptanceView = report()) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/advertisers/acceptance': { body: view },
  });
  renderWith(<AppRoutes />, '/advertisers?view=acceptance');
  await screen.findByRole('heading', { name: 'Приёмка по донорам' });
}

describe('приёмка по донорам', () => {
  it('порог ТЗ — словами сервера', async () => {
    await openReport();

    expect(
      screen.getByText(
        'Порог ТЗ: у донора 100 рекламодателей с адресом, и письмо ушло 70% из них.',
      ),
    ).toBeInTheDocument();
  });

  it('пройденный — мятой; обход — тем же видом, что на панели, с долей закрытых', async () => {
    await openReport();

    const row = screen.getByText('big.example.test').closest('tr')!;
    expect(within(row).getByText('пройдена')).toBeInTheDocument();
    expect(within(row).getByText(/обойдён: 1\s000 из 1\s000 страниц/)).toBeInTheDocument();
    // Почему кончился — под значком, как на панели обхода.
    expect(within(row).getByText(/^потолок 1\s000 страниц на донора$/)).toBeInTheDocument();
    expect(within(row).getByText(/закрыто 5% из 1\s100 запросов/)).toBeInTheDocument();
    expect(within(row).getByText('90 · 75%')).toBeInTheDocument();
  });

  it('не пройденный — чего не хватает, с десятой у доли', async () => {
    await openReport();

    const row = screen.getByText('small.example.test').closest('tr')!;
    expect(within(row).getByText('не пройдена')).toBeInTheDocument();
    expect(within(row).getByText('с адресом 34 из 100; ушло 58,8% из 70%')).toBeInTheDocument();
    // Не переведённые адреса не получат — это видно у числа найденных.
    expect(within(row).getByText('переведено 38')).toBeInTheDocument();
    expect(within(row).getByText('не обходили')).toBeInTheDocument();
  });

  it('все доноры вместе — справочно, без вердикта: критерий ТЗ — по донору', async () => {
    await openReport();

    const line = screen.getByText('Все доноры вместе — справочно, не критерий:').parentElement!;
    expect(
      within(line).getByText(
        'рекламодателей 160, с адресом 120, письмо ушло 90 · 75%; без поставщиков, домен — один раз',
      ),
    ).toBeInTheDocument();
    expect(within(line).queryByText(/пройдена/)).toBeNull();
  });

  it('поставщик — помечен, а не «не пройдена»', async () => {
    const view = report();
    await openReport({
      ...view,
      donors: [
        ...view.donors,
        { host: 'partner.example.test', crawl: null, tally: SHORT, supplier: true },
      ],
      total: 3,
    });

    const row = screen.getByText('partner.example.test').closest('tr')!;
    expect(within(row).getByText('поставщик')).toBeInTheDocument();
    expect(
      within(row).getByText('его рекламодателей не трогаем — не считается'),
    ).toBeInTheDocument();
    expect(within(row).queryByText('не пройдена')).toBeNull();
  });

  it('доноров больше, чем строк, — «показано N из M»', async () => {
    await openReport(report({ total: 315 }));

    expect(
      screen.getByText('Показано 2 из 315: сверху — у кого рекламодателей больше.'),
    ).toBeInTheDocument();
  });

  it('обойдённых нет — где обход начинается', async () => {
    await openReport(
      report({ donors: [], total: 0, overall: { ...SHORT, found: 0, accepted: false } }),
    );

    expect(screen.getByText(/Обойдённых доноров нет/)).toBeInTheDocument();
    expect(screen.queryByText(/Все доноры вместе/)).toBeNull();
  });
});

describe('чего не хватает до порога', () => {
  const view = report();

  it.each([
    [{ ...SHORT, with_address: 100, sent: 69, sent_share: 0.69 }, ['ушло 69% из 70%']],
    [{ ...SHORT, with_address: 99, sent: 99, sent_share: 1 }, ['с адресом 99 из 100']],
    // Адресов нет — и доли нет: про неё молчим, а не пишем «ушло —».
    [{ ...SHORT, with_address: 0, sent: 0, sent_share: null }, ['с адресом 0 из 100']],
    [{ ...SHORT, with_address: 100, sent: 70, sent_share: 0.7 }, []],
  ])('%o → %o', (tally, said) => {
    expect(shortOf(tally, view)).toEqual(said);
  });
});
