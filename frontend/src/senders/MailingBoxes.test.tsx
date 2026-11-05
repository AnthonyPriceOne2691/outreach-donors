/**
 * «Ящики и разгон»: строка ящика словами — ступень сейчас, ушло сегодня, следующая ступень и пишет ли.
 */

import { render, screen, within } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { describe, expect, it } from 'vitest';

import type { ForecastSender, MailingForecastView } from '../api/senders';
import { MailingBoxes } from './MailingBoxes';

const BASE: ForecastSender = {
  id: 1,
  email: 'hello@mail-a.example.test',
  domain: 'mail-a.example.test',
  enabled: true,
  warmup_day: 12,
  warmup_finished: true,
  first_letter_at: '2026-10-01T09:00:00+00:00',
  daily_cap: 20,
  allowance_today: 20,
  sent_today: 4,
  cut: false,
  refused: null,
  next_step_on: null,
  next_step_allowance: null,
};

function open(senders: ForecastSender[]) {
  render(
    <MantineProvider>
      <MailingBoxes view={{ senders } as unknown as MailingForecastView} />
    </MantineProvider>,
  );
}

function cellsOf(email: string): string[] {
  const row = screen.getByText(email).closest('tr');
  if (row === null) throw new Error(`строки ${email} нет`);
  return within(row)
    .getAllByRole('cell')
    .map((cell) => cell.textContent ?? '');
}

describe('ящики и разгон', () => {
  it('ступень сейчас, ушло сегодня, следующая ступень и пишет ли — словами', () => {
    open([
      BASE,
      {
        ...BASE,
        id: 2,
        email: 'hello@mail-b.example.test',
        warmup_day: 9,
        warmup_finished: false,
        allowance_today: 5,
        sent_today: 2,
        cut: true,
        next_step_on: '2026-11-05',
        next_step_allowance: 15,
      },
      {
        ...BASE,
        id: 3,
        email: 'team@mail-c.example.test',
        warmup_day: 1,
        warmup_finished: false,
        first_letter_at: null,
        allowance_today: 5,
        sent_today: 0,
      },
      { ...BASE, id: 4, email: 'office@mail-d.example.test', refused: 'домен на паузе до 06.11' },
      { ...BASE, id: 5, email: 'sales@mail-e.example.test', enabled: false },
      { ...BASE, id: 6, email: 'old@mail-f.example.test', warmup_day: 0 },
    ]);

    expect(cellsOf('hello@mail-a.example.test')).toEqual([
      'hello@mail-a.example.test',
      'разгон окончен: до 20 в день',
      '4 из 20',
      'выше нет',
      'пишет',
    ]);
    expect(cellsOf('hello@mail-b.example.test').slice(1)).toEqual([
      'день 9: до 5 из 20 в день',
      '2 из 5',
      'чт 05.11 — до 15 в день',
      'лимит на сутки снижен мягкими отказами',
    ]);
    expect(cellsOf('team@mail-c.example.test')[1]).toBe(
      'день 1: до 5 из 20 в день — ещё не писал, разгон с первого письма',
    );
    expect(cellsOf('office@mail-d.example.test')[4]).toBe('домен на паузе до 06.11');
    expect(cellsOf('sales@mail-e.example.test')[4]).toBe('выключен — первых писем не берёт');
    expect(cellsOf('old@mail-f.example.test')[1]).toBe('без разгона: до 20 в день');
  });
});
