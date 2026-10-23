/**
 * «Письма донорам» и «Письма рекламодателям» — экран писем, суженный адресом (`?stage=…`,
 * замечание тест-группы 23.10.2026 «разделить вкладки Письма и Диалог» у Этапа 1 и
 * Этапа 2). Проверяется: заголовок части — её пункт меню; у доноров переключателя нет; у
 * Этапа 2 — две аудитории; запомненный выбор не уводит в чужую часть; прежний адрес без
 * части — экран со всеми тремя адресатами.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NO_STUCK_LETTERS, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

/** Очередь из одного письма — адресату с этим доменом. */
function queue(stage: string, host: string) {
  return {
    stage,
    letters: [
      {
        id: host.length,
        host,
        email: `editor@${host}`,
        campaign: 'Демонстрация',
        status: 'queued',
        subject: `About ${host}`,
        body: 'Hello,\n\nA short letter.',
        uniqueness: 0.19,
        verdict: null,
        followups: [],
      },
    ],
    followup_default: [7, 14],
    letter_default: { subject: 'Hello', zones: [] },
    blocked_by: [],
    transport: { name: 'null', real: false, problem: null },
    corridor: { min: 0.15, max: 0.25 },
    funnel: { 'ещё не писали': 1 },
    queued_total: 1,
    batch_max: 20,
  };
}

const DONORS = '/api/letters';
const LINKS = '/api/letters?stage=advertisers';
const NICHE = '/api/letters?stage=advertisers&audience=niche';

async function openLetters(path: string) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    [`GET ${DONORS}`]: { body: queue('donors', 'digest.example.test') },
    [`GET ${LINKS}`]: { body: queue('advertisers', 'brand.example.test') },
    [`GET ${NICHE}`]: { body: queue('advertisers', 'bookie.example.test') },
    'GET /api/runs/with-accepted': { body: [] },
    ...NO_STUCK_LETTERS,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByRole('navigation', { name: 'Разделы' });
  return recorded;
}

const asked = (recorded: Awaited<ReturnType<typeof openLetters>>) =>
  recorded.calls.filter((call) => call.path.startsWith('/api/letters?') || call.path === DONORS);

const menuLink = (name: string) =>
  within(screen.getByRole('navigation', { name: 'Разделы' })).getByRole('link', { name });

/** Подписи переключателя «Кому письма» по порядку. */
const options = () =>
  within(screen.getByRole('radiogroup', { name: 'Кому письма' }))
    .getAllByRole<HTMLInputElement>('radio')
    .map((radio) => radio.labels?.[0]?.textContent);

describe('письма по частям', () => {
  it('«Письма донорам» — одни доноры: переключателя нет, пояснение есть', async () => {
    // Выбор, запомненный на прежнем экране, в чужую часть не уводит.
    localStorage.setItem('letters:stage', 'niche');
    const recorded = await openLetters('/letters?stage=donors');

    expect(await screen.findByRole('heading', { name: 'Письма донорам' })).toBeInTheDocument();
    expect(await screen.findAllByText('digest.example.test')).not.toHaveLength(0);
    expect(screen.queryByRole('radiogroup', { name: 'Кому письма' })).not.toBeInTheDocument();
    expect(screen.getByText(/Очередь на отправку/)).toBeInTheDocument();
    expect(asked(recorded).map((call) => call.path)).toEqual([DONORS]);
    expect(menuLink('Письма донорам')).toHaveAttribute('data-active');
  });

  it('«Письма рекламодателям» — две аудитории Этапа 2, выбор помнится', async () => {
    localStorage.setItem('letters:stage', 'donors');
    const recorded = await openLetters('/letters?stage=advertisers');

    expect(
      await screen.findByRole('heading', { name: 'Письма рекламодателям' }),
    ).toBeInTheDocument();
    expect(options()).toEqual(['По найденной ссылке', 'Бизнесам ниши']);
    expect(screen.getByRole('radio', { name: 'По найденной ссылке' })).toBeChecked();
    expect(await screen.findAllByText('brand.example.test')).not.toHaveLength(0);
    expect(menuLink('Письма рекламодателям')).toHaveAttribute('data-active');

    await userEvent.setup().click(screen.getByRole('radio', { name: 'Бизнесам ниши' }));

    expect(await screen.findAllByText('bookie.example.test')).not.toHaveLength(0);
    expect(asked(recorded).map((call) => call.path)).toEqual([LINKS, NICHE]);
    expect(localStorage.getItem('letters:stage')).toBe('niche');
  });

  it('из «Писем донорам» пунктом меню — в Этап 2, на запомненную аудиторию', async () => {
    localStorage.setItem('letters:stage', 'niche');
    await openLetters('/letters?stage=donors');
    await screen.findAllByText('digest.example.test');

    await userEvent.setup().click(menuLink('Письма рекламодателям'));

    expect(
      await screen.findByRole('heading', { name: 'Письма рекламодателям' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Бизнесам ниши' })).toBeChecked();
    expect(await screen.findAllByText('bookie.example.test')).not.toHaveLength(0);
    await waitFor(() => expect(menuLink('Письма донорам')).not.toHaveAttribute('data-active'));
  });

  it('прежний адрес без части — все три адресата, пункт меню не подсвечен', async () => {
    await openLetters('/letters');

    expect(await screen.findByRole('heading', { name: 'Письма' })).toBeInTheDocument();
    expect(options()).toEqual(['Донорам', 'Рекламодателям', 'Бизнесам ниши']);
    expect(menuLink('Письма донорам')).not.toHaveAttribute('data-active');
    expect(menuLink('Письма рекламодателям')).not.toHaveAttribute('data-active');
  });
});
