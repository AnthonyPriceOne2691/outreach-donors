/**
 * Ревью стыков (F1): общая кнопка пачки у продаж — отказ сервера на «Отправить N».
 *
 * Окно подтверждения после отказа остаётся открытым, а отказ всплывает уведомлением вне
 * окна: человек смотрит на окно, где кнопка «Отправить» по-прежнему ждёт нажатия, и жмёт
 * её снова. Отказ должен стоять там, где нажали, — в самом окне, словами сервера
 * (правило «Отказ сервера показывается целиком», `docs/UI_RULES.md`).
 *
 * Кнопка — общий код почты: тест помечен `it.fails` (как `xfail(strict=True)`); правка —
 * PR «общее» (ревью стыков R1, F1).
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { SendQueue } from './SendQueue';

const REFUSAL =
  'Очередь писем не отправлена: продажи к почте ещё не подключены — продажи выключены: SALES_ENABLED не включён';

describe('отказ пачке (F1)', () => {
  it.fails('отказ сервера — внутри окна подтверждения, где нажали', async () => {
    serve({ 'POST /api/letters/send-queue': { status: 409, body: { detail: REFUSAL } } });
    renderWith(<SendQueue stage="sales" count={2} blocked={false} onFinished={() => undefined} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 2' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 2' }));

    expect(await within(dialog).findByText(REFUSAL)).toBeInTheDocument();
  });

  it('окно после отказа открыто — проверка, на которой стоит тест выше', async () => {
    serve({ 'POST /api/letters/send-queue': { status: 409, body: { detail: REFUSAL } } });
    renderWith(<SendQueue stage="sales" count={2} blocked={false} onFinished={() => undefined} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 2' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 2' }));

    expect(await screen.findByText(REFUSAL)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
