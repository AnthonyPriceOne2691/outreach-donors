/**
 * «Ящики и разгон»: строка ящика коротко — ступень сейчас, ушло сегодня, следующая ступень и пишет ли;
 * колонка, одинаковая во всех строках, — строкой над таблицей (разведка прода 10.11.2026: до шести
 * строк в ячейке, адрес и счёт в словах фильтра повторяли соседние колонки).
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { StageBox, StageBoxesView } from '../api/senders';
import { renderWith } from '../test/render';
import { MailingBoxes } from './MailingBoxes';

const BASE: StageBox = {
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
  refused_short: null,
  next_step_on: null,
  next_step_allowance: null,
};

/** Ящик, которому сегодня снижен лимит, а потом и выбран: слова фильтра — как у сервера. */
const CUT_AND_SPENT: StageBox = {
  ...BASE,
  id: 7,
  email: 'box@mail-g.example.test',
  allowance_today: 10,
  sent_today: 11,
  cut: true,
  refused: 'ящику снижен лимит на сутки (мягкие отказы): box@mail-g.example.test — 11 из 10',
  refused_short: 'лимит ящика выбран',
};

function open(senders: StageBox[]) {
  renderWith(<MailingBoxes view={{ senders } as unknown as StageBoxesView} />);
}

/** Строка ящика по адресу: адрес разбит швами (`<wbr>`), и ищется он текстом ячейки. */
function rowOf(email: string): HTMLElement {
  const row = screen
    .getAllByRole('row')
    .find((one) => within(one).queryAllByRole('cell')[0]?.textContent === email);
  if (row === undefined) throw new Error(`строки ${email} нет`);
  return row;
}

/** Ячейки строки словами — с обычными пробелами: неразрывные держат число у его слова. */
function cellsOf(email: string): string[] {
  return within(rowOf(email))
    .getAllByRole('cell')
    .map((cell) => (cell.textContent ?? '').replace(/\s+/g, ' '));
}

function heads(): string[] {
  return screen.getAllByRole('columnheader').map((head) => head.textContent ?? '');
}

describe('ящики и разгон', () => {
  it('ступень, ушло сегодня, следующая ступень и пишет ли — коротко', () => {
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
      {
        ...BASE,
        id: 4,
        email: 'office@mail-d.example.test',
        refused: 'домен на паузе: mail-d.example.test — жалоба',
        refused_short: 'домен на паузе',
      },
      { ...BASE, id: 5, email: 'sales@mail-e.example.test', enabled: false },
      { ...BASE, id: 6, email: 'old@mail-f.example.test', warmup_day: 0 },
    ]);

    expect(heads()).toEqual([
      'Ящик',
      'Ступень сейчас',
      'Ушло сегодня',
      'Следующая ступень',
      'Пишет ли',
    ]);
    expect(cellsOf('hello@mail-a.example.test')).toEqual([
      'hello@mail-a.example.test',
      'разгон окончен · до 20 в день',
      '4 из 20',
      'выше нет',
      'пишет',
    ]);
    // Сниженный лимит — у счёта, а не второй строкой в «Пишет ли»: ящик пишет.
    expect(cellsOf('hello@mail-b.example.test').slice(1)).toEqual([
      'день 9 · до 5 из 20',
      '2 (лимит снижен до 5)',
      'чт 05.11 — до 15 в день',
      'пишет',
    ]);
    // «Ещё не писал» — значком, а не хвостом фразы; как идёт разгон — в «i» у заголовка.
    const fresh = within(rowOf('team@mail-c.example.test')).getAllByRole('cell')[1];
    expect(fresh).toHaveTextContent(/^день 1 · до 5 из 20ещё не писал$/);
    expect(within(fresh as HTMLElement).getByText('ещё не писал')).toHaveClass(
      'mantine-Badge-label',
    );
    expect(screen.getByRole('button', { name: 'Как идёт разгон' })).toBeInTheDocument();
    expect(cellsOf('office@mail-d.example.test')[4]).toBe('домен на паузе');
    expect(cellsOf('sales@mail-e.example.test')[4]).toBe('выключен');
    expect(cellsOf('old@mail-f.example.test')[1]).toBe('без разгона · до 20 в день');
  });

  it('отказ — значком без адреса и чисел, слова фильтра целиком — по нажатию «i»', async () => {
    open([BASE, CUT_AND_SPENT]);
    const user = userEvent.setup();

    // «11 из 10» читалось перерасходом; «лимит на сутки снижен мягкими отказами» второй строкой
    // повторяло слова фильтра — теперь одно значком, другое у счёта.
    expect(cellsOf('box@mail-g.example.test').slice(1)).toEqual([
      '11 (лимит снижен до 10)',
      'лимит ящика выбран',
    ]);
    expect(screen.queryByText(/мягкими отказами/)).toBeNull();
    expect(screen.queryByText(/11 из 10/)).toBeNull();

    await user.click(
      screen.getByRole('button', { name: 'Почему не пишет box@mail-g.example.test' }),
    );

    const shown = await screen.findByRole('dialog', {
      name: 'Почему не пишет box@mail-g.example.test',
    });
    expect(shown).toHaveTextContent(
      'ящику снижен лимит на сутки (мягкие отказы): box@mail-g.example.test — 11 из 10',
    );
  });

  it('одинаковое во всех строках — строкой над таблицей, колонки нет', () => {
    open([
      BASE,
      { ...BASE, id: 2, email: 'hello@mail-b.example.test', sent_today: 7 },
      { ...BASE, id: 3, email: 'hello@mail-c.example.test', cut: true, allowance_today: 10 },
    ]);

    expect(heads()).toEqual(['Ящик', 'Ушло сегодня']);
    expect(
      screen.getByText(
        'Ступень у всех ящиков: разгон окончен · до 20 в день. Следующей ступени нет. Пишут все.',
      ),
    ).toBeInTheDocument();
    expect(cellsOf('hello@mail-c.example.test')).toEqual([
      'hello@mail-c.example.test',
      '4 (лимит снижен до 10)',
    ]);
  });

  it('ступень на разгоне у всех одна — над таблицей, с днём следующей; «пишет ли» разный — колонкой', () => {
    const fresh = {
      ...BASE,
      warmup_day: 1,
      warmup_finished: false,
      first_letter_at: null,
      allowance_today: 5,
      sent_today: 0,
      next_step_on: '2026-11-12',
      next_step_allowance: 10,
    };
    open([fresh, { ...fresh, id: 2, email: 'hello@mail-b.example.test', enabled: false }]);

    expect(heads()).toEqual(['Ящик', 'Ушло сегодня', 'Пишет ли']);
    expect(
      screen.getByText(
        'Ступень у всех ящиков: день 1 · до 5 из 20, ещё не писали. Следующая: чт 12.11 — до 10 в день.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Как идёт разгон' })).toBeInTheDocument();
  });

  it('один ящик — строка как есть: одинаковости в одной строке нет', () => {
    open([BASE]);

    expect(heads()).toHaveLength(5);
  });

  it('адрес рвётся по швам — после «@» и перед точкой, а не посреди слова', () => {
    open([BASE]);

    const name = within(rowOf('hello@mail-a.example.test')).getAllByRole('cell')[0];
    expect(name?.innerHTML).toContain('hello@<wbr>mail-a<wbr>.example<wbr>.test');
  });
});
