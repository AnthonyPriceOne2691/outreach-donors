/**
 * Ключ Ahrefs и наш кап на экране «Расход» (решения владельца 16.10.2026): размер ключа и когда
 * спросили, цикл от обнуления ключа, потрачено нами и выбрано на ключе всего, осталось и доля —
 * числами сервера; наш кап — «весь лимит ключа» или «не больше N», выше ключа — действует ключ.
 * Кто и когда менял — строкой, «Изменить кап» — только с правом «править пороги». Поле «не
 * больше N» проверяется границами ключа до нажатия; ключ неизвестен — так и сказано.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Me } from '../api/types';
import { formatDateTime } from '../format';
import { ADMIN } from '../test/fixtures';
import { AHREFS_KEY, openUsage, SPENDING } from '../test/usageFixtures';

const LAST = {
  id: 2,
  units_cap: 20_000,
  set_by: 'кап@team.example.test',
  set_at: '2026-10-16T11:05:00+00:00',
};

/** «Не больше 20 000» задано, упирается наш кап. */
const LIMITED = {
  ...SPENDING,
  ahrefs_key: {
    ...AHREFS_KEY,
    cap: 20_000,
    chosen: 20_000,
    left: 13_584,
    binding: 'cap',
    used_percent: 32,
    changes: [
      LAST,
      // Учётку удалили — строка смены осталась без автора.
      { id: 1, units_cap: null, set_by: null, set_at: '2026-10-15T08:00:00+00:00' },
    ],
  },
};

/** Ahrefs ещё не ответил ни разу: размер ключа неизвестен, цикл — календарный. */
const UNKNOWN = {
  ...SPENDING,
  ahrefs_left: null,
  ahrefs_left_error: 'Не удалось узнать остаток у Ahrefs.',
  ahrefs_key: {
    ...AHREFS_KEY,
    size: null,
    own_limit: false,
    key_used: null,
    workspace_limit: null,
    workspace_used: null,
    asked_at: null,
    cycle_start: '2026-10-01T00:00:00+00:00',
    next_reset: '2026-11-01T00:00:00+00:00',
    by_key: false,
    cap: null,
    left: 0,
    binding: null,
    used_percent: null,
    unknown: 'Ahrefs ещё не ответил — размер ключа неизвестен',
    limits: null,
  },
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
  const dialog = await screen.findByRole('dialog', { name: 'Кап Ahrefs' });
  return {
    dialog,
    whole: within(dialog).getByRole('radio', { name: /Весь лимит ключа/ }),
    limit: within(dialog).getByRole('radio', { name: 'Не больше' }),
    save: within(dialog).getByRole('button', { name: 'Сохранить' }),
  };
}

describe('ключ и кап на «Расходе»', () => {
  it('ключ, цикл, наш расход и всего на ключе, осталось и доля — числами сервера', async () => {
    await openUsage();

    expect(screen.getByText(/^Ключ Ahrefs:/)).toHaveTextContent(
      spaced('Ключ Ahrefs: 50 000 юнитов за цикл · проверено'),
    );
    expect(screen.getByText('Цикл ключа: с 21 октября, обнуление 21 ноября')).toBeInTheDocument();
    expect(screen.getByText(/^Потрачено нами за цикл/)).toHaveTextContent(
      spaced(
        'Потрачено нами за цикл: 6 416 · выбрано на ключе всего 15 000 — вместе с соседней системой на общем ключе',
      ),
    );
    expect(screen.getByText(/^Осталось/)).toHaveTextContent(
      spaced('Осталось 35 000 из 50 000 — выбрано 30%, упирается в лимит ключа'),
    );
    expect(screen.getByText('Наш кап: весь лимит ключа')).toBeInTheDocument();
    expect(screen.getByText('Кап на экране ещё не меняли — весь лимит ключа')).toBeInTheDocument();
    // Плитки и статьи — за другой период, и он назван.
    expect(
      screen.getByText('Расход с 1 сентября — календарный месяц: плитки, этапы и статьи'),
    ).toBeInTheDocument();
  });

  it('ключ без своего лимита — действует лимит пространства, и сказано', async () => {
    await openUsage({
      ...SPENDING,
      ahrefs_key: { ...AHREFS_KEY, own_limit: false, size: 500_000, key_used: 1_000 },
    });

    expect(screen.getByText(/^Ключ Ahrefs:/)).toHaveTextContent(
      spaced('своего лимита нет — действует лимит пространства, 500 000 юнитов за цикл'),
    );
    expect(screen.getByText(/^Потрачено нами за цикл/)).toHaveTextContent(
      spaced('выбрано в пространстве Ahrefs всего 120 000 — всеми его ключами'),
    );
  });

  it('«не больше N» выше ключа — действует ключ, и экран это говорит', async () => {
    await openUsage({
      ...SPENDING,
      ahrefs_key: { ...AHREFS_KEY, chosen: 60_000, above_key: true },
    });

    expect(screen.getByText(/^Наш кап:/)).toHaveTextContent(
      spaced('Наш кап: не больше 60 000 — выше лимита ключа 50 000, действует 50 000'),
    );
  });

  it('без даты обнуления и без ответа Ahrefs — календарный месяц и причина словами', async () => {
    await openUsage(UNKNOWN);

    expect(screen.getByText('Ahrefs ещё не ответил — размер ключа неизвестен')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Цикл — календарный месяц, с 1 октября до 1 ноября: Ahrefs не назвал даты обнуления ключа',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Кап неизвестен, и прогоны не запустятся, пока Ahrefs не ответит/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Осталось/)).not.toBeInTheDocument();
  });

  it('кто и когда менял — строкой, «не больше N» — числом', async () => {
    await openUsage(LIMITED);

    expect(
      screen.getByText(`Кап изменён: кап@team.example.test · ${formatDateTime(LAST.set_at)}`),
    ).toBeInTheDocument();
    expect(screen.getByText(/^Наш кап:/)).toHaveTextContent(
      spaced('Наш кап: не больше 20 000 юнитов за цикл'),
    );
    expect(screen.getByText(/^Осталось/)).toHaveTextContent(
      spaced('Осталось 13 584 из 20 000 — выбрано 32%, упирается в наш кап'),
    );
  });

  it('без права — только чтение, и сказано, у кого это право', async () => {
    await openUsage(SPENDING, { me: NO_SETTINGS });

    expect(screen.queryByRole('button', { name: 'Изменить кап' })).not.toBeInTheDocument();
    expect(screen.getByText('Кап меняет учётка с правом «править пороги»')).toBeInTheDocument();
  });
});

describe('окно «Изменить кап»', () => {
  it('открывается на действующем выборе: весь лимит ключа, его размер, сохранить нечего', async () => {
    await openUsage();
    const user = userEvent.setup();

    const { dialog, whole, save } = await openEditing(user);

    expect(whole).toBeChecked();
    expect(within(dialog).getByText(spaced('Сейчас 50 000 юнитов за цикл'))).toBeInTheDocument();
    expect(save).toBeDisabled();
    expect(within(dialog).getByText('Это действующий кап — выберите другой')).toBeInTheDocument();
  });

  it('«не больше N» — границы ключа под полем, последние смены словами', async () => {
    await openUsage(LIMITED);
    const user = userEvent.setup();

    const { dialog, limit } = await openEditing(user);

    expect(limit).toBeChecked();
    expect(within(dialog).getByLabelText('Юнитов за цикл ключа')).toHaveValue('20 000');
    expect(within(dialog).getByText(spaced('От 1 до 50 000 — лимита ключа'))).toBeInTheDocument();
    expect(within(dialog).getByText('Последние смены')).toBeInTheDocument();
    expect(within(dialog).getByText('весь лимит ключа')).toBeInTheDocument();
  });

  it.each([
    ['0', 'Допустимо от 1 до 50 000'],
    ['50001', 'Допустимо от 1 до 50 000'],
    ['1.5', 'Только целое число от 1 до 50 000'],
    ['', 'Впишите число'],
  ])('«%s» — отказ под полем до нажатия, на сервер не уходит ничего', async (typed, words) => {
    const recorded = await openUsage();
    const user = userEvent.setup();
    const { dialog, limit, save } = await openEditing(user);

    await user.click(limit);
    const field = within(dialog).getByLabelText('Юнитов за цикл ключа');
    await user.clear(field);
    if (typed !== '') await user.type(field, typed);
    await user.type(field, '{Enter}');

    expect(within(dialog).getByText(spaced(words))).toBeInTheDocument();
    expect(save).toBeDisabled();
    expect(recorded.calls.filter((call) => call.method === 'POST')).toEqual([]);
  });

  it('ключ неизвестен — «не больше N» задать не от чего, и сказано почему', async () => {
    await openUsage(UNKNOWN);
    const user = userEvent.setup();

    const { dialog, limit } = await openEditing(user);

    expect(limit).toBeDisabled();
    expect(
      within(dialog).getByText(
        'Размер ключа неизвестен — Ahrefs ещё не ответил, «не больше N» задать не от чего',
      ),
    ).toBeInTheDocument();
  });

  it('сохранение «не больше N»: кап уходит числом, экран перечитывается, окно закрывается', async () => {
    let reads = 0;
    const recorded = await openUsage(SPENDING, {
      routes: {
        'GET /api/usage': () => ({ body: reads++ === 0 ? SPENDING : LIMITED }),
        'POST /api/usage/ahrefs-cap': { body: LAST },
      },
    });
    const user = userEvent.setup();
    const { dialog, limit, save } = await openEditing(user);

    await user.click(limit);
    const field = within(dialog).getByLabelText('Юнитов за цикл ключа');
    await user.type(field, '20000');
    // Разряды — пробелом по ходу набора, как у чисел на экране.
    expect(field).toHaveValue('20 000');
    await user.click(save);

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
      units_cap: 20_000,
    });
    expect(
      await screen.findByText(
        `Кап изменён: кап@team.example.test · ${formatDateTime(LAST.set_at)}`,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(spaced('Кап — не больше 20 000 юнитов за цикл ключа')),
    ).toBeInTheDocument();
  });

  it('«весь лимит ключа» уходит пустым капом', async () => {
    const recorded = await openUsage(LIMITED, {
      routes: {
        'POST /api/usage/ahrefs-cap': {
          body: { ...LAST, id: 3, units_cap: null },
        },
      },
    });
    const user = userEvent.setup();
    const { whole, save } = await openEditing(user);

    await user.click(whole);
    await user.click(save);

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
      units_cap: null,
    });
    expect(screen.getByText(/Кап — весь лимит ключа\./)).toBeInTheDocument();
  });

  it('отказ сервера — в окне над полем, вписанное остаётся', async () => {
    await openUsage(SPENDING, {
      routes: {
        'POST /api/usage/ahrefs-cap': {
          status: 422,
          // Разряды в словах сервера — неразрывным пробелом, как их пишет `ahrefs_cap.spaced`.
          body: {
            detail:
              'Кап 40\u00a0000 не годится: допустимо от 1 до 30\u00a0000 юнитов за цикл ключа',
          },
        },
      },
    });
    const user = userEvent.setup();
    const { dialog, limit, save } = await openEditing(user);

    await user.click(limit);
    const field = within(dialog).getByLabelText('Юнитов за цикл ключа');
    await user.type(field, '40000');
    await user.click(save);

    expect(await within(dialog).findByText('Кап не сменили')).toBeInTheDocument();
    expect(within(dialog).getByText(spaced('допустимо от 1 до 30 000'))).toBeInTheDocument();
    expect(field).toHaveValue('40 000');
  });
});
