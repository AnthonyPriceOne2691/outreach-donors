/**
 * Hunter на «Расходе» (слово Anthony 02.11.2026): крупно — общий остаток учётки, рядом — на сколько
 * проверок хватит, сброс и когда Hunter ответил; ниже наш потолок месяца, и две границы названы
 * словами. Нет снимка, ключа или права — экран говорит словами. Окно: пусто не принимается, ноль —
 * «проверки остановлены», границы — сервера, новое значение — со следующего прохода. Числа — выдуманные.
 * Тариф, выбрано за месяц и кто менял — под «Подробнее»; месяц у предела раскрывает их сразу.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Me } from '../api/types';
import { formatDateTime } from '../format';
import { ADMIN, OPERATOR } from '../test/fixtures';
import { HUNTER, openDetails, openUsage, SPENDING } from '../test/usageFixtures';

/** Числа экрана — с неразрывным пробелом разрядов (`format.ts`); в тексте теста — обычный. */
function spaced(words: string): RegExp {
  return new RegExp(words.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));
}

const ACCOUNT = HUNTER.account as Record<string, unknown>;
const MONTH = HUNTER.month as Record<string, unknown>;

function withHunter(hunter: object) {
  return { ...SPENDING, hunter: { ...HUNTER, ...hunter } };
}

const NO_SPEND: Me = {
  ...ADMIN,
  permissions: ADMIN.permissions.filter((right) => right !== 'spend'),
};

const LAST = {
  id: 4,
  verify_cap: 900,
  set_by: 'потолок@team.example.test',
  set_at: '2026-11-02T09:00:00+00:00',
};

function card() {
  return screen.getByRole('group', { name: 'Hunter' });
}

async function openEditing(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Изменить потолок' }));
  const dialog = await screen.findByRole('dialog', { name: 'Потолок проверок адреса' });
  return {
    dialog,
    field: within(dialog).getByLabelText('Проверок адреса в месяц'),
    save: within(dialog).getByRole('button', { name: 'Сохранить' }),
  };
}

describe('Hunter: учётка', () => {
  it('крупно — общий остаток кредитами, дробь не теряется, рядом — на сколько проверок хватит', async () => {
    await openUsage();

    expect(card()).toHaveTextContent(spaced('Осталось на учётке749,5из 1 000 кредитов'));
    expect(
      within(card()).getByText(spaced('Выбрано 25% · хватит на 1 499 проверок адреса')),
    ).toBeInTheDocument();
    expect(
      within(card()).getByRole('progressbar', { name: 'Выбрано от счёта учётки Hunter' }),
    ).toHaveAttribute('aria-valuenow', '25.05');
  });

  it('сброс и когда Hunter ответил: старый снимок виден датой', async () => {
    await openUsage();

    expect(
      within(card()).getByText(
        `Сброс 19 ноября · проверено ${formatDateTime(ACCOUNT.asked_at as string)}`,
      ),
    ).toBeInTheDocument();
  });

  it('тариф и что учётка общая', async () => {
    await openUsage();
    await openDetails('Hunter');

    expect(
      within(card()).getByText(
        'Тариф Team plan · учётка общая с соседней системой и поиском адресов',
      ),
    ).toBeInTheDocument();
  });

  it('без общего счёта крупно — проверки, рядом поиски', async () => {
    await openUsage(withHunter({ account: { ...ACCOUNT, credits: null } }));

    expect(card()).toHaveTextContent(spaced('Осталось на учётке1 499из 2 000 проверок адреса'));
    expect(
      within(card()).getByText(spaced('Выбрано 25% · поисков адреса осталось 749')),
    ).toBeInTheDocument();
  });

  it.each([
    ['Ключ Hunter на сервере не задан — остаток учётки спросить не у кого'],
    ['Hunter ещё не отвечал — остаток появится после следующего опроса, за несколько минут'],
  ])('снимка нет — словами сервера: %s', async (unknown) => {
    await openUsage(withHunter({ account: null, unknown }));

    expect(within(card()).getByText(unknown)).toBeInTheDocument();
    expect(within(card()).queryByText('Осталось на учётке')).not.toBeInTheDocument();
    expect(
      within(card()).queryByRole('progressbar', { name: 'Выбрано от счёта учётки Hunter' }),
    ).not.toBeInTheDocument();
  });
});

describe('Hunter: потолок проверок', () => {
  it('на виду — подвал с потолком, выбрано за месяц — под «Подробнее»', async () => {
    await openUsage();

    expect(
      within(card()).getByText(spaced('Потолок: 1 500 в месяц — пока из настроек сервера')),
    ).toBeInTheDocument();
    expect(within(card()).getByRole('button', { name: 'Подробнее' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(within(card()).queryByText(/^Выбрано с 1 октября/)).not.toBeInTheDocument();
    expect(within(card()).queryByText(/^Тариф/)).not.toBeInTheDocument();
  });

  it('месяц у предела — подробности раскрыты сразу: полоса янтарём не прячется', async () => {
    await openUsage(withHunter({ month: { ...MONTH, spent: 1300, left: 200, used_percent: 87 } }));

    expect(within(card()).getByRole('button', { name: 'Свернуть' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(
      within(card()).getByRole('progressbar', { name: 'Выбрано от потолка проверок за месяц' }),
    ).toBeInTheDocument();
  });

  it('выбрано за месяц числами сервера, и две границы названы словами', async () => {
    await openUsage();
    await openDetails('Hunter');

    expect(
      within(card()).getByText(spaced('Выбрано с 1 октября: 300 из 1 500 — 20%, осталось 1 200')),
    ).toBeInTheDocument();
    expect(
      within(card()).getByText('Потолок считается с первого числа, а учётка обнуляется 19 ноября'),
    ).toBeInTheDocument();
    expect(
      within(card()).getByRole('progressbar', { name: 'Выбрано от потолка проверок за месяц' }),
    ).toBeInTheDocument();
  });

  it('пока не сохраняли — действующий потолок и что он из настроек сервера', async () => {
    await openUsage();

    expect(
      within(card()).getByText(spaced('Потолок: 1 500 в месяц — пока из настроек сервера')),
    ).toBeInTheDocument();
    await openDetails('Hunter');
    expect(within(card()).getByText('Потолок на экране ещё не меняли')).toBeInTheDocument();
  });

  it('сохранённый — без оговорки, и видно, кто менял', async () => {
    await openUsage(withHunter({ month: { ...MONTH, source: 'setting', changes: [LAST] } }));

    expect(within(card()).getByText(spaced('Потолок: 1 500 в месяц'))).toBeInTheDocument();
    await openDetails('Hunter');
    expect(
      within(card()).getByText(
        `Потолок изменён: потолок@team.example.test · ${formatDateTime(LAST.set_at)}`,
      ),
    ).toBeInTheDocument();
  });

  it('ноль — словами, а не «0 из 0», и полосы нет', async () => {
    await openUsage(
      withHunter({
        month: { ...MONTH, cap: 0, left: 0, used_percent: null, source: 'setting' },
      }),
    );

    expect(
      within(card()).getByText('Потолок: 0 — проверки адресов остановлены'),
    ).toBeInTheDocument();
    await openDetails('Hunter');
    expect(within(card()).getByText('Выбрано с 1 октября: 300')).toBeInTheDocument();
    expect(
      within(card()).queryByRole('progressbar', { name: 'Выбрано от потолка проверок за месяц' }),
    ).not.toBeInTheDocument();
  });

  it('числа нет — слова сервера, почему', async () => {
    const problem = 'потолок проверки адресов на месяц не задан — задайте его на экране «Расход»';
    await openUsage(
      withHunter({ month: { ...MONTH, cap: null, left: null, used_percent: null, problem } }),
    );

    expect(
      within(card()).getByText(
        'Потолок проверки адресов на месяц не задан — задайте его на экране «Расход»',
      ),
    ).toBeInTheDocument();
    await openDetails('Hunter');
    expect(within(card()).getByText('Выбрано с 1 октября: 300')).toBeInTheDocument();
  });

  it('без права «Продажи» учётка остаётся, а потолка нет — и сказано почему', async () => {
    await openUsage(withHunter({ month: null }));

    expect(card()).toHaveTextContent(spaced('Осталось на учётке749,5из 1 000 кредитов'));
    expect(
      within(card()).getByText('Потолок проверок адреса виден с правом «Продажи»'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Изменить потолок' })).not.toBeInTheDocument();
  });

  it.each([
    ['администратор без права', NO_SPEND],
    ['оператор по роли', OPERATOR],
  ])('без права менять потолки (%s) — только чтение и чьё это право', async (_, me) => {
    await openUsage(SPENDING, { me });

    expect(screen.queryByRole('button', { name: 'Изменить потолок' })).not.toBeInTheDocument();
    expect(
      within(card()).getByText('Потолок меняет учётка с правом «менять потолки расхода»'),
    ).toBeInTheDocument();
  });
});

describe('Hunter: окно потолка', () => {
  it('открывается на действующем, говорит про ноль и про следующий проход', async () => {
    await openUsage();
    const { dialog, field, save } = await openEditing(userEvent.setup());

    expect(field).toHaveValue('1 500');
    expect(
      within(dialog).getByText(spaced('От 0 до 2 000. 0 — проверки адресов остановлены')),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        'Проход очистки читает потолок на старте: новое значение действует со следующего прохода.',
      ),
    ).toBeInTheDocument();
    // Из настроек сервера то же число — не «то же»: сохранение делает его настройкой экрана.
    expect(save).toBeEnabled();
  });

  it('сохранённый потолок второй раз не сохраняется', async () => {
    await openUsage(withHunter({ month: { ...MONTH, source: 'setting' } }));
    const { dialog, save } = await openEditing(userEvent.setup());

    expect(save).toBeDisabled();
    expect(
      within(dialog).getByText('Это действующий потолок — впишите другой'),
    ).toBeInTheDocument();
  });

  it('пусто не принимается: снять настройку нельзя', async () => {
    await openUsage();
    const user = userEvent.setup();
    const { dialog, field, save } = await openEditing(user);

    await user.clear(field);

    expect(within(dialog).getByText('Впишите число')).toBeInTheDocument();
    expect(save).toBeDisabled();
  });

  it('Enter в пустом поле ничего не отправляет', async () => {
    const recorded = await openUsage();
    const user = userEvent.setup();
    const { field } = await openEditing(user);

    await user.clear(field);
    await user.type(field, '{Enter}');

    expect(recorded.calls.some((call) => call.method === 'POST')).toBe(false);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('Enter на действующем потолке ничего не отправляет', async () => {
    const recorded = await openUsage(withHunter({ month: { ...MONTH, source: 'setting' } }));
    const user = userEvent.setup();
    const { field } = await openEditing(user);

    await user.type(field, '{Enter}');

    expect(recorded.calls.some((call) => call.method === 'POST')).toBe(false);
  });

  it('выше границы сервера — словами под полем, «Сохранить» заперта', async () => {
    await openUsage();
    const user = userEvent.setup();
    const { field, save } = await openEditing(user);

    await user.clear(field);
    await user.type(field, '2001');

    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(save).toBeDisabled();
  });

  it('ноль сохраняется: уходит числом, а не пустотой', async () => {
    const recorded = await openUsage(SPENDING, {
      routes: {
        'POST /api/usage/hunter-cap': {
          body: { ...LAST, verify_cap: 0, set_by: ADMIN.email },
        },
      },
    });
    const user = userEvent.setup();
    const { field, save } = await openEditing(user);

    await user.clear(field);
    await user.type(field, '0');
    await user.click(save);

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({ verify_cap: 0 });
    expect(
      await screen.findByText(/проверки адресов остановлены\. Новое значение/),
    ).toBeInTheDocument();
  });

  it('новый потолок уходит числом, а экран говорит, что поставили и когда подействует', async () => {
    const recorded = await openUsage(SPENDING, {
      routes: {
        'POST /api/usage/hunter-cap': {
          body: { ...LAST, verify_cap: 1800, set_by: ADMIN.email },
        },
      },
    });
    const user = userEvent.setup();
    const { field, save } = await openEditing(user);

    await user.clear(field);
    await user.type(field, '1800');
    await user.click(save);

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
      verify_cap: 1800,
    });
    expect(
      await screen.findByText(
        spaced(
          'Потолок проверок адреса: 1 800 проверок в месяц. Новое значение — со следующего прохода очистки.',
        ),
      ),
    ).toBeInTheDocument();
  });

  it('отказ сервера — над полем, вписанное остаётся', async () => {
    await openUsage(SPENDING, {
      routes: {
        'POST /api/usage/hunter-cap': {
          status: 422,
          body: { detail: 'Потолок выше проверок тарифа Hunter: 2 000 в цикл учётки' },
        },
      },
    });
    const user = userEvent.setup();
    const { dialog, field, save } = await openEditing(user);

    await user.clear(field);
    await user.type(field, '1800');
    await user.click(save);

    expect(await within(dialog).findByText('Потолок не сменили')).toBeInTheDocument();
    expect(within(dialog).getByText(/выше проверок тарифа/)).toBeInTheDocument();
    expect(field).toHaveValue('1 800');
  });

  it('последние смены — что ставили, кто и когда', async () => {
    await openUsage(withHunter({ month: { ...MONTH, source: 'setting', changes: [LAST] } }));
    const { dialog } = await openEditing(userEvent.setup());

    expect(within(dialog).getByText('Последние смены')).toBeInTheDocument();
    expect(within(dialog).getByText('900 проверок в месяц')).toBeInTheDocument();
  });
});
