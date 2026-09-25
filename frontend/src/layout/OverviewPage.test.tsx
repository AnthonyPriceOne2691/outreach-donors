/**
 * Обзор: сторож тишины и сводка.
 *
 * Сторож стоит на главной, потому что поломка этого класса не
 * показывает себя нигде: человек увидит, что «всё тихо», и закроет
 * вкладку. Поэтому проверяется и то, что тревога видна, и то, что
 * в тишине по делу экран не пугает.
 *
 * Сводка проверяется по тому, по чему решают: сколько ждёт человека
 * и куда ведёт каждая плитка, в каком состоянии почта и сколько
 * потрачено. Сами числа считает сервер — здесь проверяется, что экран
 * показывает их, а не свои.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { OverviewView } from '../api/types';
import { AppRoutes } from '../App';
import { ADMIN, OVERVIEW, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Answer } from '../test/server';
import { serve } from '../test/server';

async function openOverview(alarms: unknown[] = [], overview: Answer = { body: OVERVIEW }) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/watchdog': { body: { alarms } },
    'GET /api/overview': overview,
  });
  renderWith(<AppRoutes />, '/');
  // «Обзор» есть и в меню, и в заголовке карточки — ждём заголовок.
  await screen.findByRole('heading', { name: 'Обзор' });
}

/** Плитка сводки по её подписи — она же ссылка целиком. */
async function tile(title: string): Promise<HTMLElement> {
  const label = await screen.findByText(title);
  const link = label.closest('a');
  if (link === null) throw new Error(`Плитка «${title}» не ссылка`);
  return link;
}

describe('сторож тишины на обзоре', () => {
  it('тревога видна целиком, вместе с тем, что делать', async () => {
    await openOverview([
      {
        code: 'delivery-silence',
        title: 'Платформа молчит о доставке',
        detail: '12 писем отправлены больше 6 часов назад, и ни по одному не пришло события.',
      },
    ]);

    expect(await screen.findByText('Платформа молчит о доставке')).toBeInTheDocument();
    expect(screen.getByText(/ни по одному не пришло события/)).toBeInTheDocument();
  });

  it('в тишине по делу экран не пугает', async () => {
    await openOverview([]);

    expect(await screen.findByText(/Тихо и правильно/)).toBeInTheDocument();
  });
});

describe('сводка на главной', () => {
  it('работа для человека — плитками, и каждая ведёт туда, где её делают', async () => {
    await openOverview();

    const review = await tile('Рассмотреть домены');
    expect(review).toHaveAttribute('href', '/runs/18/review');
    expect(within(review).getByText('394')).toBeInTheDocument();
    expect(within(review).getByText('в очереди №18')).toBeInTheDocument();

    expect(await tile('Разобрать цены')).toHaveAttribute('href', '/threads');
    expect(await tile('Заполнить формы')).toHaveAttribute('href', '/forms');
    expect(await tile('Спорные рекламодатели')).toHaveAttribute('href', '/advertisers');
  });

  it('пустая очередь говорит, что пусто, а рассмотрение ведёт к прогонам', async () => {
    const quiet: OverviewView = {
      ...OVERVIEW,
      waiting: { review: 0, review_runs: [], prices: 0, leads: 0, forms: 0, advertisers: 0 },
    };
    await openOverview([], { body: quiet });

    const review = await tile('Рассмотреть домены');
    expect(review).toHaveAttribute('href', '/run');
    expect(within(review).getByText('очереди разобраны')).toBeInTheDocument();
    expect(screen.getByText('все ответы разобраны')).toBeInTheDocument();
  });

  it('несколько очередей названы по номерам, новые первыми', async () => {
    await openOverview([], {
      body: { ...OVERVIEW, waiting: { ...OVERVIEW.waiting, review_runs: [24, 21, 18, 17] } },
    });

    const review = await tile('Рассмотреть домены');
    expect(review).toHaveAttribute('href', '/runs/24/review');
    expect(within(review).getByText('в очередях №24, №21, №18 и ещё 1')).toBeInTheDocument();
  });

  it('почта не подключена — сказано спокойно, а не как поломка', async () => {
    await openOverview();

    expect(await screen.findByText('почта не подключена')).toBeInTheDocument();
    expect(screen.getByText(/подключается на рабочем сервере/)).toBeInTheDocument();
  });

  it('последний прогон — своими числами, без второго «Рассмотреть»', async () => {
    await openOverview();

    expect(await screen.findByText('Прогон №18')).toBeInTheDocument();
    expect(
      screen.getByText(/ключей 100 · доменов 535 · потрачено 16 998 юн\./),
    ).toBeInTheDocument();
    // Очередь этого прогона уже в «Ждут человека»: второе «Рассмотреть»
    // на том же экране читалось бы как сбой. Ссылка такая ровно одна — плитка.
    expect(screen.getAllByRole('link', { name: /^Рассмотреть/ })).toHaveLength(1);
  });

  it('расход юнитов — полосой и словами', async () => {
    await openOverview();

    const meter = await screen.findByRole('progressbar', { name: 'Юниты Ahrefs с начала месяца' });
    expect(meter).toHaveAttribute('aria-valuenow', String((42716 / 100000) * 100));
    expect(screen.getByText('42 716')).toBeInTheDocument();
  });

  it('отказ сервера показан целиком, а не пустой главной', async () => {
    await openOverview([], {
      status: 503,
      body: { detail: 'База недоступна — повторите через минуту' },
    });

    expect(await screen.findByText('Сводка не загрузилась')).toBeInTheDocument();
    expect(screen.getByText('База недоступна — повторите через минуту')).toBeInTheDocument();
  });
});
