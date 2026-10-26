/**
 * «Очередь писем» и прогноз рассылки: строка «очередь уйдёт к … — подробнее в «Рассылке»» и
 * переход по ней; «Лидов без письма» — с тем, скольким из них сборка напишет. Ответы сервера —
 * записанные (`serve()`); гипотезы и числа выдуманы.
 *
 * Срок — тем же ответом, что у вкладки «Рассылка» (`FORECAST_ROUTE`): без прогноза или без срока
 * строки нет. Часы закреплены: срок фикстуры — день, и тест без закреплённых часов не должен
 * зависеть от сегодняшнего дня.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLocation } from 'react-router-dom';

import { AppRoutes } from '../App';
import type { HypothesesView, SalesQueueView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer } from '../test/server';
import { useSalesForecast } from './mailingData';
import {
  FORECAST,
  FORECAST_BLOCKED,
  FORECAST_ROUTE,
  MAILING,
  MAILING_NOW,
  MAILING_ROUTE,
} from './mailingFixtures';

const SCREEN_WAIT = { timeout: 5000 };
const QUEUE = 'GET /api/sales/queue?hypothesis=5';
const LINE = 'Очередь уйдёт к ср 04.11 — подробнее в «Рассылке»';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 5,
      name: 'Кофейни Лиссабона',
      description: null,
      created_at: '2026-10-01T10:00:00+00:00',
      leads: { new: 0, ready: 13, rejected: 0 },
      total: 13,
    },
  ],
  total: 1,
  module_enabled: true,
};

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

const QUEUE_VIEW: SalesQueueView = {
  hypothesis_id: 5,
  connected: true,
  missing: [],
  chains: [{ language: 'pt', source: 'own', missing: [], version: 'chain-pt0000000000' }],
  unwritten: 13,
  will_write: 9,
  unwritten_languages: { pt: 13 },
  queued: 4,
  stage_queued: 96,
  limit_max: 200,
  batch_max: 200,
  autosend: { enabled: false, line: 'Автоотправка выключена.' },
  autobuild: { enabled: false, line: 'Автосборка выключена.' },
  batch_off: null,
};

/** Где сейчас экран: адрес целиком. */
function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

/** Пришёл ли прогноз — тем же запросом и ключом кэша, что у строки: «строки нет» проверяется после
 *  ответа, а не до него. */
function ForecastState() {
  const forecast = useSalesForecast();
  return <output data-testid="forecast">{forecast.status}</output>;
}

async function openQueue(routes: Record<string, Answer> = {}): Promise<void> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    [QUEUE]: { body: QUEUE_VIEW },
    [FORECAST_ROUTE]: { body: FORECAST },
    [MAILING_ROUTE]: { body: MAILING },
    ...routes,
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
      <ForecastState />
    </>,
    '/sales?tab=queue&hypothesis=5',
  );
  await screen.findByText('Лидов без письма', {}, SCREEN_WAIT);
}

/** Плитка по подписи: число и строка под ним. */
function tile(title: string): HTMLElement {
  const found = screen.getByText(title).closest<HTMLElement>('.metricTile');
  if (found === null) throw new Error(`плитки «${title}» нет`);
  return found;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(MAILING_NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('очередь писем: срок из прогноза рассылки', () => {
  it('строка «очередь уйдёт к …» — днём прогноза, со ссылкой на «Рассылку»', async () => {
    await openQueue();

    const line = await screen.findByText(
      (_, node) => node?.classList.contains('queueEta') === true,
      {},
      SCREEN_WAIT,
    );
    expect(line).toHaveTextContent(LINE);
    expect(within(line).getByRole('link', { name: 'подробнее в «Рассылке»' })).toHaveAttribute(
      'href',
      '/sales?tab=mailing&hypothesis=5',
    );
  });

  it('ссылка переключает вкладку: открыта «Рассылка», гипотеза раздела — с ней', async () => {
    await openQueue();
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('link', { name: 'подробнее в «Рассылке»' }, SCREEN_WAIT),
    );

    expect(
      await screen.findByRole('region', { name: 'Когда уйдёт очередь' }, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('/sales?tab=mailing&hypothesis=5');
  });

  it('прогноз отказал — строки нет, очередь на месте', async () => {
    await openQueue({ [FORECAST_ROUTE]: { status: 503, body: { detail: 'выдуманный отказ' } } });

    await waitFor(
      () => expect(screen.getByTestId('forecast')).toHaveTextContent('error'),
      SCREEN_WAIT,
    );
    expect(document.querySelector('.queueEta')).toBeNull();
    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeEnabled();
    expect(screen.queryByText(/выдуманный отказ/)).toBeNull();
  });

  it('срока нет (письма не уходят вовсе) — строки нет: почему, говорит «Рассылка»', async () => {
    await openQueue({ [FORECAST_ROUTE]: { body: FORECAST_BLOCKED } });

    await waitFor(
      () => expect(screen.getByTestId('forecast')).toHaveTextContent('success'),
      SCREEN_WAIT,
    );
    expect(document.querySelector('.queueEta')).toBeNull();
  });
});

describe('очередь писем: «Лидов без письма» и скольким напишет сборка', () => {
  it('число — все без письма, под ним — скольким сборка напишет: обещание только про эту часть', async () => {
    await openQueue();

    const unwritten = tile('Лидов без письма');
    expect(within(unwritten).getByText('13')).toBeInTheDocument();
    expect(within(unwritten).getByText('сборка напишет 9')).toBeInTheDocument();
    expect(within(unwritten).queryByText('сборка напишет 13')).toBeNull();
  });

  it('без письма есть, а сборка не напишет никому — собирать нечего: кнопка закрыта, сказано почему', async () => {
    await openQueue({
      [QUEUE]: { body: { ...QUEUE_VIEW, unwritten: 13, will_write: 0, queued: 0 } },
    });

    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeDisabled();
    const nothing = document.querySelector<HTMLElement>('.queueNothing');
    expect(nothing).toHaveTextContent(
      'Лидов, которым сборка напишет, и писем в очереди у гипотезы нет — собирать нечего; без ' +
        'письма 13 — почему, сказано в «Рассылке».',
    );
    expect(
      within(nothing as HTMLElement).getByRole('link', { name: 'почему, сказано в «Рассылке»' }),
    ).toHaveAttribute('href', '/sales?tab=mailing&hypothesis=5');
  });

  it('сборка напишет хоть одному — кнопка открыта, строки «собирать нечего» нет', async () => {
    await openQueue({
      [QUEUE]: { body: { ...QUEUE_VIEW, unwritten: 13, will_write: 2, queued: 0 } },
    });

    expect(screen.getByRole('button', { name: 'Собрать очередь' })).toBeEnabled();
    expect(document.querySelector('.queueNothing')).toBeNull();
  });
});
