/**
 * Автокруг в «К письму»: выключатель, суточный потолок писем и итог последнего круга.
 *
 * Проверяется то, чем выключатель может подвести: включиться без вопроса, назвавшего, что круг
 * сделает и когда; выключаться дольше одного нажатия; отправить потолок за границей сервера или
 * не тот, что в поле; дать кнопки тому, у кого нет прав на перевод и отправку разом; промолчать,
 * что рубильник сервиса выключен; показать итог круга не словами сервера.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { AutoRound as Round } from '../api/advertisersAutoRound';
import type { Me } from '../api/types';
import { formatTime } from '../format';
import { ADMIN, AUTO_ROUND_OFF, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { AutoRound, roundLine, switchWords, watched } from './AutoRound';

const URL = '/api/advertisers/auto-round';

const ON: Round = {
  ...AUTO_ROUND_OFF,
  switch: {
    enabled: true,
    letters: 100,
    set_by: 'владелец@example.com',
    set_at: '2026-11-17T05:10:00+00:00',
  },
  ran_today: true,
  first: 'Первый круг — завтра утром, после ночного шага судьи.',
  last: {
    day: '2026-11-17',
    status: 'done',
    at: '2026-11-17T05:12:00+00:00',
    words:
      'переведено 24 · адресов найдено 20 (платных 9) · собрано 20 из 40 · очередь уходит сама',
    going: false,
    lines: ['рассылка «Автокруг 17.11 — рекламодатели по ссылке», писем в сутки — до 100'],
  },
};

function open(answer: Round, who: Me = ADMIN, routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    [`GET ${URL}`]: { body: answer },
    ...(routes as Record<string, never>),
  });
  renderWith(<AutoRound />);
  return recorded;
}

function posted(recorded: ReturnType<typeof serve>): unknown[] {
  return recorded.calls.filter((call) => call.method === 'POST').map((call) => call.body);
}

describe('автокруг: выключатель', () => {
  it('выключен — включение только после вопроса, который называет письма и первый круг', async () => {
    const recorded = open(AUTO_ROUND_OFF, ADMIN, { [`POST ${URL}`]: { body: ON } });
    const user = userEvent.setup();

    expect(await screen.findByText(switchWords(AUTO_ROUND_OFF))).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Включить автокруг…' }));

    expect(await screen.findByText(/соберёт до 100 писем/)).toBeInTheDocument();
    expect(screen.getByText(/в ближайшие минуты/)).toBeInTheDocument();
    expect(posted(recorded)).toEqual([]);

    await user.click(screen.getByRole('button', { name: 'Включить' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ enabled: true, letters: 100 }]));
    expect(await screen.findByText(/Каждый день сам, до 100 писем в сутки/)).toBeInTheDocument();
  });

  it('включён — выключается одним нажатием, с тем же потолком', async () => {
    const recorded = open(ON, ADMIN, { [`POST ${URL}`]: { body: AUTO_ROUND_OFF } });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Выключить' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ enabled: false, letters: 100 }]));
  });

  it('потолок у включённого — «Сохранить» с числом из поля', async () => {
    const recorded = open(ON, ADMIN, {
      [`POST ${URL}`]: { body: { ...ON, switch: { ...ON.switch, letters: 40 } } },
    });
    const user = userEvent.setup();
    const field = await screen.findByLabelText('Писем в сутки');

    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument();
    await user.clear(field);
    await user.type(field, '40');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(posted(recorded)).toEqual([{ enabled: true, letters: 40 }]));
  });

  it('потолок за границей сервера — отказ словами, и включить нельзя', async () => {
    open(AUTO_ROUND_OFF);
    const user = userEvent.setup();
    const field = await screen.findByLabelText('Писем в сутки');

    await user.clear(field);
    await user.type(field, '600');

    expect(await screen.findByText(/от 1 до 500/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Включить автокруг…' })).toBeDisabled();
  });

  it('отказ сервера — словами у выключателя', async () => {
    open(ON, ADMIN, {
      [`POST ${URL}`]: { status: 403, body: { detail: 'Нет права «отправлять письма»' } },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Выключить' }));

    expect(await screen.findByText('Нет права «отправлять письма»')).toBeInTheDocument();
  });

  it('без права отправки — строки есть, кнопок нет', async () => {
    open(ON, OPERATOR);

    expect(await screen.findByText(roundLine(ON.last!))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Выключить' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Писем в сутки')).not.toBeInTheDocument();
  });

  it('рубильник сервиса выключен — сказано словами, выключателя нет', async () => {
    open({ ...AUTO_ROUND_OFF, available: false });

    expect(await screen.findByText(/Автокруг выключен настройкой сервиса/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Включить автокруг…' })).not.toBeInTheDocument();
  });
});

describe('автокруг: итог', () => {
  it('строка — дата круга, время по часам смотрящего и слова сервера', () => {
    expect(roundLine(ON.last!)).toBe(
      `Автокруг 17.11, ${formatTime('2026-11-17T05:12:00+00:00')}: ` +
        'переведено 24 · адресов найдено 20 (платных 9) · собрано 20 из 40 · очередь уходит сама',
    );
    expect(roundLine({ ...ON.last!, at: null, words: 'ждёт поиск адресов' })).toBe(
      'Автокруг 17.11: ждёт поиск адресов',
    );
  });

  it('строка перечитывается, пока круг идёт или вот-вот начнётся', () => {
    expect(watched(undefined)).toBe(false);
    expect(watched(ON)).toBe(false);
    expect(watched({ ...ON, last: { ...ON.last!, going: true } })).toBe(true);
    expect(watched({ ...ON, ran_today: false })).toBe(true);
    expect(watched({ ...AUTO_ROUND_OFF })).toBe(false);
  });
});
