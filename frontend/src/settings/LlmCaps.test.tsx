/**
 * Потолки модели на «Расходе» (слово Anthony 29.10.2026): общий на весь сервис и лимиты разделов
 * процентами общего, свободный остаток, потрачено сегодня и сброс — числами сервера. До первого
 * сохранения — «как в настройках сервера», окно открывается с предложенных значений; «добавить
 * из остатка» и «убавить» — шагом; «Без потолка» — с предупреждением; менять — только с правом
 * «менять потолки расхода».
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { Me } from '../api/types';
import { ADMIN, OPERATOR } from '../test/fixtures';
import { LLM_CAPS, openUsage, SPENDING } from '../test/usageFixtures';

/** Числа экрана — с неразрывным пробелом разрядов (`format.ts`); в тексте теста — обычный. */
function spaced(words: string): RegExp {
  return new RegExp(words.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s'));
}

/** Блок сохранён: общий 50 млн, продажи 20 %, агент 20 %, аутрич 30 %, свободно 30 %. */
const SAVED_BLOCK = {
  general_tokens: 50000000,
  sales_share: 20,
  agent_share: 20,
  outreach_share: 30,
};
const SAVED = {
  ...SPENDING,
  llm_caps: {
    ...LLM_CAPS,
    scheme: 'parts',
    general: 50000000,
    free_share: 30,
    free: 15000000,
    parts: [
      {
        part: 'sales',
        title: 'Продажи',
        share: 20,
        limit: 10000000,
        spent: 4000000,
        left: 6000000,
        used_percent: 40,
      },
      {
        part: 'agent',
        title: 'Агент переписки',
        share: 20,
        limit: 10000000,
        spent: 0,
        left: 10000000,
        used_percent: 0,
      },
      {
        part: 'outreach',
        title: 'Аутрич',
        share: 30,
        limit: 15000000,
        spent: 0,
        left: 15000000,
        used_percent: 0,
      },
    ],
    saved: SAVED_BLOCK,
    changes: [
      {
        id: 1,
        block: SAVED_BLOCK,
        set_by: 'потолок@team.example.test',
        set_at: '2026-10-29T09:00:00+00:00',
      },
    ],
  },
};

const NO_SPEND: Me = {
  ...ADMIN,
  permissions: ADMIN.permissions.filter((right) => right !== 'spend'),
};

async function openEditing(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Изменить потолки' }));
  return screen.findByRole('dialog', { name: 'Потолки модели' });
}

describe('потолки модели на «Расходе»', () => {
  it('до первого сохранения — как в настройках сервера, «без потолка» заметно', async () => {
    await openUsage();

    expect(
      screen.getByText(
        'Модель — общий потолок на весь сервис: без потолка — как в настройках сервера',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('без потолка — расход модели ничем не ограничен')).toBeInTheDocument();
    expect(
      screen.getByText(
        spaced('Продажи — лимит 10 000 000 токенов в сутки — как в настройках сервера'),
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Агент переписки — своего потолка нет, держит общий'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Потрачено моделью сегодня: 600 · сброс через 5 ч 12 мин (в 03:00 по Москве)',
      ),
    ).toBeInTheDocument();
  });

  it('окно — с предложенных значений; «добавить из остатка» — шагом, и уходит весь блок', async () => {
    const recorded = await openUsage(SPENDING, {
      routes: {
        'POST /api/usage/llm-caps': {
          body: {
            id: 1,
            block: SAVED_BLOCK,
            set_by: ADMIN.email,
            set_at: '2026-10-29T09:00:00+00:00',
          },
        },
      },
    });
    const user = userEvent.setup();
    const dialog = await openEditing(user);

    expect(within(dialog).getByText(/Сейчас потолки — из настроек сервера/)).toBeInTheDocument();
    expect(within(dialog).getByText(/^Свободный остаток/)).toHaveTextContent(
      spaced('Свободный остаток 30% · 15 000 000 токенов'),
    );
    await user.click(
      within(dialog).getByRole('button', { name: 'Добавить разделу «Продажи» из остатка' }),
    );
    expect(within(dialog).getByText(/^Продажи —/)).toHaveTextContent(
      spaced('Продажи — 25% · 12 500 000 токенов'),
    );
    expect(within(dialog).getByText(/^Свободный остаток/)).toHaveTextContent(
      'Свободный остаток 25%',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
      general_tokens: 50000000,
      sales_share: 25,
      agent_share: 20,
      outreach_share: 30,
    });
  });

  it('раздать больше остатка и убавить до нуля нельзя', async () => {
    await openUsage({
      ...SAVED,
      llm_caps: { ...SAVED.llm_caps, saved: { ...SAVED_BLOCK, sales_share: 50 }, free_share: 0 },
    });
    const user = userEvent.setup();
    const dialog = await openEditing(user);

    expect(
      within(dialog).getByRole('button', { name: 'Добавить разделу «Аутрич» из остатка' }),
    ).toBeDisabled();
    for (let i = 0; i < 3; i += 1) {
      await user.click(
        within(dialog).getByRole('button', { name: 'Убавить разделу «Агент переписки»' }),
      );
    }
    expect(
      within(dialog).getByRole('button', { name: 'Убавить разделу «Агент переписки»' }),
    ).toBeDisabled();
    expect(within(dialog).getByText(/^Агент переписки —/)).toHaveTextContent(
      'Агент переписки — 5%',
    );
  });

  it('«Без потолка» — с предупреждением', async () => {
    await openUsage(SAVED);
    const user = userEvent.setup();
    const dialog = await openEditing(user);

    const field = within(dialog).getByRole('textbox', { name: 'Общий потолок на весь сервис' });
    await user.click(field);
    const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
    await user.click(await list.findByRole('option', { name: 'Без потолка', hidden: true }));

    expect(
      within(dialog).getByText('Без потолка — настройка с ответственностью'),
    ).toBeInTheDocument();
  });

  it('сохранённый блок: роздано, свободный остаток и лимиты разделов', async () => {
    await openUsage(SAVED);

    expect(
      screen.getByText(
        spaced(
          'Роздано разделам 70% · свободный остаток 30% — можно раздать ещё 15 000 000 токенов',
        ),
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(spaced('Продажи — лимит 20% · 10 000 000 токенов в сутки')),
    ).toBeInTheDocument();
    expect(screen.getByText(/^Изменены: потолок@team\.example\.test/)).toBeInTheDocument();
  });

  it('без права «менять потолки расхода» — только чтение и чьё это право', async () => {
    await openUsage(SPENDING, { me: NO_SPEND });

    expect(screen.queryByRole('button', { name: 'Изменить потолки' })).not.toBeInTheDocument();
    expect(
      screen.getByText('Потолки меняет учётка с правом «менять потолки расхода»'),
    ).toBeInTheDocument();
  });

  it('у оператора права нет по роли', async () => {
    await openUsage(SPENDING, { me: OPERATOR });

    expect(screen.queryByRole('button', { name: 'Изменить потолки' })).not.toBeInTheDocument();
  });
});
