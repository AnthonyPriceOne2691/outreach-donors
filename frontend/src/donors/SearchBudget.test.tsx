/**
 * «найти» со сметой кредитов Hunter: что спрашивает нажатие и что ставит подтверждение.
 *
 * Проверяется то, ради чего окно: хватает — поиск всем ждущим после «Найти N»; не хватает —
 * отказ с числом сайтов, на которое хватит, и поиск ровно им; свободных нет или остаток не
 * узнать — поиска нет; платная ступень выключена — поиск сразу, как до сметы; больше потолка
 * сервера нажатие не просит.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { ContactsBudget } from '../api/contacts';
import { renderWith } from '../test/render';
import { SEARCH_ALL_MAX, SearchBudget } from './SearchBudget';

const ENOUGH: ContactsBudget = {
  sites: 2001,
  paid: true,
  needed: 1001,
  free: 5500,
  affordable: 2001,
  enough: true,
  account_left: 9000,
  reserve: 2000,
  checks_hold: 1500,
  held: 0,
  asked_at: '2026-11-25T10:00:00Z',
  live: true,
  unknown: null,
};

function open(budget: ContactsBudget, pending = 2001) {
  const ask = vi.fn(() => Promise.resolve(budget));
  const onStart = vi.fn();
  renderWith(<SearchBudget pending={pending} ask={ask} busy={false} onStart={onStart} />);
  return { ask, onStart };
}

async function press() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'найти адреса всем ждущим' }));
  return user;
}

describe('«найти» со сметой кредитов', () => {
  it('хватает — спрашивает и ищет всем ждущим', async () => {
    const { ask, onStart } = open(ENOUGH);
    const user = await press();

    // Разряды — неразрывным пробелом; поиск текста схлопывает его в обычный: в шаблоне — `\s`.
    expect(await screen.findByText(/Найти адреса 2\s001 сайтам\?/)).toBeInTheDocument();
    expect(screen.getByText(/свободно 5\s500 кредитов/)).toBeInTheDocument();
    expect(screen.getByText(/запас 2\s000, проверка адресов до конца месяца/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^Найти 2\s001$/ }));

    expect(ask).toHaveBeenCalledWith(2001);
    expect(onStart).toHaveBeenCalledWith(2001);
  });

  it('не хватает — называет, скольким сайтам хватит, и ищет ровно им', async () => {
    const { onStart } = open({
      ...ENOUGH,
      needed: 1001,
      free: 300,
      affordable: 600,
      enough: false,
    });
    const user = await press();

    expect(
      await screen.findByText(
        /^Кредитов Hunter хватит примерно на 600 сайтов из 2\s001: свободно 300\.$/,
      ),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Найти 600' }));

    expect(onStart).toHaveBeenCalledWith(600);
  });

  it('свободных нет — поиска нет, только «Понятно»', async () => {
    const { onStart } = open({ ...ENOUGH, free: 0, affordable: 0, enough: false, held: 120 });
    const user = await press();

    expect(await screen.findByText(/Свободных кредитов Hunter нет/)).toBeInTheDocument();
    expect(screen.getByText(/идущие поиски 120/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Найти/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Понятно' }));

    await waitFor(() => expect(screen.queryByText(/Свободных кредитов/)).not.toBeInTheDocument());
    expect(onStart).not.toHaveBeenCalled();
  });

  it('остаток не узнать — говорит почему и не ставит поиск', async () => {
    const said = 'остаток кредитов Hunter не узнать: сервис не ответил, а снимка учётки ещё нет';
    const { onStart } = open({ ...ENOUGH, enough: false, affordable: 0, unknown: said });
    await press();

    expect(await screen.findByText(`Поиск не поставить: ${said}.`)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Найти/ })).not.toBeInTheDocument();
    expect(onStart).not.toHaveBeenCalled();
  });

  it('Hunter не ответил — смета по снимку говорит, на какое время остаток', async () => {
    open({ ...ENOUGH, live: false });
    await press();

    expect(await screen.findByText(/Hunter не ответил — остаток на/)).toBeInTheDocument();
  });

  it('платная ступень выключена — поиск ставится сразу, без окна', async () => {
    const { onStart } = open({ ...ENOUGH, paid: false });
    await press();

    await waitFor(() => expect(onStart).toHaveBeenCalledWith(2001));
    expect(screen.queryByText(/Найти адреса/)).not.toBeInTheDocument();
  });

  it('ждущих больше потолка сервера — смету и поиск просит на потолок', async () => {
    const { ask } = open({ ...ENOUGH, sites: SEARCH_ALL_MAX }, 12_345);
    await press();

    await waitFor(() => expect(ask).toHaveBeenCalledWith(SEARCH_ALL_MAX));
  });
});
