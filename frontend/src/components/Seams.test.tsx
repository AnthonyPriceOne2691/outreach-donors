/**
 * Швы длинного имени: домен и адрес переносятся по точкам, косым и знакам
 * запроса, а не там, где кончилось место (06.10.2026), — и остаются тем же
 * текстом: шов — место переноса, а не знак.
 */

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Seams, seamsOf } from './Seams';

describe('швы имени', () => {
  it.each([
    ['example.test', ['example', '.test']],
    [
      'gambling-news-and-analysis.example.test',
      ['gambling-news-and-analysis', '.example', '.test'],
    ],
    ['singleword', ['singleword']],
    ['Bet now', ['Bet now']],
    [
      'https://www.brand.example/promo/bonus?utm_source=x&utm_medium=y',
      [
        'https://',
        'www',
        '.brand',
        '.example/',
        'promo/',
        'bonus',
        '?utm_source',
        '=x',
        '&utm_medium',
        '=y',
      ],
    ],
  ])('%s', (text, pieces) => {
    expect(seamsOf(text)).toEqual(pieces);
  });

  it.each([
    ['qa-agent@site.example.test', ['qa-agent@', 'site', '.example', '.test']],
    ['sales@supplier.co.uk', ['sales@', 'supplier', '.co.uk']],
  ])('адрес почты рвётся после «@» и перед точками, а не посреди слова: %s', (text, pieces) => {
    // Проверка прода 10.10.2026: «Кто завёл» на «Стоп-листе» рвался «qa- / agent@…co / m».
    expect(seamsOf(text, { address: true })).toEqual(pieces);
  });

  it('без `address` «@» — не шов: имена без адреса рвутся как прежде', () => {
    expect(seamsOf('qa-agent@site.example.test')).toEqual(['qa-agent@site', '.example', '.test']);
  });

  it('«//» после схемы не рвётся: «https:/» и «/» на двух строках — не адрес', () => {
    expect(seamsOf('https://a.test')[0]).toBe('https://');
  });

  it.each([
    ['news.long-name-media.com.au', ['news', '.long-name-media', '.com.au']],
    ['site.co.uk', ['site', '.co.uk']],
    ['https://site.co.uk/page', ['https://', 'site', '.co.uk/', 'page']],
  ])('зона страны не отрывается от уровня перед ней: %s', (text, pieces) => {
    // Без склейки «.au» оставалась одна на последней строке.
    expect(seamsOf(text)).toEqual(pieces);
  });

  it('имя на экране — тот же текст: швы не добавляют знаков', () => {
    const { container } = render(
      <p>
        <Seams text="news.example.com.au" />
      </p>,
    );

    // Копируют и ищут по странице имя как было; <wbr> текста не несёт.
    expect(container.textContent).toBe('news.example.com.au');
    expect(container.querySelectorAll('wbr')).toHaveLength(2);
  });
});
