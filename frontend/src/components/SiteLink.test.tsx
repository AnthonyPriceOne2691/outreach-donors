/**
 * Имя сайта ссылкой на сайт — одна на очередь рассмотрения и резерв: в новой вкладке, без
 * передачи адреса нашего экрана, чернилами и с переносом по швам.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderWith } from '../test/render';
import { SiteLink } from './SiteLink';

describe('ссылка на сайт', () => {
  it('ведёт на сайт в новой вкладке, без адреса нашего экрана', () => {
    renderWith(<SiteLink host="quiet-blog.example" />);

    const link = screen.getByRole('link', { name: 'quiet-blog.example' });
    expect(link).toHaveAttribute('href', 'https://quiet-blog.example');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noreferrer');
    expect(link).toHaveClass('cellName');
  });

  it('отступы задаёт место, а цвет, вес и класс — общие', () => {
    renderWith(<SiteLink host="quiet-blog.example" mt="xs" className="placed" c="red" />);

    const link = screen.getByRole('link', { name: 'quiet-blog.example' });
    expect(link).toHaveStyle({ marginTop: 'var(--mantine-spacing-xs)' });
    expect(link).toHaveClass('cellName');
    expect(link).not.toHaveClass('placed');
  });
});
