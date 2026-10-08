/**
 * Ревью стыков (F1): общая кнопка пачки у продаж — отказ сервера на «Отправить N».
 *
 * Окно подтверждения после отказа остаётся открытым. Отказ всплывал уведомлением вне окна:
 * человек смотрел на окно, где кнопка «Отправить» по-прежнему ждёт нажатия, и жал её снова.
 * Отказ стоит там, где нажали, — в самом окне, словами сервера (правило «Отказ сервера
 * показывается целиком», `docs/UI_RULES.md`), и только там: второй такой же текст внизу
 * экрана читался бы вторым отказом. Ошибка прежнего нажатия гаснет, когда окно закрыли и
 * открыли снова.
 *
 * Находка к F1: окно называло всю очередь («Отправить 1 234»), а пачка берёт не больше
 * потолка сервера. Очередь длиннее пачки — окно говорит «в очереди N; одна пачка берёт до M,
 * остальное — следующей», кнопка окна — M. Потолок — число сервера (`batch_max`), не копия.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { formatNumber } from '../format';
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

describe('очередь длиннее одной пачки (находка к F1)', () => {
  const open = async (count: number, batchMax: number) => {
    renderWith(
      <SendQueue
        stage="sales"
        count={count}
        batchMax={batchMax}
        blocked={false}
        onFinished={() => undefined}
      />,
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole('button', { name: `Отправить очередь · ${formatNumber(count)}` }),
    );
    return screen.findByRole('dialog');
  };

  it('окно называет потолок сервера, кнопка — то, что уйдёт этим нажатием', async () => {
    const dialog = await open(1234, 37);

    // Число с разрядами — через пробел без разрыва, а поиск сравнивает текст, приведённый
    // к обычным пробелам: отсюда `\s`.
    expect(
      within(dialog).getByText(
        /^В очереди 1\s234 письма; одна пачка берёт до 37, остальное — следующей\. Каждое/,
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Отправить 37' })).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('button', { name: `Отправить ${formatNumber(1234)}` }),
    ).not.toBeInTheDocument();
  });

  it('очередь не длиннее пачки — слова без потолка, кнопка — вся очередь', async () => {
    const dialog = await open(37, 37);

    expect(within(dialog).getByText(/^В очереди 37 писем: каждое уйдёт/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/одна пачка/)).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Отправить 37' })).toBeInTheDocument();
  });
});
