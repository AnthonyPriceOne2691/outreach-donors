/**
 * Экран писем бизнесов ниши: у скольких решённых «пишем» есть адрес и идёт ли поиск
 * (тест-группа 29.10.2026: «из 180 сделало 3 письма» — адрес был у восьми, а экран молчал).
 *
 * Очередь пуста — сборка раскрыта, и подсказка у «Собрать очередь» видна без нажатия.
 */

import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NO_STUCK_LETTERS, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Answer } from '../test/server';
import { serve } from '../test/server';

const NICHE_PATH = '/api/letters?stage=advertisers&audience=niche';
const CONTACTS_PATH = '/api/advertisers/contacts?audience=niche';

/** «Пишем» решено всем 180; адрес — у `found`, остальные ждут поиска или его не нашлось. */
function nicheView(found: number, waiting: number, missing: number) {
  return {
    stage: 'advertisers',
    audience: 'niche',
    letters: [],
    followup_default: [7, 14],
    letter_default: { subject: 'Offer for {{niche}}', zones: [] },
    blocked_by: [],
    transport: { name: 'null', real: false, problem: null },
    corridor: { min: 0.15, max: 0.25 },
    queued_total: 0,
    batch_max: 50,
    funnel: {
      'бизнесов ниши': 180,
      'ждут решения': 0,
      'решено «пишем»': 180,
      'из них с адресом': found,
      'ждут поиска адреса': waiting,
      'адреса не нашлось': missing,
      'ещё не писали': 0,
    },
  };
}

function contacts(pending: number, running: boolean): Answer {
  return { body: { pending, running, job_id: null, last: null, workers: 1 } };
}

async function openNiche(routes: Record<string, Answer | (() => Answer)>) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  localStorage.setItem('letters:stage', 'niche');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/runs/with-accepted': { body: [] },
    ...NO_STUCK_LETTERS,
    ...routes,
  });
  renderWith(<AppRoutes />, '/letters');
  await screen.findByRole('heading', { name: 'Письма' });
}

afterEach(() => {
  vi.useRealTimers();
});

describe('адрес есть не у всех решённых «пишем»', () => {
  it('шапка, строка поиска и подсказка у сборки говорят, у скольких он есть и что с остальными', async () => {
    await openNiche({
      [`GET ${NICHE_PATH}`]: { body: nicheView(8, 170, 2) },
      [`GET ${CONTACTS_PATH}`]: contacts(170, true),
    });

    expect(await screen.findByText(/адрес есть у 8 из 180 решённых/)).toBeInTheDocument();
    expect(await screen.findByText(/ждут адреса:/)).toHaveTextContent('ждут адреса: 170');
    expect(screen.getByText('· идёт поиск')).toBeInTheDocument();
    expect(
      screen.getByText(/Письма соберутся тем, у кого есть адрес: 8 из 180 решённых «пишем»/),
    ).toHaveTextContent(/Ещё 170 ждут поиска адреса.*У 2 адреса не нашлось/);
  });

  it('поиск, поставленный не отсюда, кончился — числа перечитаны, а не стоят такими, как до него', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let done = false;
    await openNiche({
      [`GET ${NICHE_PATH}`]: () => ({ body: done ? nicheView(120, 0, 60) : nicheView(8, 172, 0) }),
      [`GET ${CONTACTS_PATH}`]: () => (done ? contacts(0, false) : contacts(172, true)),
    });
    expect(await screen.findByText(/адрес есть у 8 из 180 решённых/)).toBeInTheDocument();
    expect(await screen.findByText('· идёт поиск')).toBeInTheDocument();

    done = true;
    await vi.advanceTimersByTimeAsync(5_000);

    expect(await screen.findByText(/адрес есть у 120 из 180 решённых/)).toBeInTheDocument();
    expect(screen.queryByText(/ждут адреса:/)).not.toBeInTheDocument();
  });
});
