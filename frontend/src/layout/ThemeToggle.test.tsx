/**
 * Переключатель тем: три положения и сохранение переходов.
 *
 * Отдельная проверка на `keepTransitions` — не из любви к внутренностям
 * Mantine, а потому что без неё плавная смена темы пропадает молча:
 * разметка на месте, стили на месте, а перехода нет.
 */

import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ThemeToggle } from './ThemeToggle';

function renderToggle() {
  return render(
    <MantineProvider defaultColorScheme="light">
      <ThemeToggle />
    </MantineProvider>,
  );
}

describe('переключатель тем', () => {
  it('три положения: «как в системе» — не то же самое, что светлая', () => {
    renderToggle();

    expect(screen.getByLabelText('Светлая тема')).toBeInTheDocument();
    expect(screen.getByLabelText('Как в системе')).toBeInTheDocument();
    expect(screen.getByLabelText('Тёмная тема')).toBeInTheDocument();
  });

  it('переключение меняет тему документа', async () => {
    renderToggle();
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Тёмная тема'));

    expect(document.documentElement.dataset.mantineColorScheme).toBe('dark');
  });

  it('переходы не глушатся на время переключения', async () => {
    renderToggle();
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Тёмная тема'));

    // Mantine по умолчанию вставляет в документ правило, отключающее
    // все переходы на десять миллисекунд. Оно убивает перетекание
    // полотна — ради него и стоит `keepTransitions`.
    expect(document.querySelectorAll('[data-mantine-disable-transition]')).toHaveLength(0);
  });
});
