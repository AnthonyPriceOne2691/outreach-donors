/**
 * «Переобойти спорные»: смета до траты, запуск ровно по ней, ход, итог, остановка с причиной.
 * Без права `run` кнопки нет, а смета при нуле объясняет, почему запускать нечего.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { JudgePassView } from '../api/judgePass';
import { OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { JudgePassButton, JudgePassStatus } from './JudgePass';

const IDLE: JudgePassView = {
  estimate: {
    cases: 12,
    lines: ['Спорных к разбору: 12, доноров: 3', 'Токенов: до 48 000, по 4 000 на случай'],
    refusal: null,
  },
  state: 'idle',
  done: 0,
  total: 0,
  by: null,
  ended_at: null,
  lines: [],
  why: null,
};

function open(view: JudgePassView, routes: Record<string, unknown> = {}, who: unknown = OPERATOR) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/advertisers/judge-pass': { body: view },
    ...(routes as Record<string, never>),
  });
  renderWith(
    <>
      <JudgePassButton />
      <JudgePassStatus />
    </>,
  );
  return recorded;
}

describe('переобойти спорные', () => {
  it('сначала смета, запуск — числом из неё', async () => {
    const recorded = open(IDLE, {
      'POST /api/advertisers/judge-pass': {
        status: 202,
        body: { ...IDLE, state: 'queued', total: 12 },
      },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Переобойти спорные' }));
    const dialog = await screen.findByRole('dialog', { name: 'Переобойти спорные' });
    expect(await within(dialog).findByText(/Спорных к разбору: 12/)).toBeInTheDocument();
    // Трата не начинается от открытия окна.
    expect(recorded.calls.some((call) => call.method === 'POST')).toBe(false);

    await user.click(within(dialog).getByRole('button', { name: 'Переобойти · 12' }));

    const posted = recorded.calls.find((call) => call.method === 'POST');
    expect(posted?.body).toEqual({ cases: 12 });
    expect(await screen.findByText(/ждёт свободного обходчика/)).toBeInTheDocument();
  });

  it('при нуле окно объясняет, а запускать нечем', async () => {
    open({
      ...IDLE,
      estimate: {
        cases: 0,
        lines: ['Спорных к разбору: 0, доноров: 0', 'Уже разобраны, страницы не открываем: 40'],
        refusal: null,
      },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Переобойти спорные' }));
    const dialog = await screen.findByRole('dialog');

    expect(await within(dialog).findByText(/Уже разобраны/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Переобойти · 0' })).toBeDisabled();
  });

  it('выбранный потолок — отказ словами в окне', async () => {
    open({
      ...IDLE,
      estimate: { ...IDLE.estimate, refusal: 'дневной потолок судьи рекламодателей выбран' },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Переобойти спорные' }));
    const dialog = await screen.findByRole('dialog');

    expect(await within(dialog).findByText(/потолок судьи/)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Переобойти · 12' })).toBeDisabled();
  });

  it('пока разбор идёт — ход, и кнопка не даст второй', async () => {
    open({ ...IDLE, state: 'running', done: 40, total: 90 });

    expect(await screen.findByText(/Судья разбирает спорные: 40 из 90/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Переобойти спорные' })).toBeDisabled();
  });

  it('итог — словами сервера и кто запускал', async () => {
    open({
      ...IDLE,
      state: 'done',
      done: 90,
      total: 90,
      by: 'оператор@site.com',
      ended_at: '2026-11-06T12:00:00+00:00',
      lines: ['рассужены: 88', 'токенов: 340 000'],
    });

    const line = await screen.findByText(/Разбор окончен/);
    expect(line).toHaveTextContent('оператор@site.com');
    expect(line).toHaveTextContent('рассужены: 88 · токенов: 340 000');
  });

  it('остановка — причина, сколько сделано и как продолжить', async () => {
    open({
      ...IDLE,
      state: 'stopped',
      done: 40,
      total: 90,
      why: 'дневной потолок судьи рекламодателей выбран',
    });

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Разбор остановлен');
    expect(alert).toHaveTextContent('дневной потолок судьи рекламодателей выбран');
    expect(alert).toHaveTextContent('Сделано 40 из 90');
    expect(await screen.findByRole('button', { name: 'Продолжить разбор' })).toBeEnabled();
  });

  it('обход умер вместе с задачей — «оборвался», а не вечное «идёт»', async () => {
    open({ ...IDLE, state: 'failed', done: 40, total: 90, why: 'обходчик перезапустился' });

    expect(await screen.findByText('Разбор оборвался')).toBeInTheDocument();
  });

  it('без права запускать — кто запускает, кнопки нет', async () => {
    open(IDLE, {}, { ...OPERATOR, permissions: ['view', 'advertisers'] });

    expect(await screen.findByText(/сотрудник с правом/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Переобойти спорные' })).not.toBeInTheDocument();
  });
});
