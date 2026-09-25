/**
 * Цвет `Text`: без пропса он наследуется от родителя — и чужой переменной
 * не берёт.
 *
 * У `Text` Mantine цвет — `color: var(--text-color)` без запасного значения,
 * а переменную компонент ставит себе только при пропсе `color`. Переменная
 * сама наследуется, поэтому её определение выше по дереву перекрашивает
 * каждый `Text` без пропса. Так и вышло у Anthony 25.09.2026: расширение
 * браузера определяло её на всю страницу, и в тёмной теме «Вошли как»,
 * номера прогонов и вердикты судьи стали чёрными на тёмном стекле.
 *
 * Каскад jsdom не считает, поэтому здесь проверяется сам щит — то, что
 * `Text` ставит переменную себе. Что щит держит цвет на экране, проверено
 * в браузере с подложенной переменной на всех экранах в тёмной теме.
 */

import { Anchor, Text } from '@mantine/core';
import { screen } from '@testing-library/react';
import type { CSSProperties, ReactNode } from 'react';
import { describe, expect, it } from 'vitest';

import { renderWith } from './test/render';

/** Переменная, определённая выше по дереву, — так её ставит расширение. */
function underLeak(children: ReactNode) {
  return <div style={{ '--text-color': '#000' } as CSSProperties}>{children}</div>;
}

function textColorVar(text: string): string {
  return screen.getByText(text).style.getPropertyValue('--text-color');
}

describe('цвет текста', () => {
  it('текст без цвета не берёт чужую переменную', () => {
    renderWith(underLeak(<Text>Вошли как</Text>));
    // `currentColor` в свойстве `color` значит «как у родителя»: ровно то,
    // что Mantine задумывал, оставляя переменную неопределённой.
    expect(textColorVar('Вошли как')).toBe('currentColor');
  });

  it('цвет из пропса щит не перекрывает', () => {
    renderWith(underLeak(<Text color="red">отказ</Text>));
    expect(textColorVar('отказ')).toBe('var(--mantine-color-red-filled)');
  });

  it('приглушённый текст остаётся приглушённым', () => {
    renderWith(underLeak(<Text c="dimmed">человек не смотрел</Text>));
    expect(screen.getByText('человек не смотрел').style.color).toBe('var(--mantine-color-dimmed)');
  });

  it('ссылка на `Text` держит свой цвет, а не родительский', () => {
    // Ссылка Mantine — тот же `Text`, и щит достаётся ей тоже; цвет ссылки
    // задаёт её собственное правило, которое в CSS стоит позже правила
    // `Text`. Здесь проверяется, что щит не записал цвет в саму ссылку.
    renderWith(underLeak(<Anchor href="#">Сменить пароль</Anchor>));
    expect(screen.getByText('Сменить пароль').style.color).toBe('');
  });
});
