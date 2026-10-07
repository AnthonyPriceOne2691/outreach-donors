/**
 * Подпись версии разбора ответов. Сервер называет версию промпта кодом
 * (`reply-parse-v6-offers`), и до 07.10.2026 код стоял на экране диалогов
 * как есть; человеку из него нужен только номер.
 */

import { describe, expect, it } from 'vitest';

import { versionTitle } from './ParseCalibration';

describe('версия разбора словами', () => {
  it('номер версии вместо кода', () => {
    expect(versionTitle('reply-parse-v6-offers')).toBe('версия 6');
    expect(versionTitle('reply-parse-v4-named-price')).toBe('версия 4');
    expect(versionTitle('reply-parse-v12')).toBe('версия 12');
  });

  it('без номера — словами, а не кодом и не «undefined»', () => {
    // «без версии» сервер пишет разбору, сделанному до меток версий.
    expect(versionTitle('без версии')).toBe('версия без номера');
    expect(versionTitle('reply-parse-v6x')).toBe('версия без номера');
    expect(versionTitle('')).toBe('версия без номера');
  });
});
