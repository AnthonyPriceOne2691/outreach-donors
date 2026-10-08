/**
 * Ревью стыков (F1): общая кнопка пачки у продаж — отказ сервера на «Отправить N».
 *
 * Окно подтверждения после отказа остаётся открытым. Отказ всплывал уведомлением вне окна:
 * человек смотрел на окно, где кнопка «Отправить» по-прежнему ждёт нажатия, и жал её снова.
 * Отказ стоит там, где нажали, — в самом окне, словами сервера (правило «Отказ сервера
 * показывается целиком», `docs/UI_RULES.md`), и только там: второй такой же текст внизу
 * экрана читался бы вторым отказом. Ошибка прежнего нажатия гаснет, когда окно закрыли и
 * открыли снова.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { SendQueue } from './SendQueue';

const REFUSAL =
  'Очередь писем не отправлена: продажи к почте ещё не подключены — продажи выключены: SALES_ENABLED не включён';

describe('отказ пачке (F1)', () => {
  it('отказ сервера — внутри окна подтверждения, где нажали', async () => {
    serve({ 'POST /api/letters/send-queue': { status: 409, body: { detail: REFUSAL } } });
    renderWith(<SendQueue stage="sales" count={2} blocked={false} onFinished={() => undefined} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 2' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 2' }));

    expect(await within(dialog).findByText(REFUSAL)).toBeInTheDocument();
  });

  it('окно после отказа открыто, и отказ на экране один — без уведомления внизу', async () => {
    serve({ 'POST /api/letters/send-queue': { status: 409, body: { detail: REFUSAL } } });
    renderWith(<SendQueue stage="sales" count={2} blocked={false} onFinished={() => undefined} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 2' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 2' }));

    expect(await screen.findByText(REFUSAL)).toBeInTheDocument();
    expect(screen.getAllByText(REFUSAL)).toHaveLength(1);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('отказ прежнего нажатия гаснет: окно закрыли и открыли снова', async () => {
    serve({ 'POST /api/letters/send-queue': { status: 409, body: { detail: REFUSAL } } });
    renderWith(<SendQueue stage="sales" count={2} blocked={false} onFinished={() => undefined} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 2' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Отправить 2' }));
    await within(dialog).findByText(REFUSAL);
    await user.click(within(dialog).getByRole('button', { name: 'Отмена' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Отправить очередь · 2' }));

    const again = await screen.findByRole('dialog');
    expect(within(again).getByRole('button', { name: 'Отправить 2' })).toBeEnabled();
    expect(screen.queryByText(REFUSAL)).not.toBeInTheDocument();
  });
});
