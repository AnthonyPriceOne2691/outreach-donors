/**
 * Анкор найденной ссылки в колонке «Ссылка»: не башня в восемь строк,
 * а три строки по швам; целиком — в подсказке (06.10.2026).
 */

import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { theme } from '../theme';
import { LinkAnchor } from './LinkAnchor';

const LONG =
  'https://www.brand.example/promotions/welcome-bonus-2026?utm_source=partner&utm_medium=article';

function show(anchor: string) {
  render(
    <MantineProvider theme={theme}>
      <LinkAnchor anchor={anchor} />
    </MantineProvider>,
  );
  return screen.getByText(/^анкор:/);
}

describe('анкор найденной ссылки', () => {
  it('не длиннее трёх строк, а целиком — в подсказке', () => {
    const anchor = show(LONG);

    expect(anchor.style.getPropertyValue('--text-line-clamp')).toBe('3');
    expect(anchor).toHaveAttribute('title', LONG);
  });

  it('переносится в своей колонке по швам адреса, а не где кончилось место', () => {
    const anchor = show(LONG);

    expect(anchor).toHaveClass('cellName');
    expect(anchor.querySelectorAll('wbr').length).toBeGreaterThan(0);
  });

  it('подпись держится за адрес: «анкор:» одна на строке читалась отдельным пунктом', () => {
    const anchor = show('Bet now');

    expect(anchor.textContent).toBe('анкор: Bet now');
  });
});
