/**
 * Поле числа: набранное стоит как есть, отказ — под полем, а в ряду, выровненном
 * по низу, — над полем, на месте пояснения (проверка QA 10.10.2026).
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { renderWith } from '../test/render';
import { NumberField } from './NumberField';
import { numberRefusal } from './numberText';

/** Поле с правилом «целое с единицы» — как потолок юнитов на прогоне. */
function Cap({ above = false }: { above?: boolean }) {
  const [text, setText] = useState('');
  return (
    <NumberField
      label="Потолок юнитов"
      description="Пусто — весь остаток по капу"
      refusalAbove={above}
      value={text}
      error={numberRefusal(text, { decimals: 0, min: 1 })}
      onChange={setText}
    />
  );
}

describe('поле числа', () => {
  it('набранное — как есть, отказ — под полем, пояснение на месте', async () => {
    renderWith(<Cap />);
    const field = screen.getByLabelText('Потолок юнитов');

    await userEvent.setup().type(field, '1.5');

    expect(field).toHaveValue('1.5');
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveAccessibleDescription(/Только целое число/);
    expect(field).toHaveAccessibleDescription(/Пусто — весь остаток по капу/);
    const refusal = screen.getByText('Только целое число');
    expect(refusal.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('в ряду по низу отказ встаёт на место пояснения — над полем', async () => {
    renderWith(<Cap above />);
    const field = screen.getByLabelText('Потолок юнитов');

    await userEvent.setup().type(field, '1.5');

    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveAccessibleDescription('Только целое число');
    expect(screen.queryByText('Пусто — весь остаток по капу')).not.toBeInTheDocument();
    const refusal = screen.getByText('Только целое число');
    expect(refusal.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('годное число — пояснение возвращается, отказа нет', async () => {
    renderWith(<Cap above />);
    const field = screen.getByLabelText('Потолок юнитов');

    await userEvent.setup().type(field, '5000');

    expect(field).not.toHaveAttribute('aria-invalid', 'true');
    expect(field).toHaveAccessibleDescription('Пусто — весь остаток по капу');
  });

  it('клавиатура телефона — из цифр, у денег — с запятой', () => {
    renderWith(
      <>
        <NumberField aria-label="Сколько ключей" value="" onChange={() => undefined} />
        <NumberField aria-label="Не дороже, $" decimals={2} value="" onChange={() => undefined} />
      </>,
    );

    expect(screen.getByLabelText('Сколько ключей')).toHaveAttribute('inputmode', 'numeric');
    expect(screen.getByLabelText('Не дороже, $')).toHaveAttribute('inputmode', 'decimal');
  });
});
