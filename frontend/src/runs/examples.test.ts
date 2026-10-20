/**
 * Поле «Примеры сайтов» до нажатия: тот же счёт сайтов и те же слова отказа, что
 * у сервера (`keywords/examples.py`). Имена — синтетические (`.example.test`).
 */

import { describe, expect, it } from 'vitest';

import { countExamples, examplesCaption, examplesRefusal, lineProblem, siteName } from './examples';

describe('имя сайта из строки', () => {
  it.each([
    ['garden.example.test', 'garden.example.test'],
    ['HTTPS://WWW.Garden.Example.TEST/blog?x=1#top', 'garden.example.test'],
    ['garden.example.test:8080/path', 'garden.example.test'],
    ['http://user@garden.example.test/', 'garden.example.test'],
    ['сад.example.test/статьи', 'сад.example.test'],
  ])('%s → %s', (line, name) => {
    expect(siteName(line)).toBe(name);
  });
});

describe('что не адрес сайта', () => {
  it.each([
    ['garden .example.test', 'пробел внутри'],
    ['ftp://garden.example.test', 'схема «ftp»'],
    ['192.168.0.10', 'адрес IP'],
    ['garden', 'нет зоны'],
    ['https://', 'нет имени сайта'],
  ])('%s — %s', (line, problem) => {
    expect(lineProblem(line)).toContain(problem);
  });

  it('годный адрес отказа не получает', () => {
    expect(lineProblem('https://сад.example.test/blog')).toBeNull();
  });
});

describe('счёт и отказ поля', () => {
  it('повторы считаются тем же сравнением имён, что у сервера', () => {
    const text = 'garden.example.test\n\nhttps://www.garden.example.test/blog\nnews.example.test';

    expect(countExamples(text)).toEqual({ sites: 2, repeats: 1 });
    expect(examplesCaption(countExamples(text))).toBe('2 сайта из 10 · 1 повтор не в счёт');
  });

  it('негодная строка названа своим номером в поле, пустые строки — тоже строки', () => {
    expect(examplesRefusal('garden.example.test\n\nне сайт')).toBe(
      'Не адрес сайта — строка 3: «не сайт» — пробел внутри — в строке должен быть один адрес',
    );
  });

  it('больше десяти сайтов — отказ с их числом', () => {
    const text = Array.from({ length: 11 }, (_, index) => `site${index}.example.test`).join('\n');

    expect(examplesRefusal(text)).toMatch(/^Примеров 11 — берём до 10/);
  });

  it('пустое поле не отказ: кнопка просто закрыта', () => {
    expect(examplesRefusal('\n  \n')).toBeNull();
    expect(countExamples('\n  \n').sites).toBe(0);
  });
});
