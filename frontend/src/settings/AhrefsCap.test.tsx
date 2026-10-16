/**
 * Месячный кап Ahrefs на экране «Расход» (решение владельца 16.10.2026): сколько потрачено
 * из него и осталось — числами сервера, кто и когда менял — строкой, «Изменить кап» — только
 * с правом «править пороги». Поле проверяется границами сервера до нажатия: за границей
 * на сервер не уходит ничего. Сохранение перечитывает экран, отказ сервера — в окне над полем.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Me } from '../api/types';
import { formatDateTime } from '../format';
import { ADMIN } from '../test/fixtures';
import { openUsage, SPENDING } from '../test/usageFixtures';

const LAST = {
  id: 2,
  units_cap: 150_000,
  set_by: 'кап@team.example.test',
  set_at: '2026-10-16T11:05:00+00:00',
};

const CHANGED = {
  ...SPENDING,
  ahrefs_cap: 150_000,
  ahrefs_cap_left: 143_584,
  ahrefs_cap_changes: [
    LAST,
    // Учётку удалили — строка смены осталась без автора.
    { id: 1, units_cap: 120_000, set_by: null, set_at: '2026-10-15T08:00:00+00:00' },
  ],
};

/** Админ, у которого право «править пороги» снято поимённо. */
const NO_SETTINGS: Me = {
  ...ADMIN,
  permissions: ADMIN.permissions.filter((right) => right !== 'settings'),
};

/** Числа экрана — с неразрывным пробелом разрядов (`format.ts`); в тексте теста — обычный. */
function spaced(words: string): RegExp {
  return new RegExp(words.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));
}

async function openEditing(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Изменить кап' }));
  const dialog = await screen.findByRole('dialog', { name: 'Месячный кап Ahrefs' });
  return {
    dialog,
    field: within(dialog).getByLabelText('Юнитов в месяц'),
    save: within(dialog).getByRole('button', { name: 'Сохранить' }),
  };
}

describe('кап на «Расходе»', () => {
  it('потрачено из капа и осталось — числами сервера, остаток у провайдера — отдельно', async () => {
    await openUsage();

    expect(screen.getByText(/потрачено за месяц/)).toHaveTextContent(
      spaced('потрачено за месяц 6 416 из капа 100 000, осталось 93 584'),
    );
    expect(screen.getByText(spaced('у провайдера осталось 913 000'))).toBeInTheDocument();
    expect(
      screen.getByText('Кап по умолчанию сервера — на экране его ещё не меняли'),
    ).toBeInTheDocument();
  });

  it('кто и когда менял — строкой под полосой', async () => {
    await openUsage(CHANGED);

    expect(
      screen.getByText(`Кап изменён: кап@team.example.test · ${formatDateTime(LAST.set_at)}`),
    ).toBeInTheDocument();
    expect(screen.getByText(/потрачено за месяц/)).toHaveTextContent(
      spaced('из капа 150 000, осталось 143 584'),
    );
  });

  it('без права — только чтение, и сказано, у кого это право', async () => {
    await openUsage(SPENDING, { me: NO_SETTINGS });

    expect(screen.queryByRole('button', { name: 'Изменить кап' })).not.toBeInTheDocument();
    expect(screen.getByText('Кап меняет учётка с правом «править пороги»')).toBeInTheDocument();
  });
});

describe('окно «Изменить кап»', () => {
  it('открывается на действующем капе: лимит тарифа под полем, последние смены', async () => {
    await openUsage(CHANGED);
    const user = userEvent.setup();

    const { dialog, field, save } = await openEditing(user);

    expect(field).toHaveValue('150 000');
    expect(
      within(dialog).getByText(spaced('Не больше лимита тарифа — 1 000 000')),
    ).toBeInTheDocument();
    // То же число — менять нечего: кнопка заперта, и сказано почему.
    expect(save).toBeDisabled();
    expect(within(dialog).getByText('Это действующий кап — впишите другой')).toBeInTheDocument();
    expect(within(dialog).getByText('Последние смены')).toBeInTheDocument();
    expect(
      within(dialog).getByText(`— ${formatDateTime('2026-10-15T08:00:00+00:00')}`, {
        exact: false,
      }),
    ).toBeInTheDocument();
  });

  it.each([
    ['0', 'Допустимо от 1 до 1 000 000'],
    ['1000001', 'Допустимо от 1 до 1 000 000'],
    ['1.5', 'Только целое число от 1 до 1 000 000'],
    ['', 'Впишите число'],
  ])('«%s» — отказ под полем до нажатия, на сервер не уходит ничего', async (typed, words) => {
    const recorded = await openUsage();
    const user = userEvent.setup();
    const { dialog, field, save } = await openEditing(user);

    await user.clear(field);
    if (typed !== '') await user.type(field, typed);
    await user.type(field, '{Enter}');

    expect(within(dialog).getByText(spaced(words))).toBeInTheDocument();
    expect(save).toBeDisabled();
    expect(recorded.calls.filter((call) => call.method === 'POST')).toEqual([]);
  });

  it('сохранение: кап уходит числом, экран перечитывается, окно закрывается', async () => {
    let reads = 0;
    const recorded = await openUsage(SPENDING, {
      routes: {
        'GET /api/usage': () => ({ body: reads++ === 0 ? SPENDING : CHANGED }),
        'POST /api/usage/ahrefs-cap': { body: LAST },
      },
    });
    const user = userEvent.setup();
    const { field, save } = await openEditing(user);

    await user.clear(field);
    await user.type(field, '150000');
    // Разряды — пробелом по ходу набора, как у чисел на экране.
    expect(field).toHaveValue('150 000');
    await user.click(save);

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
      units_cap: 150_000,
    });
    expect(
      await screen.findByText(
        `Кап изменён: кап@team.example.test · ${formatDateTime(LAST.set_at)}`,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/потрачено за месяц/)).toHaveTextContent(
      spaced('из капа 150 000, осталось 143 584'),
    );
    expect(screen.getByText(spaced('Кап — 150 000 юнитов в месяц'))).toBeInTheDocument();
  });

  it('отказ сервера — в окне над полем, вписанное остаётся', async () => {
    await openUsage(SPENDING, {
      routes: {
        'POST /api/usage/ahrefs-cap': {
          status: 422,
          body: { detail: 'Кап 500 000 не годится: допустимо от 1 до 400 000 юнитов в месяц' },
        },
      },
    });
    const user = userEvent.setup();
    const { dialog, field, save } = await openEditing(user);

    await user.clear(field);
    await user.type(field, '500000');
    await user.click(save);

    expect(await within(dialog).findByText('Кап не сменили')).toBeInTheDocument();
    expect(within(dialog).getByText(/допустимо от 1 до 400 000/)).toBeInTheDocument();
    expect(field).toHaveValue('500 000');
  });
});
