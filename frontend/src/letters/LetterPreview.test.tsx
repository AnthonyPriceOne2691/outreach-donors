/**
 * «Не писать» — с подтверждением у кнопки (проверка QA 10.10.2026).
 *
 * Кнопка срабатывала с первого нажатия: письмо уходило из очереди, а адресат — из всех
 * следующих сборок, хотя стоит она вплотную к «Поправить». Теперь сначала окно у кнопки
 * с последствием словами; уходит запрос только по «Не писать» в нём.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NO_STUCK_LETTERS, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';
import { LetterPreview } from './LetterPreview';

const LETTER = {
  id: 7,
  host: 'digest-weekly.example.test',
  email: 'editor@digest-weekly.example.test',
  campaign: 'Демонстрация',
  status: 'queued' as const,
  subject: 'Advertising rates for digest-weekly.example.test',
  body: 'Good afternoon to you,\n\nBest regards,\nAnna Ro',
  uniqueness: 0.19,
  verdict: null,
  followups: [],
};

const VIEW = {
  letters: [LETTER],
  followup_default: [7, 14],
  letter_default: { subject: 'Guest article on {{host}}', zones: [] },
  blocked_by: [],
  transport: { name: 'sendgrid', real: true, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: { подходящих: 1, 'с адресом': 1, 'ещё не писали': 0 },
  queued_total: 1,
  batch_max: 200,
};

/** Окно подтверждения у кнопки. В jsdom выпадающее окно Mantine остаётся `display: none`
 *  — раскладки нет, — поэтому его ищут с `hidden`; щелчок по его кнопкам настоящий.
 *  Открывается раз на тест: открытое снова, оно уже спрятано целиком (без раскладки
 *  floating-ui считает кнопку скрытой), и имя окна читается пустым. */
async function confirmation(): Promise<HTMLElement> {
  return screen.findByRole('dialog', { name: /^Не писать digest-weekly/, hidden: true });
}

/** Письмо в предпросмотре с подслушанным «не писать». */
function preview() {
  const skip = vi.fn();
  renderWith(
    <LetterPreview
      letter={LETTER}
      corridor={VIEW.corridor}
      blockedBy={[]}
      transport={VIEW.transport}
      canSend
      busy={false}
      onSend={vi.fn()}
      onSkip={skip}
      onSave={vi.fn()}
    />,
  );
  return skip;
}

describe('«Не писать»', () => {
  it('спрашивает и называет последствие; «Отмена» ничего не решает', async () => {
    const skip = preview();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не писать' }));
    const ask = await confirmation();

    expect(skip).not.toHaveBeenCalled();
    expect(ask).toHaveTextContent(
      'Не писать digest-weekly.example.test? Письмо уберём из очереди, и в следующих сборках этого адресата не будет.',
    );
    await user.click(within(ask).getByRole('button', { name: 'Отмена', hidden: true }));
    expect(skip).not.toHaveBeenCalled();
  });

  it('решение — только «Не писать» в окне', async () => {
    const skip = preview();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не писать' }));
    await user.click(
      within(await confirmation()).getByRole('button', { name: 'Не писать', hidden: true }),
    );

    expect(skip).toHaveBeenCalledTimes(1);
  });

  it('на экране писем запрос уходит только после подтверждения, и сказано, что адресат не вернётся', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/letters': { body: VIEW },
      'GET /api/runs/with-accepted': { body: [] },
      'POST /api/letters/7/skip': { body: { ...LETTER, status: 'stopped' } },
      ...NO_STUCK_LETTERS,
    });
    renderWith(<AppRoutes />, '/letters');
    const user = userEvent.setup();
    const skips = () => recorded.calls.filter((call: Call) => call.path === '/api/letters/7/skip');

    await user.click(await screen.findByRole('button', { name: 'Не писать' }));
    expect(skips()).toHaveLength(0);
    await user.click(
      within(await confirmation()).getByRole('button', { name: 'Не писать', hidden: true }),
    );

    await waitFor(() => expect(skips()).toHaveLength(1));
    expect(await screen.findByText(/в следующей сборке не появится/)).toBeInTheDocument();
  });
});
